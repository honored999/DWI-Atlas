"""Bounded case scheduler. Workers emit events; only the calling thread publishes state."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from itertools import groupby
from queue import Queue, Empty
import subprocess
import math
import re
import time
from threading import Event


def utc():
    return datetime.now(timezone.utc).isoformat()


def units(steps):
    """Contiguous case groups; ungrouped steps are barriers, never parallel reductions."""
    for key, items in groupby(enumerate(steps), lambda pair: pair[1].get('stage')):
        items = list(items)
        if key is None:
            for item in items:
                yield None, [[item]]
        else:
            yield key, [list(group) for _, group in groupby(items, lambda pair: pair[1]['task_id'])]


class ResourceWatch:
    """Benchmark-only observation; only registered process identities can be ended."""
    def __init__(self, state):
        import psutil
        self.ps = psutil
        self.state = state
        self.registered = {}
        self.peak = {}
        self.samples = []
        self.phase_sample_start = 0
        self.cpu_high = 0
        self.start = time.monotonic()
        self.last = 0
        psutil.cpu_percent(None)
        self.previous_disk = psutil.disk_io_counters()
        self.previous_time = self.start
        state['training_observation'] = {
            'status': 'EVIDENCE_GAP',
            'detail': 'Read-only process names/CPU/RAM only; no existing training data-supply log configured. '
                      'Cannot claim no impact on other training.'}
        state['process_snapshot'] = []
        for proc in psutil.process_iter(['pid', 'name', 'create_time']):
            try:
                if any(word in (proc.info['name'] or '').lower() for word in ('python', 'train', 'ants')):
                    state['process_snapshot'].append(proc.info)
            except psutil.Error:
                pass

    def register(self, n, pid):
        try:
            proc = self.ps.Process(pid)
            self.registered[n] = (pid, proc.create_time())
        except self.ps.Error:
            return None
        return self.registered[n][1]

    def sample(self, tasks, force=False):
        now = time.monotonic()
        if not force and now - self.last < 2:
            return None
        self.last = now
        mem = self.ps.virtual_memory()
        cpu = self.ps.cpu_percent(interval=.25 if not self.samples else None)
        gpu = []
        try:
            answer = subprocess.run(['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                    timeout=5, check=False)
            if answer.returncode != 0:
                raise RuntimeError(f'nvidia-smi exit code {answer.returncode}')
            seen = set()
            lines = answer.stdout.splitlines()
            if not lines:
                raise ValueError('empty device list')
            for row, line in enumerate(lines, 1):
                fields = [v.strip() for v in line.split(',')]
                if len(fields) != 4:
                    raise ValueError(f'row {row}: expected four columns')
                if re.fullmatch(r'[0-9]+', fields[0]) is None:
                    raise ValueError(f'row {row}: invalid device index')
                index = int(fields[0])
                if index in seen:
                    raise ValueError(f'row {row}: duplicate device index {index}')
                seen.add(index)
                util, used, total = map(float, fields[1:])
                if not all(math.isfinite(v) for v in (util, used, total)):
                    raise ValueError(f'row {row}: non-finite utilization/memory')
                if not (0 <= util <= 100 and total > 0 and 0 <= used <= total):
                    raise ValueError(f'row {row}: utilization/memory out of range')
                gpu.append({'index': index, 'util': util, 'vram_percent': 100 * (used / total),
                            'excluded_from_gate': index == 0})
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            gpu = []
            self.state['resource_stop'] = ('GPU observation failed; fail closed for benchmark: '
                                           + type(exc).__name__ + ': ' + str(exc))
        live_memory = {}
        for n, (pid, created) in list(self.registered.items()):
            try:
                proc = self.ps.Process(pid)
                if proc.create_time() != created:
                    continue
                processes = [proc, *proc.children(recursive=True)]
                rss, private = 0, 0
                for child in processes:
                    mi = child.memory_info()
                    rss += mi.rss
                    private += getattr(mi, 'private', mi.rss)
                task = tasks[str(n)]['task_id']
                peak = self.peak.setdefault(task, {'working_set': 0, 'private': 0, 'samples': 0})
                peak['working_set'] = max(peak['working_set'], rss)
                peak['private'] = max(peak['private'], private)
                peak['samples'] += 1
                live_memory[task] = rss
            except self.ps.Error:
                continue
        disk = self.ps.disk_io_counters()
        delta = max(now - self.previous_time, .001)
        active_cases = {task['task_id'] for task in tasks.values() if task.get('status') == 'RUNNING'}
        sample = {'active_case_count': len(active_cases), 'elapsed_s': now - self.start, 'cpu_percent': cpu,
                  'ram_percent': mem.percent, 'ram_used': mem.used, 'ram_total': mem.total,
                  'gpu': gpu, 'task_working_set': live_memory,
                  'disk_bytes_per_s': ((disk.read_bytes + disk.write_bytes -
                                        self.previous_disk.read_bytes - self.previous_disk.write_bytes) / delta)
                                      if disk and self.previous_disk else None,
                  'disk_busy_percent': (min(100., (disk.busy_time - self.previous_disk.busy_time) / (10 * delta))
                                        if disk and self.previous_disk and hasattr(disk, 'busy_time') else None)}
        self.previous_disk, self.previous_time = disk, now
        self.samples.append(sample)
        self.cpu_high = self.cpu_high + 1 if cpu >= 80 else 0
        if not self.state.get('resource_stop') and (mem.percent >= 80 or self.cpu_high >= 3 or any(
                g['index'] != 0 and (g['util'] >= 80 or g['vram_percent'] >= 80) for g in gpu)):
            self.state['resource_stop'] = '80% resource gate reached'
        self.state['resource_samples'] = self.samples
        self.state['task_memory_peaks'] = self.peak
        return sample

    def capacity(self, current, maximum, completed, tasks):
        if current >= maximum:
            return current
        if not any(s['active_case_count'] >= current for s in self.samples[self.phase_sample_start:]):
            if completed:
                self.state['resource_stop'] = 'No effective resource sample at current ramp level'
            return current
        eligible = [self.peak[t] for t in completed if t in self.peak and self.peak[t]['samples'] > 0]
        # A fully finished case is the minimum basis for a conservative next-level estimate.
        if len(eligible) < current:
            if len(completed) >= current:
                self.state['resource_stop'] = 'Cannot reliably estimate SyN memory from completed cases'
            return current
        target = min(current * 2, maximum)
        mem = self.ps.virtual_memory()
        peak = max(max(p['working_set'], p['private']) for p in eligible)
        if mem.used + target * peak * 1.5 >= .8 * mem.total:
            self.state['resource_stop'] = 'Insufficient RAM headroom for next ramp level'
            return current
        return target

    def terminate_owned(self):
        # Verify identity before enumerating children; never terminate by executable name.
        for pid, created in list(self.registered.values()):
            try:
                root = self.ps.Process(pid)
                if root.create_time() != created:
                    continue
                identities = [(p.pid, p.create_time()) for p in root.children(recursive=True)]
                identities.append((pid, created))
                for child_pid, child_created in reversed(identities):
                    try:
                        child = self.ps.Process(child_pid)
                        if child.create_time() == child_created:
                            child.kill()
                    except self.ps.Error:
                        pass
            except self.ps.Error:
                pass


def summarize(values):
    if not values:
        return None
    values = sorted(values)
    import math
    return {'mean': sum(values) / len(values),
            'p95': values[max(0, math.ceil(.95 * len(values)) - 1)], 'peak': values[-1]}


def run_steps(steps, perform, state, save, config):
    events = Queue()
    failed = Event()
    abort = Event()
    state.update(tasks={}, active_tasks=[], total_steps=len(steps), completed_steps=0)
    benchmark = config.get('execution_mode') == 'benchmark'
    watch = ResourceWatch(state) if benchmark else None
    state['benchmark_results'] = {} if benchmark else None

    def emit(kind, n, data=None):
        events.put((kind, n, {**(data or {}), '_event_utc': utc(), '_event_monotonic': time.monotonic()}))

    def worker(group):
        for n, step in group:
            if abort.is_set():
                raise RuntimeError('Owned run cancelled/timeout; next dependent step not launched')
            emit('start', n)
            try:
                result = perform(n, step, emit)
            except BaseException as exc:
                failed.set()
                emit('failed', n, {'error': type(exc).__name__ + ': ' + str(exc)})
                raise
            emit('done', n, result)

    def drain():
        while True:
            try:
                kind, n, data = events.get_nowait()
            except Empty:
                break
            event_utc = data.pop('_event_utc')
            event_clock = data.pop('_event_monotonic')
            task = state['tasks'].setdefault(str(n), {'task_id': steps[n].get('task_id', f'step:{n}'),
                                                     'step_id': n, 'threads': steps[n].get('threads', config['threads'])})
            if kind == 'start':
                task.update(status='RUNNING', started_utc=event_utc, started_monotonic=event_clock,
                            input_identity={k: steps[n][k] for k in ('image', 'reference', 'fixed', 'moving', 'path') if k in steps[n]})
                state['active_tasks'].append(n)
            elif kind == 'pid':
                task.update(data)
                if watch:
                    task['process_created'] = watch.register(n, data['pid'])
            elif kind == 'exit':
                task.update(data)
            else:
                task.update(status='SUCCEEDED' if kind == 'done' else 'FAILED', ended_utc=event_utc)
                task['wall_s'] = event_clock - task.get('started_monotonic', event_clock)
                if n in state['active_tasks']:
                    state['active_tasks'].remove(n)
                if kind == 'done':
                    steps[n] = data
                    state['completed_steps'] += 1
                    task['output_identity'] = data.get('output_sha256', {})
                else:
                    task.update(data)
            save()

    save()
    for stage, groups in units(steps):
        if benchmark and stage == 'benchmark_B':
            a = state['benchmark_results'].get('benchmark_A', {})
            if not a.get('eligible_for_B'):
                raise RuntimeError('A gate not satisfied; B not dispatched')
        started = time.monotonic()
        sample_start = len(watch.samples) if watch else 0
        failure = None
        completed = []
        maximum = config['parallel_jobs'] if stage else 1
        capacity = 1 if benchmark and stage else maximum
        pending = iter(groups)
        exhausted = False
        active = {}
        if watch and stage:
            watch.phase_sample_start = sample_start
            watch.cpu_high = 0
            watch.sample(state['tasks'], force=True)
        with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix='atlas-case') as pool:
            try:
                while active or not exhausted:
                    drain()
                    if watch and stage:
                        watch.sample(state['tasks'])
                        if time.monotonic() - started >= 1800:
                            state['resource_stop'] = '30 minute phase hard timeout'
                            abort.set()
                            watch.terminate_owned()
                        if state.get('resource_stop'):
                            failure = failure or RuntimeError(state['resource_stop'])
                        if not failure:
                            capacity = watch.capacity(capacity, maximum, completed, state['tasks'])
                            if state.get('resource_stop'):
                                failure = RuntimeError(state['resource_stop'])
                    # Harvest *all* ready results before considering another dispatch.
                    for future in [f for f in active if f.done()]:
                        group = active.pop(future)
                        try:
                            future.result()
                            completed.append(group[0][1].get('task_id', f'step:{group[0][0]}'))
                        except BaseException as exc:
                            failure = failure or exc
                    if failed.is_set():
                        failure = failure or RuntimeError('Case failed; no further dispatch')
                    if failure:
                        exhausted = True
                    while not exhausted and not failed.is_set() and len(active) < capacity:
                        try:
                            group = next(pending)
                        except StopIteration:
                            exhausted = True
                            break
                        if watch and stage and watch.peak:
                            mem = watch.ps.virtual_memory()
                            peak = max(max(p['working_set'], p['private']) for p in watch.peak.values())
                            if mem.used + max(1, capacity - len(active)) * peak * 1.5 >= .8 * mem.total:
                                state['resource_stop'] = 'Projected concurrent SyN RAM reaches 80%'
                                failure = RuntimeError(state['resource_stop'])
                                exhausted = True
                                break
                        active[pool.submit(worker, group)] = group
                    if active:
                        time.sleep(.02)
                drain()
            except BaseException:
                # Cancellation owns this run only. Atlas tasks naturally drain; benchmark children are identity checked.
                abort.set()
                if watch:
                    drain()
                    watch.terminate_owned()
                for future in active:
                    try:
                        future.result()
                    except BaseException:
                        pass
                drain()
                raise
        if failure:
            state['undispatched_or_incomplete_steps'] = [n for n in range(len(steps))
                if state['tasks'].get(str(n), {}).get('status') != 'SUCCEEDED']
            if benchmark and stage:
                state['benchmark_results'][stage] = {'successful_cases': len(completed),
                    'failed_or_incomplete_cases': len(groups) - len(completed),
                    'wall_s': time.monotonic() - started, 'eligible_for_B': False,
                    'status': 'ABORTED_NONFORMAL' if state.get('resource_stop') else 'FAILED',
                    'full_cohort_extrapolation': 'FORBIDDEN'}
                save()
            raise failure
        if benchmark and stage:
            wall = time.monotonic() - started
            samples = watch.samples[sample_start:]
            result = {'successful_cases': len(completed), 'failed_cases': 0, 'cancelled_cases': 0,
                      'wall_s': wall, 'throughput_cases_per_s': len(completed) / wall,
                      'max_concurrency': max((s['active_case_count'] for s in samples), default=0), 'start_order': [g[0][1]['case_id'] for g in groups],
                      'per_case_wall_s': {g[0][1]['case_id']: sum(state['tasks'][str(n)]['wall_s'] for n, _ in g) for g in groups},
                      'resources': {k: summarize([s[k] for s in samples if s[k] is not None])
                                    for k in ('cpu_percent', 'ram_percent', 'disk_bytes_per_s', 'disk_busy_percent')},
                      'eligible_for_B': len(completed) == 8 and any(s['active_case_count'] == 4 for s in samples) and not state.get('resource_stop'),
                      'geometry': 'command/output/grid/binary/Jacobian checks only; anatomical QC pending',
                      'task_memory': {task: peak for task, peak in watch.peak.items() if task.startswith(stage + ':')}}
            result['gpu_resources'] = {str(index): {key: summarize([g[key] for sample in samples
                for g in sample['gpu'] if g['index'] == index]) for key in ('util', 'vram_percent')}
                for index in {g['index'] for sample in samples for g in sample['gpu']}}
            result['sampling_interval_s'] = 2
            result['memory_peak_caveat'] = 'Sampled child-process peaks at 2s; short transients may be missed; Python image buffers included only in system RAM'
            if not all(task in watch.peak for task in completed):
                result['eligible_for_B'] = False
                result['incomplete_resource_evidence'] = 'At least one case lacks an effective process memory sample'
            result['disk_busy_status'] = ('AVAILABLE' if all(s['disk_busy_percent'] is not None for s in samples)
                                          else 'UNAVAILABLE; byte throughput only, not disk saturation')
            state['benchmark_results'][stage] = result
            save()
        if watch and stage == 'benchmark_B':
            a, b = (state['benchmark_results'][key] for key in ('benchmark_A', 'benchmark_B'))
            state['benchmark_selection'] = '4x4'
            state['benchmark_candidate'] = ('4x6' if b['eligible_for_B'] and
                    b['throughput_cases_per_s'] >= a['throughput_cases_per_s'] * 1.1 else '4x4')
            state['benchmark_selection_status'] = 'PENDING_PAIRED_GEOMETRIC_QC; no automatic full run'
            state['benchmark_order_caveat'] = 'A then B; caching and system load confounded; engineering only'
            save()
