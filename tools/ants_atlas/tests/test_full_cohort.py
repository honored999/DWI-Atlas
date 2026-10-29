"""Synthetic metadata/coverage regression tests; no ANTs or patient data."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).resolve().parents[1] / 'atlas.py'
spec = importlib.util.spec_from_file_location('atlas_under_test', ENTRY)
atlas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atlas)


def cohorts():
    records = [{'case_id': f'case{i:03d}', 'patient_pseudonym': f'P{i % 27:02d}',
                'dwi_path': f'C:/synthetic/data/imagesTr/case{i:03d}_0000.nii.gz',
                'verified_511_file_sha256': f'{i:064x}', 'actual_ITK_header': {}}
               for i in range(100)]
    pilot = {'selection_unchanged': True, 'selected': records[:10]}
    validation = [{'case_id': f'val{i:03d}', 'patient_pseudonym': f'V{i % 7}'}
                  for i in range(28)]
    full = {'status': 'SERVER_READONLY_VERIFIED', 'split_sha256': atlas.SPLIT_SHA,
            'weighting': 'equal_check', 'selected': records,
            'validation_cases': validation}
    return pilot, full, {r['case_id'] for r in records}, {r['case_id'] for r in validation}


class CohortTests(unittest.TestCase):
    def select(self, cfg=None, mutate=None):
        pilot, full, train, val = cohorts()
        if mutate:
            mutate(pilot, full, train, val)
        return atlas.select_records(cfg or {'input_cohort': 'fold0_train100',
                                           'weighting': 'equal_check'}, pilot, full, train, val)

    def test_full_exact_training_set(self):
        records = self.select()
        self.assertEqual(len(records), 100)
        self.assertEqual(len({r['patient_pseudonym'] for r in records}), 27)

    def test_legacy_pilot_default_unchanged(self):
        pilot, full, train, val = cohorts()
        self.assertEqual(atlas.select_records({}, pilot, None, train, val), pilot['selected'])

    def test_reject_missing_training_case(self):
        with self.assertRaises(ValueError):
            self.select(mutate=lambda p, f, t, v: f['selected'].pop())

    def test_reject_validation_case_in_training(self):
        def change(p, f, t, v):
            f['selected'][-1] = dict(f['selected'][-1], case_id='val000')
        with self.assertRaises(ValueError):
            self.select(mutate=change)

    def test_reject_duplicate_case(self):
        def change(p, f, t, v):
            f['selected'][-1] = f['selected'][-2]
        with self.assertRaises(ValueError):
            self.select(mutate=change)

    def test_reject_patient_overlap(self):
        def change(p, f, t, v):
            f['validation_cases'][0]['patient_pseudonym'] = 'P00'
        with self.assertRaises(ValueError):
            self.select(mutate=change)

    def test_reject_incomplete_validation_identity(self):
        with self.assertRaises(ValueError):
            self.select(mutate=lambda p, f, t, v: f['validation_cases'].pop())

    def test_reject_changed_pilot_hash(self):
        def change(p, f, t, v):
            p['selected'][0] = dict(p['selected'][0], verified_511_file_sha256='f' * 64)
        with self.assertRaises(ValueError):
            self.select(mutate=change)

    def test_reject_unverified_full_manifest(self):
        with self.assertRaises(ValueError):
            self.select(mutate=lambda p, f, t, v: f.update(status='DRAFT'))

    def test_reject_implicit_weighting_for_full(self):
        with self.assertRaises(ValueError):
            self.select({'input_cohort': 'fold0_train100'})

    def test_reject_patient_equal_unimplemented(self):
        with self.assertRaises(ValueError):
            self.select({'input_cohort': 'fold0_train100', 'weighting': 'equal_patient'})

    def test_reject_unknown_cohort(self):
        with self.assertRaises(ValueError):
            self.select({'input_cohort': 'arbitrary'})


class PipelineTests(unittest.TestCase):
    def config(self):
        p = ENTRY.parent / 'configs/pilot.example.json'
        return json.loads(p.read_text(encoding='utf-8-sig'))

    def test_build_has_300_registrations_and_native_mapping(self):
        c = self.config()
        p, f, train, val = cohorts()
        records = f['selected']
        inputs = [Path(r['dwi_path']) for r in records]
        exes = {name: Path('C:/synthetic/ants') / (name + '.exe') for name in atlas.TOOLS}
        with patch.object(atlas.subprocess, 'run', side_effect=AssertionError('No process in plan')):
            steps = atlas.build(c, records, inputs, {'case_id': records[0]['case_id']}, exes,
                                Path('C:/synthetic/new_run'))
        registrations = [s for s in steps if s.get('tool') == 'antsRegistration']
        self.assertEqual(len(registrations), 300)
        self.assertEqual(len(steps), 1814)
        self.assertEqual(len(steps[-1]['masks']), 100)
        final = [s for s in steps if s.get('tool') == 'antsApplyTransforms'
                 and s['outputs'][0].endswith('final_99_atlas_native.nii.gz')]
        self.assertEqual(len(final), 1)
        self.assertTrue(final[0]['transforms'][0].endswith('final_99_0GenericAffine.mat,1]'))
        self.assertTrue(final[0]['reference'].endswith('case099_0000.nii.gz'))

    def test_full_execute_stays_blocked_without_geometric_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'must_not_exist'
            c = self.config()
            c.update(input_cohort='fold0_train100', execute_authorized=True,
                     geometric_qc_accepted=False)
            with self.assertRaisesRegex(ValueError, 'geometric'):
                atlas.execute(c, [], [], run, [], [])
            self.assertFalse(run.exists())

    def test_coverage_fraction_uses_100_not_10(self):
        import numpy as np
        import SimpleITK as sitk
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            masks = []
            for i in range(100):
                arr = np.ones((2, 3, 4), dtype=np.float32)
                if i >= 40:
                    arr[:, :, 0] = 0
                im = sitk.GetImageFromArray(arr)
                p = root / f'mask{i}.nii.gz'
                sitk.WriteImage(im, str(p))
                masks.append(p)
            c = self.config()
            c.update(input_cohort='fold0_train100', execute_authorized=True,
                     geometric_qc_accepted=True)
            records = [{'verified_511_file_sha256': atlas.sha(p)} for p in masks]
            steps = [{'kind': 'coverage', 'masks': list(map(str, masks)),
                      'reference': str(masks[0])}]
            run = root / 'run'
            atlas.execute(c, records, masks, run, [], steps)
            fraction = sitk.GetArrayFromImage(sitk.ReadImage(str(run/'coverage_fraction.nii.gz')))
            common = sitk.GetArrayFromImage(sitk.ReadImage(str(run/'coverage_common.nii.gz')))
            np.testing.assert_allclose(fraction[:, :, 0], .4)
            np.testing.assert_array_equal(fraction[:, :, 1:], 1)
            np.testing.assert_array_equal(common[:, :, 0], 0)
            np.testing.assert_array_equal(common[:, :, 1:], 1)
            self.assertTrue(all(atlas.sha(p) == r['verified_511_file_sha256']
                                for p, r in zip(masks, records)))


class PrepareTests(unittest.TestCase):
    def fixture(self, root, full_mode=True):
        pilot, full, train, val = cohorts()
        data, evidence, cfgdir, refdir, ants = [root / n for n in
                                               ('data', 'evidence', 'config', 'reference', 'ants')]
        for d in (data / 'imagesTr', evidence, cfgdir, refdir, ants / 'bin'):
            d.mkdir(parents=True)
        split = data / 'splits_final.json'
        split.write_text(json.dumps([{'train': sorted(train), 'val': sorted(val)}]), encoding='utf-8')
        digest = atlas.sha(split)
        for r in full['selected']:
            path = data / 'imagesTr' / (r['case_id'] + '_0000.nii.gz')
            path.write_bytes(b'Synthetic plan-only placeholder, not an image')
            r['dwi_path'] = str(path)
            r['verified_511_file_sha256'] = atlas.sha(path)
        full['split_sha256'] = digest
        script = refdir / 'official.sh'
        script.write_bytes(b'Synthetic official-source pin fixture')
        for name in atlas.TOOLS:
            (ants / 'bin' / (name + '.exe')).write_bytes(b'Not an executable')
        a = {'status': 'COMPLETED', 'acceptance': 'PASS_WITH_EXPLICIT_LIMITS',
             'split_sha256': digest,
             'cohort': {'checks': 128, 'patient_fields': 34, 'fold0_train_checks': 100,
                        'fold0_train_patient_fields': 27, 'fold0_val_checks': 28,
                        'fold0_val_patient_fields': 7, 'all_five_fold_patient_overlap': 0}}
        grid = {'case_id': pilot['selected'][0]['case_id'],
                'patient_pseudonym': pilot['selected'][0]['patient_pseudonym'],
                'native_ITK_header': {}}
        documents = {'stageA_acceptance.json': a,
                     'pilot_selection_v1_identity_confirmed_private.json': pilot,
                     'reference_grid_ITK_candidate_private.json': grid,
                     'fold0_train100_identity_private.json': full}
        hashes = {}
        for name, value in documents.items():
            path = evidence / name
            path.write_text(json.dumps(value), encoding='utf-8')
            hashes[name] = atlas.sha(path)
        index = evidence / 'FILE_SHA256.json'
        index.write_text(json.dumps(hashes), encoding='utf-8')
        c = json.loads((ENTRY.parent/'configs/pilot.example.json').read_text(encoding='utf-8-sig'))
        c.update(ants_root=str(ants), official_script=str(script),
                 read_only_data_roots=[str(data)], evidence_index_sha256=atlas.sha(index))
        if full_mode:
            c.update(input_cohort='fold0_train100', weighting='equal_check',
                     geometric_qc_accepted=False)
        cfg = cfgdir / 'plan.json'
        cfg.write_text(json.dumps(c), encoding='utf-8')
        args = argparse.Namespace(config=str(cfg), evidence=str(evidence), split=str(split),
                                  output_root=str(root/'outputs'), run_id='fresh')
        return args, digest, atlas.sha(script), documents

    def test_prepare_full_pins_without_image_or_process_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, split_sha, source_sha, _ = self.fixture(Path(tmp))
            with patch.object(atlas, 'SPLIT_SHA', split_sha), patch.object(atlas, 'SOURCE_SHA', source_sha), \
                 patch.object(atlas.subprocess, 'run', side_effect=AssertionError('No external process')):
                c, records, inputs, grid, exes, run, evidence = atlas.prepare(args)
                self.assertEqual(len(inputs), 100)
                self.assertIn('fold0_train100_identity_private.json', [p.name for p in evidence])
                self.assertFalse(run.exists())
                self.assertEqual(sum(s.get('tool') == 'antsRegistration' for s in
                                     atlas.build(c, records, inputs, grid, exes, run)), 300)

    def test_prepare_legacy_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, split_sha, source_sha, _ = self.fixture(Path(tmp), False)
            with patch.object(atlas, 'SPLIT_SHA', split_sha), patch.object(atlas, 'SOURCE_SHA', source_sha):
                c, records, inputs, grid, exes, run, evidence = atlas.prepare(args)
                self.assertEqual(len(inputs), 10)
                self.assertEqual(len(atlas.build(c, records, inputs, grid, exes, run)), 194)

    def test_full_manifest_pin_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, split_sha, source_sha, _ = self.fixture(Path(tmp))
            (Path(args.evidence)/'fold0_train100_identity_private.json').write_text('{}', encoding='utf-8')
            with patch.object(atlas, 'SPLIT_SHA', split_sha), patch.object(atlas, 'SOURCE_SHA', source_sha):
                with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
                    atlas.prepare(args)

    def test_output_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, split_sha, source_sha, _ = self.fixture(Path(tmp))
            args.output_root = str(Path(args.split).parent/'derived')
            with patch.object(atlas, 'SPLIT_SHA', split_sha), patch.object(atlas, 'SOURCE_SHA', source_sha):
                with self.assertRaisesRegex(ValueError, 'overlaps'):
                    atlas.prepare(args)

    def test_existing_run_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, split_sha, source_sha, _ = self.fixture(Path(tmp))
            (Path(args.output_root)/args.run_id).mkdir(parents=True)
            with patch.object(atlas, 'SPLIT_SHA', split_sha), patch.object(atlas, 'SOURCE_SHA', source_sha):
                with self.assertRaisesRegex(ValueError, 'Run exists'):
                    atlas.prepare(args)


if __name__ == '__main__':
    unittest.main()
