"""Windows ANTs affine atlas. Default plan reads JSON metadata only."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

SOURCE_SHA = '80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87'
SPLIT_SHA = 'cfcad46bf1f47d397cf7ea8b5c3a40c2a49db77767eb343c6e3a92e3e88831d4'
TOOLS = ('antsRegistration', 'antsApplyTransforms', 'AverageImages', 'AverageAffineTransform')
REPO = Path(__file__).resolve().parents[2]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def path_checked(value, exists=False):
    p = Path(value)
    require(p.is_absolute(), 'Absolute paths required')
    # Refuse junctions/symlinks, including ancestors, and Windows alternate streams.
    require(not str(p).startswith(('\\\\', '\\\\?\\')), 'UNC/device paths unsupported')
    require(':' not in str(p)[2:], 'Alternate streams unsupported')
    for q in (p, *p.parents):
        if q.exists():
            st = q.lstat()
            require(not (getattr(st, 'st_file_attributes', 0) & 0x400),
                    'Reparse points unsupported: ' + str(q))
            require(not q.is_symlink(), 'Symbolic links unsupported')
            if q.is_file():
                require(st.st_nlink == 1, 'Hardlinked inputs unsupported')
    p = p.resolve(strict=exists)
    require(not any(ch in str(p) for ch in ',[]'), 'ANTs argument delimiters in paths unsupported')
    return p


def within(a, b):
    a, b = os.path.normcase(str(a)), os.path.normcase(str(b))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def pinned(path, digest):
    p = path_checked(path, True)
    require(re.fullmatch('[0-9a-f]{64}', digest) is not None, 'Set private SHA256 pins')
    require(sha(p) == digest, 'Hash mismatch: ' + str(p))
    return p


def select_records(c, selection, full, train, val):
    """Select only the accepted pilot or the exact pinned fold0 training cohort."""
    cohort = c.get('input_cohort', 'pilot10')
    require(cohort in ('pilot10', 'fold0_train100'), 'Unsupported input cohort')
    pilot = selection['selected']
    require(selection['selection_unchanged'] is True and len(pilot) == 10 and
            len({r['case_id'] for r in pilot}) == 10 and
            len({r['patient_pseudonym'] for r in pilot}) == 10, 'Pilot identity changed')
    require(len(train) == 100 and len(val) == 28 and not train & val, 'Fold0 mismatch')
    require(all(r['case_id'] in train for r in pilot), 'Pilot not in fold0 training set')
    if cohort == 'pilot10':
        require(c.get('weighting', 'equal_check') == 'equal_check', 'Unsupported weighting')
        return pilot
    require(c.get('weighting') == 'equal_check', 'Explicit equal_check weighting required')
    require(full is not None and full['status'] == 'SERVER_READONLY_VERIFIED' and
            full['split_sha256'] == SPLIT_SHA and full['weighting'] == 'equal_check',
            'Full training manifest not verified')
    records, validation = full['selected'], full['validation_cases']
    require(len(records) == 100 and {r['case_id'] for r in records} == train,
            'Full manifest must contain all and only 100 training checks')
    require([r['case_id'] for r in records] == sorted(train), 'Training order must be sorted')
    require(len(validation) == 28 and {r['case_id'] for r in validation} == val,
            'Validation identity manifest mismatch')
    tp = {r['patient_pseudonym'] for r in records}
    vp = {r['patient_pseudonym'] for r in validation}
    require(len(tp) == 27 and len(vp) == 7 and not tp & vp, 'Patient grouping/overlap mismatch')
    by_case = {r['case_id']: r for r in records}
    for accepted in pilot:
        r = by_case[accepted['case_id']]
        require(all(r[k] == accepted[k] for k in
                    ('patient_pseudonym', 'dwi_path', 'verified_511_file_sha256',
                     'actual_ITK_header')), 'Accepted pilot identity changed in full manifest')
    return records


def prepare(args):
    cfg_path = path_checked(args.config, True)
    c = read_json(cfg_path)
    require(c['dimension'] == 3 and c['outer_iterations'] == 2 and
            c['threads'] == 2 and c['parallel_jobs'] == 1, 'Only the fixed two-round/two-thread serial budget supported')
    require(c['n4'] is False and c['sharpen'] is False and
            c['average_images_mode'] == 2 and c['extra_initial_rigid'] is False,
            'Only explicit candidate processing supported')
    require(c['scan_domain'] == 'full_acquisition_voxel_grid', 'Scan domain assumption required')
    require(c['rigid'] == {'iterations': '1000x500x250x0', 'threshold': '1e-6',
                         'window': 10, 'shrink': '6x4x2x1', 'smooth_vox': '4x2x1x0',
                         'sampling': 'Regular', 'fraction': '0.25'}, 'Rigid settings changed')
    require(c['affine'] == {'iterations': '100x100x70x20', 'threshold': '1e-9',
                          'window': 10, 'shrink': '6x4x2x1', 'smooth_vox': '3x2x1x0',
                          'sampling': 'None', 'fraction': '1'}, 'Affine settings changed')
    require(type(c['seed']) is int and c['seed'] == 20261028, 'Candidate seed changed')
    for key in ('z_pad_low', 'z_pad_high'):
        value = c.get(key, 0)
        require(type(value) is int and value >= 0,
                key + ' must be a non-negative integer')
        c[key] = value
    evidence = path_checked(args.evidence, True)
    require(evidence.is_dir(), 'Evidence directory required')
    index = pinned(evidence / 'FILE_SHA256.json', c['evidence_index_sha256'])
    hashes = read_json(index)
    names = ('stageA_acceptance.json', 'pilot_selection_v1_identity_confirmed_private.json',
             'reference_grid_ITK_candidate_private.json')
    paths = {n: pinned(evidence / n, hashes[n]) for n in names}
    a, selection, grid = (read_json(paths[n]) for n in names)
    require(a['status'] == 'COMPLETED' and a['acceptance'] == 'PASS_WITH_EXPLICIT_LIMITS'
            and a['split_sha256'] == SPLIT_SHA, 'Stage A acceptance mismatch')
    require(selection['selection_unchanged'] is True, 'Pilot selection changed')
    require(a['cohort'] == {'checks': 128, 'patient_fields': 34, 'fold0_train_checks': 100,
            'fold0_train_patient_fields': 27, 'fold0_val_checks': 28,
            'fold0_val_patient_fields': 7, 'all_five_fold_patient_overlap': 0}, 'Cohort mismatch')
    split_path = pinned(args.split, SPLIT_SHA)
    folds = read_json(split_path)
    train, val = set(folds[0]['train']), set(folds[0]['val'])
    require(len(train) == 100 and len(val) == 28 and not train & val, 'Fold0 mismatch')
    full = None
    if c.get('input_cohort', 'pilot10') == 'fold0_train100':
        name = 'fold0_train100_identity_private.json'
        paths[name] = pinned(evidence / name, hashes[name])
        full = read_json(paths[name])
    records = select_records(c, selection, full, train, val)
    count = len(records)
    source_script = pinned(c['official_script'], SOURCE_SHA)
    data_roots = [path_checked(x, True) for x in c['read_only_data_roots']]
    require(bool(data_roots) and all(p.is_dir() for p in data_roots), 'Data roots required')
    inputs = [path_checked(r['dwi_path'], True) for r in records]
    require(len({os.path.normcase(str(p)) for p in inputs}) == count, 'Duplicate input path')
    require(all(any(within(p, d) for d in data_roots) for p in inputs), 'Input outside data roots')
    require(all(p.name == r['case_id'] + '_0000.nii.gz' for p, r in zip(inputs, records)),
            'DWI identity/name mismatch')
    require(all(re.fullmatch('[0-9a-f]{64}', r['verified_511_file_sha256']) for r in records),
            'Missing accepted image hashes')
    ref = [r for r in records if r['case_id'] == grid['case_id']]
    require(len(ref) == 1 and ref[0]['patient_pseudonym'] == grid['patient_pseudonym']
            and ref[0]['actual_ITK_header'] == grid['native_ITK_header'], 'Reference mismatch')
    ants = path_checked(c['ants_root'], True)
    exes = {name: path_checked(ants / 'bin' / (name + '.exe'), True) for name in TOOLS}
    out = path_checked(args.output_root)
    require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.run_id) is not None,
            'Invalid run-id')
    require(args.run_id.upper().split('.')[0] not in
            {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
             *(f'LPT{i}' for i in range(1, 10))}, 'Reserved Windows name')
    run = path_checked(out / args.run_id)
    protected = [REPO, evidence, cfg_path.parent, split_path.parent, source_script.parent,
                 ants, *data_roots]
    for p in protected:
        require(not within(out, p) and not within(p, out), 'Output root overlaps protected path')
    require(not run.exists(), 'Run exists; choose a new run-id, never clean/reuse')
    return c, records, inputs, grid, exes, run, [cfg_path, index, *paths.values(), split_path,
                                               source_script]


def build(c, records, inputs, grid, exes, run):
    steps = []

    def internal(kind, **kw):
        steps.append({'kind': kind, **kw})

    def command(tool, argv, outputs, **meta):
        steps.append({'kind': 'command', 'tool': tool,
                      'argv': [str(exes[tool]), *map(str, argv)],
                      'outputs': list(map(str, outputs)), **meta})

    def apply(image, reference, output, transforms, interpolation='Linear', **meta):
        command('antsApplyTransforms', ['-d', 3, '--float', 1, '-e', 'scalar',
                '-i', image, '-r', reference, '-o', output, '-n', interpolation,
                '--default-value', 0, *sum((['-t', t] for t in transforms), [])],
                [output], image=str(image), reference=str(reference),
                transforms=transforms, interpolation=interpolation,
                semantics='image pull resampling; NOT a point-transform command', **meta)
        internal('check_image', path=str(output), reference=str(reference), mask=interpolation == 'NearestNeighbor')

    def register(fixed, moving, prefix):
        mat = str(prefix) + '0GenericAffine.mat'
        argv = ['-d', 3, '--float', 1, '--verbose', 1, '-u', 0, '-w', '[0.01,0.99]',
                '-z', 1, '--random-seed', c['seed'], '--interpolation', 'Linear',
                '-r', f'[{fixed},{moving},1]']
        for name in ('rigid', 'affine'):
            s = c[name]
            argv += ['-t', ('Rigid' if name == 'rigid' else 'Affine') + '[0.1]',
                     '-m', f'MI[{fixed},{moving},1,32,{s["sampling"]},{s["fraction"]}]',
                     '-c', f'[{s["iterations"]},{s["threshold"]},{s["window"]}]',
                     '-f', s['shrink'], '-s', s['smooth_vox'] + 'vox']
        argv += ['-o', str(prefix)]
        command('antsRegistration', argv, [mat], fixed=str(fixed), moving=str(moving),
                transform_semantics='A pulls fixed LPS locations to moving LPS locations; '
                'image moving->fixed uses A; inverse image fixed->moving uses [A,1]; '
                'collapsed Rigid+Affine, no displacement field')
        return mat

    def average(images, output):
        command('AverageImages', [3, output, c['average_images_mode'], *images], [output])
        internal('check_image', path=str(output), reference=str(reference), mask=False)

    reference = run / 'reference.nii.gz'
    internal('reference', output=str(reference), grid=grid,
             z_pad_low=c.get('z_pad_low', 0),
             z_pad_high=c.get('z_pad_high', 0))
    init = []
    for i, image in enumerate(inputs):
        internal('check_source', path=str(image), header=records[i]['actual_ITK_header'])
        aligned = run / f'init_{i:02d}.nii.gz'
        com = run / f'init_{i:02d}_com.mat'
        fixed_source = next(p for p, r in zip(inputs, records) if r['case_id'] == grid['case_id'])
        internal('centre', fixed=str(fixed_source), moving=str(image), output=str(com))
        apply(image, reference, aligned, [str(com)])
        init.append(aligned)
        internal('domain', source=str(image), output=str(run / f'domain_{i:02d}.nii.gz'))
    template = run / 'template_00.nii.gz'
    average(init, template)
    for iteration in range(1, 3):
        warped, mats = [], []
        for i, image in enumerate(inputs):
            prefix = run / f'iter{iteration}_{i:02d}_'
            mat = register(template, image, prefix)
            target = run / f'iter{iteration}_{i:02d}_warped.nii.gz'
            apply(image, template, target, [mat])
            warped.append(target)
            mats.append(mat)
        mean = run / f'mean_{iteration}.nii.gz'
        average(warped, mean)
        avg_mat = run / f'mean_affine_{iteration}.mat'
        command('AverageAffineTransform', [3, avg_mat, *mats], [avg_mat])
        updated = run / f'template_{iteration:02d}.nii.gz'
        apply(mean, mean, updated, [f'[{avg_mat},1]'],
              purpose='official affine-only shape update: inverse average affine; no SyN gradient')
        template = updated
    masks = []
    for i, image in enumerate(inputs):
        mat = register(template, image, run / f'final_{i:02d}_')
        apply(image, template, run / f'final_{i:02d}_dwi.nii.gz', [mat])
        apply(template, image, run / f'final_{i:02d}_atlas_native.nii.gz', [f'[{mat},1]'])
        mask = run / f'final_{i:02d}_coverage.nii.gz'
        apply(run / f'domain_{i:02d}.nii.gz', template, mask, [mat], 'NearestNeighbor')
        masks.append(str(mask))
    internal('coverage', masks=masks, reference=str(template))
    return steps


def execute(c, records, inputs, run, evidence_paths, steps):
    require(os.name == 'nt', 'Execution is Windows-only')
    require(c['execute_authorized'] is True, 'Candidate only: execution authorization required')
    if c.get('input_cohort', 'pilot10') == 'fold0_train100':
        require(c.get('geometric_qc_accepted') is True,
                'Full cohort geometric QC acceptance required before execute')
    # Lazy imports: plan never imports an image library or reads image bytes/voxels.
    import SimpleITK as sitk
    import numpy as np

    for r, p in zip(records, inputs):
        require(sha(p) == r['verified_511_file_sha256'], 'Source image hash mismatch')
    run.parent.mkdir(parents=True, exist_ok=True)
    run.mkdir(exist_ok=False)
    state = {'status': 'RUNNING', 'runtime_validation': 'NOT_RUN', 'run_id': run.name,
             'started_utc': datetime.now(timezone.utc).isoformat(), 'config': c,
             'reported_ants_version_unverified': '2.6.5-gfdce4d2',
             'python': sys.version, 'SimpleITK': sitk.Version_VersionString(),
             'numpy': np.__version__, 'inputs_sha256': {str(p): sha(p) for p in inputs},
             'evidence_sha256': {str(p): sha(p) for p in evidence_paths},
             'source_sha256': sha(Path(__file__)), 'steps': steps, 'completed_steps': 0}

    def save():
        (run / 'run.json').write_text(json.dumps(state, indent=2), encoding='utf-8')

    def image(path):
        im = sitk.ReadImage(str(path), sitk.sitkFloat32)
        arr = sitk.GetArrayViewFromImage(im)  # NumPy ZYX; ITK metadata/index XYZ, physical LPS.
        require(im.GetDimension() == 3 and im.GetNumberOfComponentsPerPixel() == 1,
                'Expected scalar 3D image')
        require(np.isfinite(arr).all(), 'Non-finite image: ' + str(path))
        return im

    def geometry(im, ref):
        require(im.GetSize() == ref.GetSize() and
                np.allclose(im.GetSpacing(), ref.GetSpacing(), atol=1e-5, rtol=0) and
                np.allclose(im.GetOrigin(), ref.GetOrigin(), atol=1e-4, rtol=0) and
                np.allclose(im.GetDirection(), ref.GetDirection(), atol=1e-6, rtol=0),
                'Output geometry mismatch')

    env = os.environ.copy()
    env['ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS'] = str(c['threads'])
    env['ANTS_RANDOM_SEED'] = str(c['seed'])
    # Child-only DLL search: bundled sibling lib/bin; never modify permanent PATH.
    env['PATH'] = os.pathsep.join([str(Path(c['ants_root']) / 'bin'),
                                 str(Path(c['ants_root']) / 'lib'), env.get('PATH', '')])
    save()
    try:
        state['exe_sha256'] = {s['argv'][0]: sha(s['argv'][0]) for s in steps if s['kind'] == 'command'}
        for n, s in enumerate(steps):
            if 'output' in s:
                require(within(Path(s['output']), run) and not Path(s['output']).exists(), 'Output conflict')
            state['active_step'] = n
            save()
            kind = s['kind']
            if kind == 'command':
                for p in s['outputs']:
                    require(within(Path(p), run) and not Path(p).exists(), 'Output conflict')
                logfile = run / f'command_{n:03d}.log'
                s['log'] = str(logfile)
                with logfile.open('x', encoding='utf-8') as log:
                    log.write(json.dumps(s['argv']) + '\n')
                    log.flush()
                    try:
                        result = subprocess.run(s['argv'], shell=False, cwd=run, env=env,
                                                stdout=log, stderr=subprocess.STDOUT, check=False)
                        s['exit_code'] = result.returncode
                    except OSError as exc:
                        s['exit_code'] = None
                        s['launch_error'] = str(exc)
                        raise
                require(result.returncode == 0, 'ANTs failed; inspect ' + str(logfile))
                require(all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in s['outputs']),
                        'Expected output missing/empty')
            elif kind == 'reference':
                g = s['grid']
                h = g['native_ITK_header']
                reduced = g['reduced_inplane_grid_candidate']
                require(reduced['shape_xyz'][:2] == [256, 256] and
                        reduced['shape_xyz'][2] == h['shape_xyz'][2] and
                        reduced['spacing_xyz'][2] == h['spacing_xyz'][2], 'Grid candidate changed')
                require(all(math.isclose((h['shape_xyz'][k]-1)*h['spacing_xyz'][k],
                                          (reduced['shape_xyz'][k]-1)*reduced['spacing_xyz'][k],
                                          abs_tol=1e-4)
                            for k in range(3)), 'Base voxel-centre extent changed')
                p_low = s.get('z_pad_low', 0)
                p_high = s.get('z_pad_high', 0)
                for key, value in (('z_pad_low', p_low), ('z_pad_high', p_high)):
                    require(type(value) is int and value >= 0,
                            key + ' must be a non-negative integer')
                shape = list(reduced['shape_xyz'])
                shape[2] += p_low + p_high
                origin = [h['origin_lps'][j]
                          - h['direction_lps'][j][2] * reduced['spacing_xyz'][2] * p_low
                          for j in range(3)]
                im = sitk.Image(shape, sitk.sitkFloat32)
                im.SetSpacing(reduced['spacing_xyz'])
                im.SetOrigin(origin)
                im.SetDirection([v for row in h['direction_lps'] for v in row])
                require(all(math.isclose((h['shape_xyz'][k]-1)*h['spacing_xyz'][k],
                                          (im.GetSize()[k]-1)*im.GetSpacing()[k], abs_tol=1e-4)
                            for k in range(2)), 'XY voxel-centre extent changed')
                require(im.GetSize()[2] == reduced['shape_xyz'][2] + p_low + p_high,
                        'Padded Z size changed')
                for q in ((0, 0, 0), (255, 255, reduced['shape_xyz'][2] - 1)):
                    old_point = [h['origin_lps'][j] + sum(
                        h['direction_lps'][j][k] * reduced['spacing_xyz'][k] * q[k]
                        for k in range(3)) for j in range(3)]
                    new_point = im.TransformIndexToPhysicalPoint((q[0], q[1], q[2] + p_low))
                    require(all(math.isclose(old_point[j], new_point[j],
                                             abs_tol=1e-4, rel_tol=0)
                                for j in range(3)), 'Original grid physical embedding changed')
                sitk.WriteImage(im, s['output'])
            elif kind == 'centre':
                fixed, moving = image(s['fixed']), image(s['moving'])
                transform = sitk.CenteredTransformInitializer(
                    fixed, moving, sitk.Euler3DTransform(),
                    sitk.CenteredTransformInitializerFilter.MOMENTS)
                sitk.WriteTransform(transform, s['output'])
            elif kind == 'check_source':
                im = image(s['path'])
                h = s['header']
                ref = sitk.Image(h['shape_xyz'], sitk.sitkFloat32)
                ref.SetSpacing(h['spacing_xyz'])
                ref.SetOrigin(h['origin_lps'])
                ref.SetDirection([v for row in h['direction_lps'] for v in row])
                geometry(im, ref)
                require(float(sitk.GetArrayViewFromImage(im).max()) >
                        float(sitk.GetArrayViewFromImage(im).min()), 'Constant DWI')
            elif kind == 'check_image':
                im, ref = image(s['path']), image(s['reference'])
                geometry(im, ref)
                arr = sitk.GetArrayViewFromImage(im)
                s['range'] = [float(arr.min()), float(arr.max())]
                if s['mask']:
                    require(np.isin(arr, [0, 1]).all(), 'Nonbinary domain mask')
                else:
                    require(s['range'][1] > s['range'][0], 'Constant output image')
                sitk.WriteImage(im, s['path'])  # Derived outputs only; enforce float32.
            elif kind == 'domain':
                src = image(s['source'])
                mask = sitk.Image(src.GetSize(), sitk.sitkFloat32) + 1.0
                mask.CopyInformation(src)
                sitk.WriteImage(mask, s['output'])
            elif kind == 'coverage':
                ref = image(s['reference'])
                total = len(s['masks'])
                require(total > 0 and total == len(records), 'Coverage/input count mismatch')
                count = np.zeros(tuple(reversed(ref.GetSize())), dtype=np.float32)
                for p in s['masks']:
                    mask = image(p)
                    geometry(mask, ref)
                    count += sitk.GetArrayViewFromImage(mask)
                for name, arr in [('coverage_count', count), ('coverage_fraction', count / total),
                                  ('coverage_common', (count == total).astype(np.float32))]:
                    im = sitk.GetImageFromArray(arr)
                    im.CopyInformation(ref)
                    sitk.WriteImage(im, str(run / (name + '.nii.gz')))
                s['common_voxels'] = int((count == total).sum())
                s['union_voxels'] = int((count > 0).sum())
                s['common_fraction_reference_grid'] = float((count == total).mean())
            else:
                raise ValueError('Unknown step')
            state['completed_steps'] = n + 1
            save()
        state['status'] = 'COMPUTED_PENDING_GEOMETRIC_QC'
        state['runtime_validation'] = 'UNACCEPTED; inspect every case'
    except BaseException as exc:
        state['status'] = 'FAILED'
        state['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('plan', 'execute'), default='plan')
    parser.add_argument('--config', required=True)
    parser.add_argument('--evidence', required=True, help='Existing private stage A acceptance directory')
    parser.add_argument('--split', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    c, records, inputs, grid, exes, run, evidence = prepare(args)
    steps = build(c, records, inputs, grid, exes, run)
    if args.mode == 'plan':
        print(json.dumps({'status': 'PLAN_ONLY', 'runtime_validation': 'NOT_RUN',
                          'run': str(run), 'resolved_config': c, 'registration_tasks': sum(s.get('tool') == 'antsRegistration' for s in steps),
                          'steps': steps}, indent=2))
    else:
        execute(c, records, inputs, run, evidence, steps)


if __name__ == '__main__':
    main()
