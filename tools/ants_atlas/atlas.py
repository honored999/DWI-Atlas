"""Windows ANTs atlas; explicit affine/SyN and bounded case concurrency. Plan reads metadata only."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import copy
import importlib.util
from datetime import datetime, timezone

SOURCE_SHA = '80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87'
SPLIT_SHA = 'cfcad46bf1f47d397cf7ea8b5c3a40c2a49db77767eb343c6e3a92e3e88831d4'
SYN_SOURCE_SHA = '80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87'
SYN_SOURCE_URL = 'https://raw.githubusercontent.com/ANTsX/ANTs/v2.6.5/Scripts/antsMultivariateTemplateConstruction2.sh'
SYN_SETTINGS = {'transform': '0.1,3,0', 'radius': 4, 'iterations': '100x100x70x20',
                'threshold': '1e-6', 'window': 10, 'shrink': '6x4x2x1',
                'smooth_vox': '3x2x1x0'}
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


def protocol(c):
    """Strict, deliberately bounded resource/method configuration (bool is not int)."""
    for key in ('threads', 'parallel_jobs'):
        require(type(c[key]) is int and c[key] > 0, key + ' must be a positive integer')
    require((c['parallel_jobs'], c['threads']) in ((1, 2), (4, 4), (4, 6)),
            'Only prepared jobs x threads budgets 1x2, 4x4, 4x6 supported')
    method = c.get('method', 'rigid_affine')
    require(method in ('rigid_affine', 'rigid_affine_syn'), 'Unsupported method')
    require(c.get('execution_mode', 'atlas') in ('atlas', 'benchmark'), 'Unsupported execution mode')
    if method == 'rigid_affine_syn':
        require(c.get('syn') == SYN_SETTINGS and type(c.get('shape_gradient')) is float and
                c['shape_gradient'] == 0.25, 'SyN candidate settings changed')
        require(c.get('z_pad_low') == 8 and c.get('z_pad_high') == 10, 'SyN requires Z8/10')
    if c.get('execution_mode') == 'benchmark':
        require(method == 'rigid_affine_syn' and c.get('input_cohort', 'pilot10') == 'pilot10',
                'Benchmark requires accepted pilot10 and SyN')
        require((c['parallel_jobs'], c['threads']) == (4, 4), 'Benchmark starts with A=4x4')
    return method


def artifact_pin(path, digest, verify_bytes):
    """Internal phase selector; never exposed as an execution configuration option."""
    p = path_checked(path, True)
    require(p.is_file() and re.fullmatch('[0-9a-f]{64}', digest) is not None,
            'Artifact file and SHA256 declaration required')
    if verify_bytes:
        pinned(p, digest)
    return p


def method_identity(c):
    """Result-affecting settings only; execution paths/counts are not method identity."""
    keys = ('dimension', 'outer_iterations', 'seed', 'scan_domain', 'extra_initial_rigid',
            'n4', 'sharpen', 'average_images_mode', 'rigid', 'affine')
    result = {k: c[k] for k in keys}
    result.update(method=c.get('method', 'rigid_affine'), weighting=c.get('weighting', 'equal_check'),
                  z_pad_low=c.get('z_pad_low', 0), z_pad_high=c.get('z_pad_high', 0))
    if result['method'] == 'rigid_affine_syn':
        result.update(syn=c['syn'], shape_gradient=c['shape_gradient'])
    return result


def receipt_json(pin):
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}, 'Expected path/SHA256 pin')
    path = pinned(pin['path'], pin['sha256'])
    return path, read_json(path)


def required_acceptance_outputs(count):
    return ['template_02.nii.gz', *[f'final_{i:02d}_{suffix}.nii.gz'
            for i in range(count) for suffix in ('dwi', 'atlas_native', 'coverage')]]


def validate_run_receipt(provenance, expected_records, *, artifact_bytes=True):
    """Bind trusted manifest, computed run and independent acceptance; never hash source images."""
    run_path, state = receipt_json(provenance['run'])
    _, acceptance = receipt_json(provenance['geometry_acceptance'])
    manifest_path, manifest = receipt_json(provenance['input_manifest'])
    require(provenance.get('status') == 'PASS' and provenance.get('gt_used') is False and
            provenance.get('split_sha256') == SPLIT_SHA, 'Receipt acceptance/split/GT mismatch')
    records = manifest['selected']
    # Compare ordered complete records, not a set of IDs; expected_records come from pinned evidence.
    require(records == expected_records and len(records) == len({r['case_id'] for r in records}) and
            len(records) == len({r['dwi_path'] for r in records}), 'Input manifest identity/order mismatch')
    require(state.get('evidence_sha256', {}).get(str(manifest_path)) == provenance['input_manifest']['sha256'],
            'Build run did not pin this input manifest')
    split_evidence = state.get('evidence_sha256', {}).values()
    require(SPLIT_SHA in split_evidence, 'Build run did not pin the fixed split')
    require(state.get('inputs_sha256') == {r['dwi_path']: r['verified_511_file_sha256'] for r in records},
            'Build input hashes differ from trusted manifest')
    require(state.get('status') == 'COMPUTED_PENDING_GEOMETRIC_QC' and bool(state.get('ended_utc')) and
            state.get('completed_steps', 0) > 0 and
            state['completed_steps'] == state.get('total_steps') == len(state['steps']) and
            len(state.get('tasks', {})) == len(state['steps']) and
            all(t.get('status') == 'SUCCEEDED' for t in state['tasks'].values()),
            'Build run is incomplete or failed')
    config = state['config']
    protocol(config)
    require(config.get('execution_mode', 'atlas') == 'atlas', 'Benchmark is not an atlas build')
    require(provenance.get('method_config') == method_identity(config), 'Recorded method provenance mismatch')
    require(acceptance.get('schema') == 'atlas-geometric-acceptance/v1' and
            acceptance.get('status') == 'PASS' and acceptance.get('independent_review') is True and
            isinstance(acceptance.get('reviewer'), str) and bool(acceptance['reviewer'].strip()) and
            acceptance.get('run_sha256') == provenance['run']['sha256'],
            'Independent geometric acceptance missing or for a different run')
    outputs = acceptance.get('outputs_sha256', {})
    recorded = {}
    for step in state['steps']:
        recorded.update(step.get('output_sha256', {}))
    required = {str(run_path.parent / name) for name in required_acceptance_outputs(len(records))}
    require(required <= outputs.keys(), 'Acceptance missing template or necessary per-case outputs')
    for path, digest in outputs.items():
        require(within(path_checked(path), run_path.parent) and recorded.get(path) == digest,
                'Accepted output was not recorded by build run')
        artifact_pin(path, digest, artifact_bytes)
    return state, outputs


def validate_legacy_template(provenance, template, expected_records, *, artifact_bytes=True):
    """Post-hoc independent acceptance of serial affine templates ONLY."""
    require(provenance.get('schema') == 'legacy-affine-template/v1', 'Legacy receipt schema required')
    run_path, state = receipt_json(provenance['run'])
    receipt_path = path_checked(provenance['receipt_path'], True)
    require(not within(receipt_path, REPO) and not within(receipt_path, run_path.parent),
            'Legacy acceptance receipt must be independent of repository and old run')
    require(read_json(receipt_path) == provenance, 'Legacy receipt contents differ from pinned receipt')
    require(provenance.get('hash_collection') == 'post_hoc' and
            isinstance(provenance.get('collected_utc'), str), 'Explicit post-hoc collection time required')
    collected = datetime.fromisoformat(provenance['collected_utc'].replace('Z', '+00:00'))
    require(collected.tzinfo is not None, 'Collection timestamp needs timezone')
    require(provenance.get('status') == 'PASS' and provenance.get('gt_used') is False and
            provenance.get('split_sha256') == SPLIT_SHA, 'Legacy acceptance/split/GT mismatch')
    manifest_path, manifest = receipt_json(provenance['input_manifest'])
    require(manifest['selected'] == expected_records, 'Legacy input manifest identity/order mismatch')
    records = expected_records
    require(records and len({r['case_id'] for r in records}) == len(records) and
            len({r['dwi_path'] for r in records}) == len(records), 'Legacy duplicate inputs')
    config_path, config = receipt_json(provenance['original_config'])
    require(state['evidence_sha256'].get(str(config_path)) == provenance['original_config']['sha256'],
            'Original config was not pinned by old run')
    resolved = copy.deepcopy(config)
    resolved.setdefault('z_pad_low', 0)
    resolved.setdefault('z_pad_high', 0)
    require(resolved == state['config'], 'Original config differs from old run')
    require(state['evidence_sha256'].get(str(manifest_path)) == provenance['input_manifest']['sha256'] and
            SPLIT_SHA in state['evidence_sha256'].values(), 'Legacy manifest/split historical pins missing')
    _, fixed_split = receipt_json(provenance['fixed_split'])
    require(provenance['fixed_split']['sha256'] == SPLIT_SHA, 'Legacy fixed split hash mismatch')
    require({r['case_id'] for r in records} <= set(fixed_split[0]['train']), 'Legacy inputs outside training split')
    require(state.get('inputs_sha256') == {r['dwi_path']: r['verified_511_file_sha256'] for r in records},
            'Legacy source hashes changed')
    protocol(config)
    require(config.get('method', 'rigid_affine') == 'rigid_affine' and
            config.get('execution_mode', 'atlas') == 'atlas' and config['parallel_jobs'] == 1 and
            provenance.get('method_config') == method_identity(config), 'Only historical serial rigid_affine templates allowed')
    require('tasks' not in state and 'total_steps' not in state,
            'New-format runs must use strict acceptance')
    steps = state.get('steps', [])
    require(any(step.get('kind') == 'command' for step in steps), 'Legacy command evidence missing')
    require(state.get('status') == 'COMPUTED_PENDING_GEOMETRIC_QC' and steps and
            type(state.get('completed_steps')) is int and state['completed_steps'] == len(steps),
            'Legacy build incomplete or failed')
    _, integrity = receipt_json(provenance['integrity_acceptance'])
    _, geometry_report = receipt_json(provenance['geometry_acceptance'])
    for report, schema in ((integrity, 'legacy-affine-integrity/v1'),
                           (geometry_report, 'atlas-geometric-acceptance/v1')):
        require(report.get('schema') == schema and report.get('status') == 'PASS' and
                report.get('independent_review') is True and bool(report.get('reviewer', '').strip()) and
                report.get('run_sha256') == provenance['run']['sha256'],
                'Independent integrity/geometric acceptance missing or wrong run')
    code_path = pinned(
        provenance['build_code']['path'], provenance['build_code']['sha256'])
    require(sha(code_path) == state.get('source_sha256'), 'Historical build code identity mismatch')
    tools = provenance.get('build_tools', {})
    require(tools and {name: pin['sha256'] for name, pin in tools.items()} == state.get('exe_sha256'),
            'Historical tool identity mismatch')
    for name, pin in tools.items():
        require(str(path_checked(pin['path'], True)) == name, 'Historical tool path mismatch')
        pinned(pin['path'], pin['sha256'])
    outputs, logs = integrity.get('outputs_sha256', {}), integrity.get('logs_sha256', {})
    require(integrity.get('checked_steps') == list(range(len(steps))), 'Integrity report must check every old step')
    required = {str(run_path.parent / name) for name in required_acceptance_outputs(len(records))}
    expected = set(required)
    for i, step in enumerate(steps):
        expected.update(step.get('outputs', []))
        if 'output' in step:
            expected.add(step['output'])
        if step.get('kind') == 'command':
            require(type(step.get('exit_code')) is int and step['exit_code'] == 0,
                    'Legacy command failed or exit code missing: ' + str(i))
            logfile = str(run_path.parent / ('command_%03d.log' % i))
            require(within(path_checked(logfile), run_path.parent), 'Legacy log outside run')
            require(step.get('log') == logfile and logfile in logs, 'Legacy command log evidence missing: ' + str(i))
            logpath = pinned(logfile, logs[logfile])
            require(json.loads(logpath.read_text(encoding='utf-8').splitlines()[0]) == step['argv'],
                    'Legacy log command mismatch')
            require(step['argv'][0] in tools, 'Legacy command tool not identified')
    require(expected <= outputs.keys(), 'Legacy expected output evidence missing')
    require(geometry_report.get('outputs_sha256') == outputs, 'Geometry/integrity output identity mismatch')
    for path, digest in outputs.items():
        require(within(path_checked(path), run_path.parent), 'Legacy output outside run')
        artifact_pin(path, digest, artifact_bytes)
    for step in steps:
        for path, digest in step.get('output_sha256', {}).items():
            require(outputs.get(path) == digest, 'Existing historical output hash mismatch')
    require(Path(template).name == 'template_02.nii.gz' and
            outputs.get(str(template)) == provenance.get('template_sha256'),
            'Legacy template changed')
    artifact_pin(template, provenance['template_sha256'], artifact_bytes)
    return state


def validate_template_receipt(provenance, template, expected_records, *, artifact_bytes=True):
    if provenance.get('schema') == 'legacy-affine-template/v1':
        return validate_legacy_template(provenance, template, expected_records, artifact_bytes=artifact_bytes)
    require(provenance.get('schema') == 'benchmark-template/v1', 'Versioned template receipt required')
    state, outputs = validate_run_receipt(provenance, expected_records, artifact_bytes=artifact_bytes)
    require(outputs.get(str(template)) == provenance.get('template_sha256'),
            'Benchmark template output identity mismatch')
    artifact_pin(template, provenance['template_sha256'], artifact_bytes)
    require(Path(template).name == 'template_02.nii.gz', 'Expected frozen final training template')
    return state


def validate_pilot_qc(accepted, c, pilot_records, tools, deployment, trusted_evidence):
    require(accepted.get('schema') == 'syn-pilot-qc/v1', 'Versioned SyN pilot QC receipt required')
    state, _ = validate_run_receipt(accepted, pilot_records)
    old = state['config']
    old_evidence = {Path(path).name: digest for path, digest in state['evidence_sha256'].items()}
    for name in ('stageA_acceptance.json', 'reference_grid_ITK_candidate_private.json',
                 'pilot_selection_v1_identity_confirmed_private.json'):
        require(name in trusted_evidence and old_evidence.get(name) == trusted_evidence[name],
                'SyN pilot trusted evidence changed: ' + name)
    require(old.get('input_cohort', 'pilot10') == 'pilot10' and len(pilot_records) == 10 and
            old.get('method') == 'rigid_affine_syn' and
            method_identity(old) == method_identity(c), 'SyN pilot method configuration mismatch')
    # No evidence supports transferring geometric acceptance across different parallel budgets yet.
    require((old['threads'], old['parallel_jobs']) == (c['threads'], c['parallel_jobs']),
            'SyN pilot threads/concurrency differ; separate acceptance required')
    require(state.get('source_sha256') == sha(Path(__file__)) and
            state.get('scheduler_sha256') == sha(Path(__file__).with_name('scheduler.py')),
            'SyN pilot code/scheduler identity mismatch')
    require(state.get('exe_sha256') == tools and
            state.get('validated_tool_versions') == deployment['tool_versions'] and
            state.get('syn_validation_receipt_sha256') == old.get('syn_validation_receipt_sha256') ==
            c['syn_validation_receipt_sha256'] and
            c['syn_validation_receipt_sha256'] in state['evidence_sha256'].values() and
            state.get('official_source', {}).get('raw_sha256') == SYN_SOURCE_SHA,
            'SyN pilot tool/version/deployment identity mismatch')
    return state


def prepare(args):
    cfg_path = path_checked(args.config, True)
    c = read_json(cfg_path)
    method = protocol(c)
    require(type(c['dimension']) is int and c['dimension'] == 3 and
            type(c['outer_iterations']) is int and c['outer_iterations'] == 2,
            'Only fixed 3D/two-round protocol supported')
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
    source_script = pinned(c['syn_official_script'] if method == 'rigid_affine_syn' else c['official_script'],
                           SYN_SOURCE_SHA if method == 'rigid_affine_syn' else SOURCE_SHA)
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
    tool_names = TOOLS + (('MultiplyImages',) if method == 'rigid_affine_syn' else ())
    exes = {name: path_checked(ants / 'bin' / (name + '.exe'), True) for name in tool_names}
    benchmark_template = None
    template_receipt = None
    if c.get('execution_mode') == 'benchmark':
        benchmark_template = artifact_pin(c['benchmark_template'], c['benchmark_template_sha256'], False)
        template_receipt = pinned(c['benchmark_template_receipt'], c['benchmark_template_receipt_sha256'])
        provenance = read_json(template_receipt)
        _, build_state = receipt_json(provenance['run'])
        source_cohort = build_state['config'].get('input_cohort', 'pilot10')
        require(source_cohort in ('pilot10', 'fold0_train100'), 'Unsupported template source cohort')
        expected = selection['selected']
        if source_cohort == 'fold0_train100':
            name = 'fold0_train100_identity_private.json'
            paths[name] = pinned(evidence / name, hashes[name])
            full = read_json(paths[name])
            expected = select_records({'input_cohort': source_cohort, 'weighting': 'equal_check'},
                                      selection, full, train, val)
        validate_template_receipt(provenance, benchmark_template, expected, artifact_bytes=False)
        identity = hashlib.sha256(json.dumps(records[:8], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        require(c.get('benchmark_manifest_sha256') == identity, 'Pin accepted pilot first8 canonical manifest SHA256')
    out = path_checked(args.output_root)
    require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.run_id) is not None,
            'Invalid run-id')
    require(args.run_id.upper().split('.')[0] not in
            {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
             *(f'LPT{i}' for i in range(1, 10))}, 'Reserved Windows name')
    run = path_checked(out / args.run_id)
    protected = [REPO, evidence, cfg_path.parent, split_path.parent, source_script.parent,
                 ants, *data_roots]
    if method == 'rigid_affine_syn':
        protected.append(path_checked(c['syn_validation_receipt']).parent)
        if 'syn_pilot_qc_receipt' in c:
            protected.append(path_checked(c['syn_pilot_qc_receipt']).parent)
    if benchmark_template is not None:
        protected.extend([benchmark_template.parent, template_receipt.parent])
    for p in protected:
        require(not within(out, p) and not within(p, out), 'Output root overlaps protected path')
    require(not any((p / 'run.json').exists() for p in (out, *out.parents)),
            'Output root is inside an old run')
    require(not run.exists(), 'Run exists; choose a new run-id, never clean/reuse')
    evidence_paths = [cfg_path, index, *paths.values(), split_path, source_script]
    if benchmark_template is not None:
        evidence_paths.extend([benchmark_template, template_receipt])
        records, inputs = records[:8], inputs[:8]
    return c, records, inputs, grid, exes, run, evidence_paths


def build(c, records, inputs, grid, exes, run):
    steps = []
    syn = c.get('method', 'rigid_affine') == 'rigid_affine_syn'
    task = {}

    def internal(kind, **kw):
        steps.append({'kind': kind, **task, **kw})

    def command(tool, argv, outputs, **meta):
        steps.append({'kind': 'command', **task, 'tool': tool,
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
        if syn:
            v = c['syn']
            argv += ['-t', 'SyN[' + v['transform'] + ']',
                     '-m', f'CC[{fixed},{moving},1,{v["radius"]}]',
                     '-c', f'[{v["iterations"]},{v["threshold"]},{v["window"]}]',
                     '-f', v['shrink'], '-s', v['smooth_vox'] + 'vox']
        argv += ['-o', f'[{prefix},{prefix}registered.nii.gz]' if syn else str(prefix)]
        command('antsRegistration', argv, [mat, str(prefix) + '1Warp.nii.gz',
                str(prefix) + '1InverseWarp.nii.gz', str(prefix) + 'registered.nii.gz'] if syn else [mat], fixed=str(fixed), moving=str(moving),
                transform_semantics=('Rigid+Affine+SyN image pull: forward Warp,Affine; '
                                     'inverse [Affine,1],InverseWarp; not point transforms'
                                     if syn else 'A pulls fixed LPS locations to moving LPS locations; '
                                     'image moving->fixed uses A; inverse image fixed->moving uses [A,1]; '
                                     'collapsed Rigid+Affine, no displacement field'))
        if syn:
            for field in ('1Warp.nii.gz', '1InverseWarp.nii.gz'):
                internal('check_field', path=str(prefix) + field, reference=str(fixed), jacobian=True)
        return mat

    def average(images, output):
        command('AverageImages', [3, output, c['average_images_mode'], *images], [output])
        internal('check_image', path=str(output), reference=str(reference), mask=False)

    def chains(mat):
        prefix = mat[:-len('0GenericAffine.mat')]
        return ([prefix + '1Warp.nii.gz', mat],
                [f'[{mat},1]', prefix + '1InverseWarp.nii.gz']) if syn else ([mat], [f'[{mat},1]'])

    def case_task(stage, i):
        task.clear()
        task.update(task_id=f'{stage}:{i:03d}', stage=stage, case_index=i,
                    case_id=records[i]['case_id'])

    if c.get('execution_mode') == 'benchmark':
        template = Path(c['benchmark_template'])
        for phase, threads in (('benchmark_A', 4), ('benchmark_B', 6)):
            for i, image in enumerate(inputs):
                case_task(phase, i)
                task['threads'] = threads
                internal('check_source', path=str(image), header=records[i]['actual_ITK_header'])
                mat = register(template, image, run / f'{phase}_{i:02d}_')
                forward, inverse = chains(mat)
                apply(image, template, run / f'{phase}_{i:02d}_dwi.nii.gz', forward)
                apply(template, image, run / f'{phase}_{i:02d}_atlas_native.nii.gz', inverse)
                domain = run / f'{phase}_{i:02d}_domain.nii.gz'
                internal('domain', source=str(image), output=str(domain))
                apply(domain, template, run / f'{phase}_{i:02d}_coverage.nii.gz', forward, 'NearestNeighbor')
            task.clear()
            internal('benchmark_barrier', phase=phase)
        return steps

    reference = run / 'reference.nii.gz'
    internal('reference', output=str(reference), grid=grid,
             z_pad_low=c.get('z_pad_low', 0),
             z_pad_high=c.get('z_pad_high', 0))
    init = []
    for i, image in enumerate(inputs):
        case_task('initial', i)
        internal('check_source', path=str(image), header=records[i]['actual_ITK_header'])
        aligned = run / f'init_{i:02d}.nii.gz'
        com = run / f'init_{i:02d}_com.mat'
        fixed_source = next(p for p, r in zip(inputs, records) if r['case_id'] == grid['case_id'])
        internal('centre', fixed=str(fixed_source), moving=str(image), output=str(com))
        apply(image, reference, aligned, [str(com)])
        init.append(aligned)
        internal('domain', source=str(image), output=str(run / f'domain_{i:02d}.nii.gz'))
    task.clear()
    template = run / 'template_00.nii.gz'
    average(init, template)
    for iteration in range(1, 3):
        warped, mats = [], []
        for i, image in enumerate(inputs):
            case_task(f'iteration{iteration}', i)
            prefix = run / f'iter{iteration}_{i:02d}_'
            mat = register(template, image, prefix)
            target = run / f'iter{iteration}_{i:02d}_warped.nii.gz'
            apply(image, template, target, chains(mat)[0])
            warped.append(target)
            mats.append(mat)
        task.clear()
        mean = run / f'mean_{iteration}.nii.gz'
        average(warped, mean)
        avg_mat = run / f'mean_affine_{iteration}.mat'
        command('AverageAffineTransform', [3, avg_mat, *mats], [avg_mat])
        updated = run / f'template_{iteration:02d}.nii.gz'
        if syn:
            fields = [m[:-len('0GenericAffine.mat')] + '1Warp.nii.gz' for m in mats]
            avg_field = run / f'mean_warp_{iteration}.nii.gz'
            scaled = run / f'negative_warp_{iteration}.nii.gz'
            physical = run / f'physical_warp_{iteration}.nii.gz'
            command('AverageImages', [3, avg_field, 0, *fields], [avg_field])
            internal('check_field', path=str(avg_field), reference=str(mean), jacobian=False)
            command('MultiplyImages', [3, avg_field, -c['shape_gradient'], scaled], [scaled])
            internal('check_field', path=str(scaled), reference=str(mean), jacobian=False)
            command('antsApplyTransforms', ['-d', 3, '-e', 'physical-vector', '--float', 1,
                    '-i', scaled, '-o', physical, '-t', f'[{avg_mat},1]', '-r', mean,
                    '-n', 'Linear'], [physical])
            internal('check_field', path=str(physical), reference=str(mean), jacobian=False)
            apply(mean, mean, updated, [f'[{avg_mat},1]', *[str(physical)] * 4],
                  purpose='v2.6.5 shape update; four compositions, not field times four')
        else:
            apply(mean, mean, updated, [f'[{avg_mat},1]'],
                  purpose='official affine-only shape update: inverse average affine; no SyN gradient')
        template = updated
    masks = []
    for i, image in enumerate(inputs):
        case_task('final', i)
        mat = register(template, image, run / f'final_{i:02d}_')
        apply(image, template, run / f'final_{i:02d}_dwi.nii.gz', chains(mat)[0])
        apply(template, image, run / f'final_{i:02d}_atlas_native.nii.gz', chains(mat)[1])
        mask = run / f'final_{i:02d}_coverage.nii.gz'
        apply(run / f'domain_{i:02d}.nii.gz', template, mask, chains(mat)[0], 'NearestNeighbor')
        masks.append(str(mask))
    task.clear()
    internal('coverage', masks=masks, reference=str(template))
    return steps


def execute(c, records, inputs, run, evidence_paths, steps):
    protocol(c)
    require(os.name == 'nt', 'Execution is Windows-only')
    require(c['execute_authorized'] is True, 'Candidate only: execution authorization required')
    if c.get('execution_mode') == 'benchmark':
        provenance = read_json(pinned(c['benchmark_template_receipt'], c['benchmark_template_receipt_sha256']))
        _, origin = receipt_json(provenance['run'])
        name = ('fold0_train100_identity_private.json' if
                origin['config'].get('input_cohort', 'pilot10') == 'fold0_train100' else
                'pilot_selection_v1_identity_confirmed_private.json')
        indexes = [p for p in evidence_paths if Path(p).name == 'FILE_SHA256.json']
        require(len(indexes) == 1, 'Trusted evidence index required')
        index = pinned(indexes[0], c['evidence_index_sha256'])
        expected = read_json(pinned(index.parent / name, read_json(index)[name]))['selected']
        template = pinned(c['benchmark_template'], c['benchmark_template_sha256'])
        validate_template_receipt(provenance, template, expected)
    if c.get('input_cohort', 'pilot10') == 'fold0_train100':
        require(c.get('geometric_qc_accepted') is True,
                'Full cohort geometric QC acceptance required before execute')
    if c.get('method') == 'rigid_affine_syn':
        require(c.get('syn_deployment_validation_accepted') is True and
                re.fullmatch('[0-9a-f]{64}', c.get('syn_validation_receipt_sha256', '')) is not None,
                'SyN deployment geometry validation and pinned receipt required')
        receipt_path = pinned(c['syn_validation_receipt'], c['syn_validation_receipt_sha256'])
        receipt = read_json(receipt_path)
        require(receipt.get('status') == 'PASS' and
                receipt.get('official_source_sha256') == SYN_SOURCE_SHA and
                receipt.get('atlas_sha256') == sha(Path(__file__)) and
                receipt.get('scheduler_sha256') == sha(Path(__file__).with_name('scheduler.py')),
                'SyN receipt must identify this exact code/source snapshot')
        for check in ('real_windows_ants', 'noncommuting_chain', 'oblique_lps_spacing',
                      'identity_field', 'analytic_translation', 'physical_vector_shape_update',
                      'native_grid', 'binary_coverage', 'jacobian'):
            require(receipt.get(check) is True, 'Missing deployment validation: ' + check)
        error = receipt.get('interior_roundtrip_error_min_spacing')
        require(type(error) in (int, float) and math.isfinite(error) and 0 <= error < 0.25,
                'Deployment roundtrip interior error must be <0.25 minimum spacing')
        require(receipt.get('boundary_error_reported') is True, 'Boundary error must be separately reported')
        require(isinstance(receipt.get('tool_versions'), dict) and receipt['tool_versions'],
                'Record real deployment tool versions in geometry receipt')
    if c.get('method') == 'rigid_affine_syn':
        tools = {step['argv'][0]: sha(step['argv'][0]) for step in steps if step['kind'] == 'command'}
        require(all(receipt.get('exe_sha256', {}).get(name) == digest for name, digest in tools.items()),
                'Deployment tool identities differ from geometry receipt')
        evidence_paths = [*evidence_paths, receipt_path]
        if c.get('input_cohort') == 'fold0_train100':
            require(c.get('syn_pilot_geometric_qc_accepted') is True,
                    'SyN-specific pilot QC acceptance required before full cohort')
            qc = pinned(c['syn_pilot_qc_receipt'], c['syn_pilot_qc_receipt_sha256'])
            # prepare already pinned this identity manifest; recheck it without opening patient images.
            manifests = [p for p in evidence_paths
                         if Path(p).name == 'pilot_selection_v1_identity_confirmed_private.json']
            require(len(manifests) == 1, 'Trusted pilot identity manifest required')
            accepted = read_json(qc)
            indexes = [p for p in evidence_paths if Path(p).name == 'FILE_SHA256.json']
            require(len(indexes) == 1, 'Trusted evidence index required')
            hashes = read_json(pinned(indexes[0], c['evidence_index_sha256']))
            trusted = {}
            for name in ('stageA_acceptance.json', 'reference_grid_ITK_candidate_private.json',
                         'pilot_selection_v1_identity_confirmed_private.json'):
                trusted[name] = sha(pinned(Path(indexes[0]).parent / name, hashes[name]))
            validate_pilot_qc(accepted, c, read_json(manifests[0])['selected'], tools, receipt, trusted)
            evidence_paths = [*evidence_paths, qc,
                              *[Path(accepted[k]['path']) for k in
                                ('run', 'geometry_acceptance', 'input_manifest')]]
    # Lazy imports: plan never imports an image library or reads image bytes/voxels.
    import SimpleITK as sitk
    import numpy as np

    for r, p in zip(records, inputs):
        require(sha(p) == r['verified_511_file_sha256'], 'Source image hash mismatch')
    # Validate every output and the supplied plan against normalized run boundaries before creating anything.
    for step in steps:
        outputs = ([step['output']] if 'output' in step else step.get('outputs', []))
        for output in outputs:
            require(within(path_checked(output), path_checked(run)), 'Plan output outside run')
        if step.get('kind') == 'check_image':
            require(within(path_checked(step['path']), path_checked(run)), 'Derived image check outside run')
    run.parent.mkdir(parents=True, exist_ok=True)
    run.mkdir(exist_ok=False)
    state = {'status': 'RUNNING', 'runtime_validation': 'NOT_RUN', 'run_id': run.name,
             'started_utc': datetime.now(timezone.utc).isoformat(), 'config': c,
             'reported_ants_version_unverified': '2.6.5-gfdce4d2',
             'python': sys.version, 'SimpleITK': sitk.Version_VersionString(),
             'numpy': np.__version__, 'inputs_sha256': {str(p): sha(p) for p in inputs},
             'evidence_sha256': {str(p): sha(p) for p in evidence_paths},
             'source_sha256': sha(Path(__file__)), 'steps': steps, 'completed_steps': 0}

    if c.get('method') == 'rigid_affine_syn':
        state['validated_tool_versions'] = receipt['tool_versions']
        state['official_source'] = {'url': SYN_SOURCE_URL, 'raw_sha256': SYN_SOURCE_SHA}
        state['syn_validation_receipt_sha256'] = c['syn_validation_receipt_sha256']

    def save():
        temporary = run / 'run.json.tmp'
        temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
        os.replace(temporary, run / 'run.json')

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
    for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                     'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[variable] = '1'
    state['thread_policy'] = {k: env[k] for k in ('ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS',
        'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS')}
    env['ANTS_RANDOM_SEED'] = str(c['seed'])
    # Child-only DLL search: bundled sibling lib/bin; never modify permanent PATH.
    env['PATH'] = os.pathsep.join([str(Path(c['ants_root']) / 'bin'),
                                 str(Path(c['ants_root']) / 'lib'), env.get('PATH', '')])
    input_identities = {str(p): r['verified_511_file_sha256'] for p, r in zip(inputs, records)}

    def perform(n, original, emit):
        s = copy.deepcopy(original)
        for output in ([s['output']] if 'output' in s else s.get('outputs', [])):
            p = path_checked(output)
            require(within(p, run) and not p.exists(), 'Output conflict/outside run')
        task_env = env.copy()
        task_env['ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS'] = str(s.get('threads', c['threads']))
        s['input_sha256'] = {str(p): input_identities[str(p)] for p in
            (s.get('image'), s.get('moving'), s.get('source'), s.get('path'))
            if str(p) in input_identities}
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
                    proc = subprocess.Popen(s['argv'], shell=False, cwd=run, env=task_env,
                                                stdout=log, stderr=subprocess.STDOUT)
                    emit('pid', n, {'pid': proc.pid})
                    result = subprocess.CompletedProcess(s['argv'], proc.wait())
                    s['exit_code'] = result.returncode
                    emit('exit', n, {'exit_code': result.returncode, 'log': str(logfile)})
                except OSError as exc:
                    s['exit_code'] = None
                    s['launch_error'] = str(exc)
                    emit('exit', n, {'exit_code': None, 'launch_error': str(exc), 'log': str(logfile)})
                    raise
            require(result.returncode == 0, 'ANTs failed; inspect ' + str(logfile))
            require(all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in s['outputs']),
                    'Expected output missing/empty')
        elif kind == 'check_field':
            field = sitk.ReadImage(s['path'], sitk.sitkVectorFloat64)
            require(field.GetDimension() == 3 and field.GetNumberOfComponentsPerPixel() == 3,
                    'Expected physical LPS 3D displacement vector (mm)')
            geometry(field, image(s['reference']))
            arr = sitk.GetArrayViewFromImage(field)
            require(np.isfinite(arr).all(), 'Non-finite displacement')
            s['max_displacement_mm'] = float(np.linalg.norm(arr, axis=-1).max())
            if s['jacobian']:
                # d(u_LPS)/d(index_XYZ) @ inv(direction @ diag(spacing));
                # account for oblique direction, not just spacing-axis derivatives.
                derivatives = np.stack([np.gradient(arr, axis=a) for a in (2, 1, 0)], axis=-1)
                basis = np.asarray(field.GetDirection()).reshape(3, 3) @ np.diag(field.GetSpacing())
                ja = np.linalg.det(derivatives @ np.linalg.inv(basis) + np.eye(3))
                require(np.isfinite(ja).all(), 'Non-finite Jacobian')
                s['jacobian_range'] = [float(ja.min()), float(ja.max())]
                s['jacobian_nonpositive'] = int((ja <= 0).sum())
                require(s['jacobian_nonpositive'] == 0, 'Non-positive Jacobian; geometric QC failed')
        elif kind == 'benchmark_barrier':
            pass
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
        if kind == 'command':
            s['output_sha256'] = {p: sha(p) for p in s['outputs']}
        elif 'output' in s:
            s['output_sha256'] = {s['output']: sha(s['output'])}
        elif kind in ('check_image', 'check_field'):
            s['output_sha256'] = {s['path']: sha(s['path'])}
        return s

    previous_image_threads = sitk.ProcessObject.GetGlobalDefaultNumberOfThreads()
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)
    state['internal_image_threads'] = 1
    save()
    try:
        state['exe_sha256'] = {s['argv'][0]: sha(s['argv'][0]) for s in steps if s['kind'] == 'command'}
        scheduler_path = Path(__file__).with_name('scheduler.py')
        spec = importlib.util.spec_from_file_location('atlas_scheduler', scheduler_path)
        scheduler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        state['scheduler_sha256'] = sha(scheduler_path)
        scheduler.run_steps(steps, perform, state, save, c)
        state['status'] = 'COMPUTED_PENDING_GEOMETRIC_QC'
        state['runtime_validation'] = 'UNACCEPTED; inspect every case'
        if c.get('execution_mode') == 'benchmark':
            state['experiment_class'] = 'ENGINEERING_BENCHMARK_NONFORMAL'
            state['formal_aggregation_eligible'] = False
    except BaseException as exc:
        state['status'] = ('ABORTED_NONFORMAL' if c.get('execution_mode') == 'benchmark'
                           and state.get('resource_stop') else 'FAILED')
        state['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(previous_image_threads)
        state['ended_utc'] = datetime.now(timezone.utc).isoformat()
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
                          'artifact_byte_validation': 'PENDING; plan is not execution approval',
                          'run': str(run), 'resolved_config': c, 'registration_tasks': sum(s.get('tool') == 'antsRegistration' for s in steps),
                          'steps': steps}, indent=2))
    else:
        execute(c, records, inputs, run, evidence, steps)


if __name__ == '__main__':
    main()
