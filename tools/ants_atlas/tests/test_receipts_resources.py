"""Receipt and GPU fail-closed regressions; synthetic bytes only, no ANTs."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_full_cohort as baseline
import test_parallel

atlas = baseline.atlas
scheduler = test_parallel.scheduler


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = json.loads((baseline.ENTRY.parent / 'configs/syn_pilot.example.json').read_text())
        self.records = baseline.cohorts()[0]['selected']
        self.manifest = self.pin('pilot_selection_v1_identity_confirmed_private.json', {'selected': self.records})
        self.trusted = {'stageA_acceptance.json': 'b' * 64,
                        'reference_grid_ITK_candidate_private.json': 'c' * 64,
                        'pilot_selection_v1_identity_confirmed_private.json': self.manifest['sha256']}
        self.tools = {'antsRegistration.exe': 'a' * 64}
        self.deployment = {'tool_versions': {'antsRegistration': 'synthetic-only'}}
        self.c['syn_validation_receipt_sha256'] = 'd' * 64
        self.run = {'status': 'COMPUTED_PENDING_GEOMETRIC_QC', 'ended_utc': 'synthetic',
                    'config': copy.deepcopy(self.c), 'source_sha256': atlas.sha(baseline.ENTRY),
                    'scheduler_sha256': atlas.sha(baseline.ENTRY.with_name('scheduler.py')),
                    'inputs_sha256': {r['dwi_path']: r['verified_511_file_sha256'] for r in self.records},
                    'evidence_sha256': {self.manifest['path']: self.manifest['sha256'],
                                        'fixed-split.json': atlas.SPLIT_SHA,
                                        'deployment.json': 'd' * 64, **self.trusted},
                    'official_source': {'raw_sha256': atlas.SYN_SOURCE_SHA},
                    'exe_sha256': self.tools, 'validated_tool_versions': self.deployment['tool_versions'],
                    'syn_validation_receipt_sha256': 'd' * 64,
                    'completed_steps': 1, 'total_steps': 1, 'tasks': {'0': {'status': 'SUCCEEDED'}},
                    'steps': [{'output_sha256': {}}]}
        self.outputs = {}
        for name in atlas.required_acceptance_outputs(len(self.records)):
            p = self.root / name
            p.write_bytes(b'synthetic output')
            self.outputs[str(p)] = atlas.sha(p)
        self.run['steps'][0]['output_sha256'] = self.outputs
        self.refresh()

    def pin(self, name, value):
        p = self.root / name
        p.write_text(json.dumps(value), encoding='utf-8')
        return {'path': str(p), 'sha256': atlas.sha(p)}

    def refresh(self, **acceptance_changes):
        run_pin = self.pin('run.json', self.run)
        acceptance = {'schema': 'atlas-geometric-acceptance/v1', 'status': 'PASS',
                      'independent_review': True, 'reviewer': 'synthetic reviewer',
                      'run_sha256': run_pin['sha256'], 'outputs_sha256': self.outputs}
        acceptance.update(acceptance_changes)
        self.receipt = {'schema': 'syn-pilot-qc/v1', 'status': 'PASS',
                        'run': run_pin, 'geometry_acceptance': self.pin('geometry.json', acceptance),
                        'input_manifest': self.manifest,
                        'split_sha256': atlas.SPLIT_SHA,
                        'method_config': atlas.method_identity(self.run['config']), 'gt_used': False}

    def validate(self):
        return atlas.validate_pilot_qc(self.receipt, self.c, self.records, self.tools, self.deployment, self.trusted)

    def test_valid_and_allowed_run_differences(self):
        self.c.update(input_cohort='fold0_train100', run_id='different', output_root='elsewhere')
        self.validate()

    def test_code_tools_deployment_and_config_drift(self):
        for key, value in [('source_sha256', 'f' * 64), ('scheduler_sha256', 'f' * 64),
                           ('exe_sha256', {'antsRegistration.exe': 'f' * 64}),
                           ('validated_tool_versions', {'antsRegistration': 'changed'}),
                           ('syn_validation_receipt_sha256', 'f' * 64)]:
            with self.subTest(key=key):
                old = self.run[key]
                self.run[key] = value
                self.refresh()
                with self.assertRaises(ValueError):
                    self.validate()
                self.run[key] = old
        self.refresh()
        for key, value in [('seed', 1), ('threads', 4), ('parallel_jobs', 4), ('shape_gradient', .5)]:
            with self.subTest(config=key):
                old = self.c[key]
                self.c[key] = value
                with self.assertRaises(ValueError):
                    self.validate()
                self.c[key] = old

    def test_reference_grid_or_manifest_identity_changed(self):
        for name in self.trusted:
            old = self.trusted[name]
            self.trusted[name] = 'f' * 64
            with self.assertRaises(ValueError):
                self.validate()
            self.trusted[name] = old

    def test_source_hash_and_order_drift(self):
        self.records[0]['verified_511_file_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.validate()
        self.records = baseline.cohorts()[0]['selected'][::-1]
        with self.assertRaises(ValueError):
            self.validate()

    def test_incomplete_failed_and_unaccepted(self):
        for status in ('RUNNING', 'FAILED', 'ABORTED_NONFORMAL'):
            self.run['status'] = status
            self.refresh()
            with self.assertRaises(ValueError):
                self.validate()
        self.run['status'] = 'COMPUTED_PENDING_GEOMETRIC_QC'
        for change in ({'status': 'PENDING'}, {'independent_review': False},
                       {'run_sha256': 'f' * 64}, {'outputs_sha256': {} }):
            self.refresh(**change)
            with self.assertRaises(ValueError):
                self.validate()
        self.run['completed_steps'] = 0
        self.refresh()
        with self.assertRaises(ValueError):
            self.validate()

    def test_pinned_artifact_tamper(self):
        Path(self.receipt['run']['path']).write_text('{}')
        with self.assertRaises(ValueError):
            self.validate()

    def template(self, records=None):
        if records is not None:
            self.records = records
            self.manifest = self.pin('pilot_selection_v1_identity_confirmed_private.json', {'selected': records})
            self.run['inputs_sha256'] = {r['dwi_path']: r['verified_511_file_sha256'] for r in records}
            self.run['evidence_sha256'] = {self.manifest['path']: self.manifest['sha256'], 'fixed-split.json': atlas.SPLIT_SHA}
        for name in atlas.required_acceptance_outputs(len(self.records)):
            path = self.root / name
            path.write_bytes(b'synthetic output')
            self.outputs[str(path)] = atlas.sha(path)
        self.run['config'].update(method='rigid_affine')
        self.refresh()
        self.receipt['schema'] = 'benchmark-template/v1'
        self.receipt['template_sha256'] = self.outputs[str(self.root / 'template_02.nii.gz')]
        return self.root / 'template_02.nii.gz'

    def test_current_affine_template_accepted(self):
        template = self.template()
        atlas.validate_template_receipt(self.receipt, template, self.records)

    def test_template_same_ids_stale_hash_and_bad_membership(self):
        template = self.template()
        expected = copy.deepcopy(self.records)
        for changed in (expected[:-1], expected + [expected[0]],
                        expected + [dict(expected[0], case_id='extra')], expected[::-1]):
            with self.subTest(length=len(changed)):
                with self.assertRaises(ValueError):
                    atlas.validate_template_receipt(self.receipt, template, changed)
        expected[0]['verified_511_file_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            atlas.validate_template_receipt(self.receipt, template, expected)

    def test_template_manifest_and_method_pins(self):
        template = self.template()
        for field, value in [('gt_used', True), ('split_sha256', 'f' * 64),
                             ('method_config', {}), ('template_sha256', 'f' * 64)]:
            old = self.receipt[field]
            self.receipt[field] = value
            with self.assertRaises(ValueError):
                atlas.validate_template_receipt(self.receipt, template, self.records)
            self.receipt[field] = old
        Path(self.manifest['path']).write_text('{}')
        with self.assertRaises(ValueError):
            atlas.validate_template_receipt(self.receipt, template, self.records)

    def test_missing_or_modified_accepted_output(self):
        output = Path(next(iter(self.outputs)))
        output.write_bytes(b'changed after acceptance')
        with self.assertRaises(ValueError):
            self.validate()
        output.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.validate()

    def test_old_unversioned_qc_rejected(self):
        self.receipt.pop('schema')
        with self.assertRaises(ValueError):
            self.validate()

    def test_failed_task_and_missing_split_evidence(self):
        self.run['tasks']['0']['status'] = 'FAILED'
        self.refresh()
        with self.assertRaises(ValueError):
            self.validate()
        self.run['tasks']['0']['status'] = 'SUCCEEDED'
        self.run['evidence_sha256'].pop('fixed-split.json')
        self.refresh()
        with self.assertRaises(ValueError):
            self.validate()

    def test_train100_template_accepted(self):
        records = baseline.cohorts()[1]['selected']
        self.run['config']['input_cohort'] = 'fold0_train100'
        template = self.template(records)
        atlas.validate_template_receipt(self.receipt, template, records)


class LegacyTests(ReceiptTests):
    def legacy(self):
        ReceiptTests.setUp(self)
        template = self.template()
        self.run['config']['parallel_jobs'] = 1
        self.run.pop('tasks'); self.run.pop('total_steps')
        code = self.root / 'historical.py'; code.write_text('historical synthetic code')
        tool = self.root / 'historical.exe'; tool.write_bytes(b'synthetic tool')
        self.run['source_sha256'] = atlas.sha(code)
        self.run['exe_sha256'] = {str(tool): atlas.sha(tool)}
        log = self.root / 'command_000.log'
        argv = [str(tool), 'synthetic']
        log.write_text(json.dumps(argv) + '\ncompleted synthetic command\n')
        self.run['steps'] = [dict(kind='command', argv=argv, exit_code=0,
                                  log=str(log), outputs=list(self.outputs))]
        config = self.pin('original.json', self.run['config'])
        split = self.pin('fixed.json', [{'train': [r['case_id'] for r in self.records], 'val': []}])
        self.run['evidence_sha256'][config['path']] = config['sha256']
        self.run['evidence_sha256']['fixed-split.json'] = split['sha256']
        self.refresh()
        self.receipt.update(schema='legacy-affine-template/v1', template_sha256=atlas.sha(template),
            receipt_path=str(self.root.parent/(self.root.name+'-legacy.json')), hash_collection='post_hoc',
            collected_utc='2026-10-29T00:00:00+00:00', original_config=config, fixed_split=split,
            build_code={'path': str(code), 'sha256': atlas.sha(code)},
            build_tools={str(tool): {'path': str(tool), 'sha256': atlas.sha(tool)}})
        integrity = dict(schema='legacy-affine-integrity/v1', status='PASS', independent_review=True,
            reviewer='synthetic independent check', run_sha256=self.receipt['run']['sha256'],
            checked_steps=[0], outputs_sha256=self.outputs, logs_sha256={str(log): atlas.sha(log)})
        self.receipt['integrity_acceptance'] = self.pin('integrity.json', integrity)
        return template, split['sha256']

    def verify_legacy(self, template, split_hash):
        with patch.object(atlas, 'SPLIT_SHA', split_hash):
            self.receipt['split_sha256'] = split_hash
            Path(self.receipt['receipt_path']).write_text(json.dumps(self.receipt))
            self.addCleanup(lambda p=Path(self.receipt['receipt_path']): p.unlink(missing_ok=True))
            return atlas.validate_template_receipt(self.receipt, template, self.records)

    def test_legacy_valid(self):
        template, digest = self.legacy()
        self.verify_legacy(template, digest)

    def test_legacy_rejects_missing_evidence(self):
        for field in ('integrity_acceptance', 'geometry_acceptance'):
            template, digest = self.legacy()
            self.receipt.pop(field)
            with self.assertRaises((ValueError, KeyError)):
                self.verify_legacy(template, digest)

    def test_legacy_failures_and_tampering(self):
        for change in ('incomplete', 'failed', 'exit', 'log', 'output', 'template', 'input'):
            template, digest = self.legacy()
            if change == 'incomplete': self.run['completed_steps'] = 0
            elif change == 'failed': self.run['status'] = 'FAILED'
            elif change == 'exit': self.run['steps'][0]['exit_code'] = 1
            elif change == 'log': (self.root/'command_000.log').unlink()
            elif change == 'output': (self.root/'final_00_dwi.nii.gz').unlink()
            elif change == 'template': template.write_bytes(b'changed')
            elif change == 'input': self.records[0]['verified_511_file_sha256'] = 'f'*64
            if change in ('incomplete', 'failed', 'exit'):
                self.receipt['run'] = self.pin('run.json', self.run)
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.verify_legacy(template, digest)

    def test_legacy_cannot_be_syn_qc(self):
        self.legacy()
        with self.assertRaisesRegex(ValueError, 'SyN pilot QC'):
            self.validate()


class FakePS:
    class Error(Exception):
        pass
    def cpu_percent(self, interval=None):
        return 1
    def virtual_memory(self):
        return SimpleNamespace(percent=1, used=1, total=100000)
    def disk_io_counters(self):
        return None
    def process_iter(self, attrs):
        return []


class GPUValidationTests(unittest.TestCase):
    def watch(self, state):
        with patch.dict('sys.modules', {'psutil': FakePS()}):
            return scheduler.ResourceWatch(state)

    def sample(self, output):
        state = {}
        watch = self.watch(state)
        with patch.object(scheduler.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)):
            watch.sample({}, force=True)
        return state

    def test_invalid_records_fail_closed(self):
        for output in ('\n', 'not csv', '', '0,1,2', 'x,1,2,3', '0,nan,2,3', '0,inf,2,3',
                       '0,1,nan,3', '0,1,2,inf', '0,1,2,0', '0,101,2,3',
                       '0,-1,2,3', '0,1,-1,3', '0,1,4,3', '-1,1,2,3',
                       '0.5,1,2,3', '0,1,2,3\n0,2,2,3'):
            with self.subTest(output=output):
                self.assertIn('resource_stop', self.sample(output))

    def test_gpu0_valid_high_load_excluded(self):
        self.assertNotIn('resource_stop', self.sample('0,100,100,100\n1,1,2,100'))
        self.assertIn('resource_stop', self.sample('0,1,2,100\n1,80,2,100'))

    def dispatch(self, first_invalid):
        state, called = {}, []
        watch = self.watch(state)
        original = watch.sample
        queries = iter([''] if first_invalid else ['0,1,2,100', '0,1,2,100', ''])
        def query(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=next(queries, ''))
        def sample(tasks, force=False):
            return original(tasks, force=True)
        steps = [dict(kind='fake', stage='benchmark_A', task_id=f'case{i}', case_id=f'case{i}') for i in range(3)]
        with patch.object(scheduler, 'ResourceWatch', return_value=watch), \
             patch.object(watch, 'sample', side_effect=sample), \
             patch.object(scheduler.subprocess, 'run', side_effect=query):
            with self.assertRaisesRegex(RuntimeError, 'GPU observation'):
                scheduler.run_steps(steps, lambda n, s, e: (called.append(n) or dict(s)), state,
                                    lambda: None, {'execution_mode': 'benchmark', 'parallel_jobs': 1, 'threads': 2})
        return called, state

    def test_first_invalid_zero_dispatch(self):
        called, state = self.dispatch(True)
        self.assertEqual(called, [])
        self.assertIn('resource_stop', state)

    def test_invalid_during_run_stops_new_dispatch(self):
        called, state = self.dispatch(False)
        self.assertEqual(called, [0])
        self.assertIn('GPU observation', state['resource_stop'])

