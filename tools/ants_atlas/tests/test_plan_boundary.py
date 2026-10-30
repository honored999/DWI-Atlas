"""Production prepare/execute boundaries; synthetic files, no ANTs."""
import builtins
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_full_cohort as base
import test_receipts_resources as receipts
atlas = base.atlas


class PlanBoundaryTests(unittest.TestCase):
    def exercise(self, legacy, mutate=None):
        fixture = receipts.LegacyTests() if legacy else receipts.ReceiptTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if legacy:
            template, split_hash = fixture.legacy()
        else:
            template = fixture.template()
        with tempfile.TemporaryDirectory() as tmp:
            args, split_hash, source_hash, docs = base.PrepareTests().fixture(Path(tmp), False)
            records = docs['pilot_selection_v1_identity_confirmed_private.json']['selected']
            manifest = Path(args.evidence)/'pilot_selection_v1_identity_confirmed_private.json'
            fixture.records = records
            fixture.run['inputs_sha256'] = {r['dwi_path']: r['verified_511_file_sha256'] for r in records}
            fixture.run['evidence_sha256'][str(manifest)] = atlas.sha(manifest)
            fixture.run['evidence_sha256']['split'] = split_hash
            fixture.receipt['input_manifest'] = dict(path=str(manifest), sha256=atlas.sha(manifest))
            fixture.receipt['split_sha256'] = split_hash
            fixture.receipt['run'] = fixture.pin('run.json', fixture.run)
            for field in ('geometry_acceptance', 'integrity_acceptance'):
                if field in fixture.receipt:
                    report = atlas.read_json(fixture.receipt[field]['path'])
                    report['run_sha256'] = fixture.receipt['run']['sha256']
                    fixture.receipt[field] = fixture.pin(field+'.json', report)
            if legacy:
                fixture.receipt['fixed_split'] = dict(path=args.split, sha256=split_hash)
                receipt_dir = tempfile.TemporaryDirectory()
                self.addCleanup(receipt_dir.cleanup)
                rp = Path(receipt_dir.name)/'legacy.json'
                fixture.receipt['receipt_path'] = str(rp)
                self.addCleanup(lambda: rp.unlink(missing_ok=True))
            else:
                rp = fixture.root/'receipt.json'
            rp.write_text(json.dumps(fixture.receipt))
            cfg = atlas.read_json(args.config)
            syn = json.loads((base.ENTRY.parent/'configs/syn_pilot.example.json').read_text())
            for key in ('method','syn','shape_gradient','z_pad_low','z_pad_high'):
                cfg[key] = syn[key]
            cfg.update(execution_mode='benchmark', threads=4, parallel_jobs=4,
                       syn_official_script=cfg['official_script'], syn_validation_receipt=str(fixture.root/'deploy.json'),
                       benchmark_template=str(template), benchmark_template_sha256=atlas.sha(template),
                       benchmark_template_receipt=str(rp), benchmark_template_receipt_sha256=atlas.sha(rp),
                       benchmark_manifest_sha256=atlas.hashlib.sha256(json.dumps(records[:8],sort_keys=True,separators=(',', ':')).encode()).hexdigest())
            (Path(cfg['ants_root'])/'bin/MultiplyImages.exe').write_bytes(b'fake')
            Path(args.config).write_text(json.dumps(cfg))
            real_open, real_path_open = builtins.open, Path.open
            def guarded(file, *a, **kw):
                if str(file).endswith(('.nii', '.nii.gz')):
                    raise AssertionError('plan read NIfTI bytes')
                return real_open(file, *a, **kw)
            def guarded_path(path, *a, **kw):
                if str(path).endswith(('.nii', '.nii.gz')):
                    raise AssertionError('plan read NIfTI bytes')
                return real_path_open(path, *a, **kw)
            with patch.object(atlas, 'SPLIT_SHA', split_hash), patch.object(atlas, 'SYN_SOURCE_SHA', source_hash), patch.object(atlas, 'SOURCE_SHA', source_hash):
                with patch('builtins.open', side_effect=guarded), patch.object(Path, 'open', guarded_path), patch.object(atlas.subprocess, 'run', side_effect=AssertionError('No process')):
                    prepared = atlas.prepare(args)
                    atlas.build(*prepared[:3], prepared[3], prepared[4], prepared[5])
                c, rr, inputs, grid, exes, run, evidence = prepared
                self.assertFalse(run.exists())
                c['execute_authorized'] = True
                if mutate:
                    (template if mutate == 'template' else fixture.root/'final_00_dwi.nii.gz').write_bytes(b'changed')
                    with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
                        atlas.execute(c, rr, inputs, run, evidence, [])
                else:
                    # After mandatory byte checks, stop at the still-required deployment gate.
                    with self.assertRaisesRegex(ValueError, 'deployment geometry'):
                        atlas.execute(c, rr, inputs, run, evidence, [])
                self.assertFalse(run.exists())

    def test_plan_and_execute_boundaries(self):
        for legacy in (False, True):
            for mutate in (None, 'template', 'output'):
                with self.subTest(legacy=legacy, mutate=mutate):
                    self.exercise(legacy, mutate)
