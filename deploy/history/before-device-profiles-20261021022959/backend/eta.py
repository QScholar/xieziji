"""Read-only task ETA: path-weighted progress calibrated by observed throughput."""
import math
import pathlib
import re
import threading
import time


ACTIVE = {'running', 'draining'}
MOTION = re.compile(r'G1 (?:X(-?[\d.]+) Y(-?[\d.]+)|Z([\d.]+)) F([\d.]+)')


def path_work(path, total):
    """One prefix entry per acknowledged line; no commands are executed."""
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        return None
    prefix, position = [0.], [0., 0., 0.]
    with path.open(encoding='ascii') as source:
        for line in source:
            match = MOTION.fullmatch(line.strip())
            cost = .003  # Initial serial overhead estimate; measured rate corrects it.
            if match:
                x, y, z, feed = match.groups()
                target = [float(x), float(y), position[2]] if x is not None else [*position[:2], float(z)]
                rate = float(feed)
                if not rate > 0 or not all(math.isfinite(v) for v in target + [rate]):
                    return None
                cost = max(cost, math.dist(position, target) / rate * 60)
                position = target
            prefix.append(prefix[-1] + cost)
            if len(prefix) > total + 1:
                return None
    return prefix if len(prefix) == total + 1 else None


class TaskETA:
    def __init__(self, jobs):
        self.jobs = pathlib.Path(jobs)
        self.lock = threading.Lock()
        self.job = None
        self.last = None
        self.rate = None
        self.window_time = self.window_work = 0.
        self.tail = None
        self.paused_work = None

    def decorate(self, state, now=None, wall=None):
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        with self.lock:
            return {**state, 'eta':self.estimate(state, now, wall)}

    def estimate(self, state, now, wall):
        phase, job = state.get('phase'), state.get('job_id')
        total = state.get('total', 0)
        empty = dict(remaining_seconds=None, estimated_finish_at=None, source=None, status='inactive')
        if not isinstance(job, str) or not re.fullmatch(r'[a-f0-9]{32}', job) or not isinstance(total, int) or total <= 0:
            self.last = None
            self.window_time = self.window_work = 0.
            return empty
        if self.job != (job, total):
            self.job, self.last, self.rate, self.tail = (job, total), None, None, None
            self.paused_work = None
            self.window_time = self.window_work = 0.
            try:
                self.prefix = path_work(self.jobs / job / 'job.gcode', total)
            except (OSError, ValueError, UnicodeError, OverflowError):
                self.prefix = None
        if phase == 'complete':
            self.last = None
            return dict(remaining_seconds=0, estimated_finish_at=None, source='complete', status='complete')
        if not state.get('connected') or phase not in ACTIVE | {'paused'}:
            self.last = None
            self.window_time = self.window_work = 0.
            return {**empty, 'status':'unavailable' if not state.get('connected') else 'inactive'}
        acknowledged = min(total, max(0, int(state.get('acknowledged', 0))))
        # An `ok` acknowledges buffering, not physical completion. Reserve the
        # last 16 lines as a conservative planner tail until physical Idle.
        index = max(0, acknowledged - 16)
        work = self.prefix[index] if self.prefix else index
        full = self.prefix[-1] if self.prefix else total
        override = state.get('feed_override')
        factor = override / 100 if isinstance(override, (int, float)) and 10 <= override <= 200 else 1.
        store_sample = True
        if self.last:
            previous_time, previous_work, previous_factor, previous_phase = self.last
            dt, delta = now - previous_time, work - previous_work
            if phase in ACTIVE and previous_phase in ACTIVE and previous_factor == factor:
                work = max(work, previous_work)
                if dt > 0:
                    self.window_time += dt
                    self.window_work += work - previous_work
                    if self.window_time >= 8 and self.window_work > 0:
                        measured = self.window_work / self.window_time / factor
                        self.rate = measured if self.rate is None else .65 * self.rate + .35 * measured
                        self.window_time = self.window_work = 0.
                else:
                    # Windows clocks can give concurrent polls the same tick.
                    # Keep the earlier sample so the next tick includes all work.
                    store_sample = False
            else:
                self.window_time = self.window_work = 0.
        if store_sample:
            self.last = (now, work, factor, phase)
        rate = self.rate if self.rate is not None else (1. if self.prefix else None)
        if rate is None:
            return {**empty, 'status':'paused' if phase == 'paused' else 'calibrating'}
        remaining = max(1., (full - work) / rate / factor)
        tail_phase = phase != 'paused' and (phase == 'draining' or acknowledged == total)
        if phase == 'paused':
            if self.paused_work is None:
                if self.tail:
                    start, budget, old_factor = self.tail
                    remaining = max(1., budget - max(0., now - start)) * old_factor / factor
                self.paused_work = remaining * factor
            remaining = self.paused_work / factor
            self.tail = None
        elif tail_phase:
            if self.tail is None:
                if self.paused_work is not None:
                    remaining = self.paused_work / factor
                self.tail = (now, remaining, factor)
            start, budget, old_factor = self.tail
            if old_factor != factor:
                budget = max(1., budget - (now - start)) * old_factor / factor
                start = now
                self.tail = (start, budget, factor)
            remaining = budget - (now - start)
            if remaining <= 0:
                return {**empty, 'status':'draining', 'source':'observed' if self.rate else 'path'}
        else:
            self.tail = None
        if phase != 'paused':
            self.paused_work = None
        return dict(remaining_seconds=math.ceil(remaining),
                    estimated_finish_at=None if phase == 'paused' else wall + remaining,
                    source='observed' if self.rate is not None else 'path', status='draining' if tail_phase else phase)
