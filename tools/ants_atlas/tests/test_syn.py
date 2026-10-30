"""Minimal plan-contract tests; explicitly not real ANTs geometry validation."""
import copy
import json
from pathlib import Path
import unittest
import test_full_cohort as baseline

atlas = baseline.atlas


class SynTests(unittest.TestCase):
    def config(self):
        return json.loads((Path(__file__).parents[1] / 'configs/syn_pilot.example.json').read_text())

    def test_strict_budget_and_legacy_default(self):
        c = baseline.PipelineTests().config()
        self.assertEqual(atlas.protocol(c), 'rigid_affine')
        for key, value in [('threads', True), ('parallel_jobs', False), ('threads', 0), ('threads', 3)]:
            changed = copy.deepcopy(c)
            changed[key] = value
            with self.assertRaises(ValueError):
                atlas.protocol(changed)

    def test_nonlinear_outputs_inverse_chain_and_four_shape_compositions(self):
        c = self.config()
        atlas.protocol(c)
        pilot, _, _, _ = baseline.cohorts()
        records = pilot['selected']
        inputs = [Path(r['dwi_path']) for r in records]
        exes = {name: Path('C:/synthetic/ants') / (name + '.exe') for name in (*atlas.TOOLS, 'MultiplyImages')}
        steps = atlas.build(c, records, inputs, {'case_id': records[0]['case_id']}, exes, Path('C:/synthetic/new'))
        registration = next(s for s in steps if s.get('tool') == 'antsRegistration')
        self.assertEqual(len(registration['outputs']), 4)
        self.assertIn('SyN[0.1,3,0]', registration['argv'])
        final = next(s for s in steps if s.get('outputs', [''])[0].endswith('final_00_atlas_native.nii.gz'))
        self.assertTrue(final['transforms'][0].endswith('0GenericAffine.mat,1]'))
        self.assertTrue(final['transforms'][1].endswith('1InverseWarp.nii.gz'))
        shape = [s for s in steps if s.get('purpose', '').startswith('v2.6.5')]
        self.assertEqual(len(shape), 2)
        self.assertTrue(all(len(s['transforms']) == 5 and len(set(s['transforms'][1:])) == 1 for s in shape))
        self.assertEqual(sum(s.get('tool') == 'MultiplyImages' for s in steps), 2)
        self.assertFalse(c['execute_authorized'])
        self.assertFalse(c['syn_deployment_validation_accepted'])

    def test_benchmark_uses_only_fixed_eight_and_no_template_rebuild(self):
        c = self.config()
        c.update(execution_mode='benchmark', threads=4, parallel_jobs=4,
                 benchmark_template='C:/synthetic/frozen.nii.gz')
        records = baseline.cohorts()[0]['selected'][:8]
        inputs = [Path(r['dwi_path']) for r in records]
        exes = {name: Path('C:/synthetic/ants') / (name + '.exe') for name in (*atlas.TOOLS, 'MultiplyImages')}
        steps = atlas.build(c, records, inputs, {}, exes, Path('C:/synthetic/benchmark'))
        registrations = [s for s in steps if s.get('tool') == 'antsRegistration']
        self.assertEqual(len(registrations), 16)
        self.assertEqual({s['fixed'] for s in registrations}, {str(Path('C:/synthetic/frozen.nii.gz'))})
        self.assertEqual({s['threads'] for s in registrations}, {4, 6})
        self.assertFalse(any(s.get('tool') in ('AverageImages', 'MultiplyImages') for s in steps))
