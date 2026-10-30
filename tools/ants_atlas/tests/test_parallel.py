"""Small scheduler regressions; no ANTs or patient images."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location('scheduler', Path(__file__).parents[1] / 'scheduler.py')
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


class ParallelTests(unittest.TestCase):
    def test_case_dependencies_barrier_and_coordinator_publication(self):
        steps = [dict(kind='fake', stage='round', task_id=f'case{i}', case_id=f'case{i}', local=j)
                 for i in range(4) for j in range(2)] + [dict(kind='average')]
        barrier = threading.Barrier(4, timeout=3)
        lock = threading.Lock()
        completed = []
        caller = threading.get_ident()
        state = {}
        saves = []

        def perform(n, step, emit):
            if step['kind'] == 'average':
                self.assertEqual(len(completed), 8)
            else:
                if step['local'] == 0:
                    barrier.wait()
                    time.sleep(.005 * (4 - int(step['case_id'][-1])))
                else:
                    self.assertIn(n - 1, completed)
                with lock:
                    completed.append(n)
            return dict(step)

        def save():
            saves.append(threading.get_ident())

        scheduler.run_steps(steps, perform, state, save, {'parallel_jobs': 4, 'threads': 4})
        self.assertEqual(state['completed_steps'], 9)
        self.assertEqual(state['active_tasks'], [])
        self.assertTrue(all(t == caller for t in saves))
        self.assertEqual([s['task_id'] for s in steps[:-1]], [f'case{i}' for i in range(4) for _ in range(2)])

    def test_failure_stops_dispatch_and_reduction(self):
        steps = [dict(kind='fake', stage='round', task_id=f'case{i}') for i in range(4)]
        steps.append(dict(kind='average'))
        called = []
        state = {}

        def perform(n, step, emit):
            called.append(n)
            raise ValueError('synthetic failure')

        with self.assertRaises((RuntimeError, ValueError)):
            scheduler.run_steps(steps, perform, state, lambda: None,
                                {'parallel_jobs': 1, 'threads': 2})
        self.assertEqual(called, [0])
        self.assertEqual(state['completed_steps'], 0)
        self.assertEqual(state['tasks']['0']['status'], 'FAILED')
