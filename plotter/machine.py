"""One serial reader, acknowledged writes, and live GRBL feed hold/resume."""
import datetime
import json
import math
import re
import threading
import time


class MotionCancelled(Exception):
    pass


class Machine:
    def __init__(self, simulate=False, serial_factory=None):
        self.simulate = simulate
        self.factory = serial_factory
        self.serial = None
        self.operation = threading.RLock()
        self.tx_lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.manual_cancel = threading.Event()
        self.last_status_poll = 0.
        self.worker = None
        self.log = None
        self.state = dict(connected=False, mode='simulation' if simulate else 'serial',
                          phase='disconnected', status='', port='', job_id=None, acknowledged=0, total=0, error=None,
                          operation_type=None, position=None, pen='unknown', pen_top=None, status_updated_at=None,
                          motion_revision=0, last_action='')

    def changed_position(self):
        with self.state_lock:
            self.state['motion_revision'] += 1

    def check_cancelled(self):
        if self.stop_event.is_set(): raise MotionCancelled()

    def update_position(self, position, phase='Idle'):
        position = [round(v, 3) for v in position]
        z = position[2]
        self.update(position=position, pen='up' if z <= .05 else 'down' if abs(z - 10) < .05 else 'unknown',
                    pen_top=z <= .05, status_updated_at=time.time())
        if self.simulate:
            self.update(status=f'<{phase}|MPos:{position[0]:.3f},{position[1]:.3f},{position[2]:.3f}|FS:0,0>')

    def update(self, **fields):
        with self.state_lock:
            self.state.update(fields)

    def snapshot(self):
        with self.state_lock:
            return dict(self.state)

    def active(self):
        return self.worker is not None and self.worker.is_alive()

    def refresh_status(self):
        # HTTP polling must not compete with the single motion worker's reader.
        if self.simulate or not self.operation.acquire(blocking=False): return self.snapshot()
        try:
            state = self.snapshot()
            if state['connected'] and not self.active() and state['phase'] in ('idle', 'complete') and time.monotonic() - self.last_status_poll >= .5:
                self.last_status_poll = time.monotonic()
                try: self.status()
                except (RuntimeError, OSError, TimeoutError) as error:
                    self.update(phase='failed', position=None, pen='unknown', pen_top=None, error=str(error))
            return self.snapshot()
        finally:
            self.operation.release()

    def record(self, direction, raw):
        if self.log:
            self.log.write(json.dumps(dict(time=datetime.datetime.now().astimezone().isoformat(),
                direction=direction, hex=raw.hex(), text=raw.decode('ascii', errors='replace'))) + '\n')
            self.log.flush()

    def write(self, raw):
        with self.tx_lock:
            self.record('tx', raw)
            self.serial.write(raw)

    def read(self):
        raw = self.serial.readline()
        if not raw: return ''
        self.record('rx', raw)
        line = raw.decode('ascii', errors='replace').strip()
        if line.startswith('<'):
            self.update(status=line)
            position = re.search(r'MPos:([-\d.]+),([-\d.]+),([-\d.]+)', line)
            if position: self.update_position([float(v) for v in position.groups()])
            else: self.update(position=None, pen='unknown', pen_top=None)
        if line.startswith(('error:', 'ALARM:')): raise RuntimeError(line)
        return line

    def status(self):
        with self.tx_lock:
            self.check_cancelled()
            self.write(b'?')
        end = time.monotonic() + 4
        while time.monotonic() < end:
            self.check_cancelled()
            line = self.read()
            self.check_cancelled()
            if line.startswith('<'): return line
        raise TimeoutError('控制器状态查询超时')

    def command(self, line, running=False, manual=False):
        raw = line.encode('ascii') + b'\n'
        if len(raw) > 78: raise ValueError('指令过长')
        with self.tx_lock:
            self.check_cancelled()
            if manual and self.manual_cancel.is_set(): return []
            self.write(raw)
        deadline = time.monotonic() + 30
        replies = []; last_poll = time.monotonic()
        while True:
            self.check_cancelled()
            reply = self.read()
            self.check_cancelled()
            if reply:
                replies.append(reply)
                if reply == 'ok': return replies
            now = time.monotonic()
            if running and now - last_poll > .5:
                self.write(b'?'); last_poll = now
            if running and not self.pause_event.is_set():
                deadline = now + 30
            if now > deadline: raise TimeoutError('指令确认超时：' + line)

    def connect(self, port):
        with self.operation:
            if self.active(): raise ValueError('写字任务仍在运行')
            if self.serial is not None or self.snapshot()['connected']:
                raise ValueError('请先断开当前连接')
            self.stop_event.clear()
            self.manual_cancel.clear()
            if self.simulate:
                self.update(connected=True, port='SIMULATOR', phase='idle', status='<Idle|MPos:0.000,0.000,0.000|FS:0,0>')
                self.update_position([0., 0., 0.])
                return self.snapshot()
            if self.factory is None:
                import serial
                factory = serial.Serial
            else: factory = self.factory
            device = factory(port=None, baudrate=115200, timeout=.2, write_timeout=3)
            device.dtr = False; device.rts = False; device.port = port
            self.serial = device
            try:
                device.open()
                end = time.monotonic() + 2
                while time.monotonic() < end: self.read()
                status = self.status()
                if not status.startswith('<Idle'): raise ValueError('机器未处于空闲状态：' + status)
                info = self.command('$I')
                if not any('[VER:1.3a.20221114:' in line for line in info):
                    raise ValueError('当前版本只支持已验证的 1.3a.20221114 写字机固件')
                self.update(connected=True, phase='idle', port=port, error=None)
            except Exception:
                device.close(); self.serial = None
                raise
            return self.snapshot()

    def disconnect(self):
        with self.operation:
            if self.active(): raise ValueError('请先完成当前写字任务')
            if self.snapshot()['phase'] == 'failed' and self.serial:
                raise ValueError('中断后控制器可能保留指令，请先在设备端处理并重启控制器，再重启服务')
            if self.serial: self.serial.close()
            self.serial = None
            self.update(connected=False, phase='disconnected', port='', status='', operation_type=None, position=None, pen='unknown', pen_top=None)
            return self.snapshot()

    def jog(self, axis, distance, feed=300):
        if axis not in ('X', 'Y'): raise ValueError('微动轴必须是 X 或 Y')
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not .01 <= abs(distance) <= 20 or not math.isfinite(distance):
            raise ValueError('单次微动距离必须为 0.01–20 mm')
        if isinstance(feed, bool) or not isinstance(feed, (int, float)) or not 50 <= feed <= 1000 or not math.isfinite(feed):
            raise ValueError('微动速度必须为 50–1000 mm/min')
        distance = round(distance, 3)
        return self.begin_manual(f'$J=G21 G91 {axis}{distance:.3f} F{feed:g}',
                                 f'{axis}{distance:+g} mm', axis=axis, distance=distance, feed=feed)

    def set_pen(self, position):
        if position not in ('up', 'down'): raise ValueError('笔状态必须是 up 或 down')
        target = 0 if position == 'up' else 10
        return self.begin_manual(f'$J=G21 G90 G53 Z{target} F600', '抬笔' if target == 0 else '落笔一次', target_z=target)

    def begin_manual(self, command, label, axis=None, distance=0, feed=300, target_z=None):
        with self.operation:
            state = self.snapshot()
            if not state['connected']: raise ValueError('请先连接机器')
            if self.active() or state['operation_type'] == 'writing':
                raise ValueError('任务运行或暂停期间不能使用手动控制台')
            if state['phase'] not in ('idle', 'complete'):
                raise ValueError('当前机器状态不能手动移动，请先处理控制器状态')
            if not self.simulate:
                status = self.status()
                if not status.startswith('<Idle'): raise ValueError('手动控制要求机器处于 Idle：' + status)
            steps = [(command, target_z)]
            if target_z is not None:
                actual = self.snapshot()['position']
                if actual is None:
                    raise ValueError('控制器未返回 Z 坐标，暂不能操作笔')
                if target_z == 0 and actual[2] <= .05:
                    self.update(last_action='笔已在顶部，无需抬笔')
                    return self.snapshot()
                if target_z == 10:
                    # A manual press is one complete test cycle, not a latched
                    # "down" state. Always finish at Z0 so presses can repeat.
                    steps = [('$J=G21 G90 G53 Z10 F600', 10), ('$J=G21 G90 G53 Z0 F600', 0)]
                    if actual[2] > .05:
                        steps.insert(0, ('$J=G21 G90 G53 Z0 F600', 0))
            self.manual_cancel.clear()
            self.changed_position()
            self.update(phase='manual', operation_type='manual', last_action=label, error=None)
            self.worker = threading.Thread(target=self.run_manual, args=(steps, label, axis, distance, feed), daemon=True)
            self.worker.start()
            return self.snapshot()

    def run_manual(self, steps, label, axis, distance, feed):
        try:
            for command, target_z in steps:
                if self.manual_cancel.is_set(): break
                if axis is None and target_z == 0 and self.snapshot()['pen_top'] is True:
                    continue
                if self.simulate:
                    start = list(self.snapshot()['position'] or [0., 0., 0.])
                    target = list(start)
                    if axis: target[0 if axis == 'X' else 1] += distance
                    else: target[2] = target_z
                    duration = min(2., max(.15, (abs(distance) / feed * 60) if axis else .3))
                    began = time.monotonic()
                    while True:
                        fraction = min(1., (time.monotonic() - began) / duration)
                        self.update_position([a + (b-a)*fraction for a, b in zip(start, target)], 'Jog')
                        if self.manual_cancel.is_set() or fraction >= 1: break
                        time.sleep(.02)
                    self.update_position(self.snapshot()['position'])
                else:
                    self.command(command, manual=True)
                    deadline = time.monotonic() + 35
                    while True:
                        status = self.status()
                        if status.startswith('<Idle'): break
                        if status.startswith(('<Alarm', '<Door', '<Hold')):
                            raise RuntimeError('手动动作未正常完成：' + status)
                        if time.monotonic() > deadline: raise TimeoutError('等待手动动作完成超时')
                        time.sleep(.05)
            completed = '落笔完成，已自动回位 Z0' if label == '落笔一次' else label + '完成'
            self.update(phase='idle', operation_type=None, last_action=self.snapshot()['last_action'] if self.manual_cancel.is_set() else completed)
        except Exception as error:
            if self.serial:
                try: self.write(b'\x85')
                except Exception: pass
            self.update(phase='failed', operation_type=None, error=str(error), last_action='手动动作失败')

    def cancel_manual(self, terminate=False):
        # Dedicated endpoint: a stale console cannot stop a writing task.
        with self.operation:
            if not self.active() or self.snapshot()['operation_type'] != 'manual':
                raise ValueError('当前没有手动动作；写字任务请使用任务区控制')
            with self.tx_lock:
                self.manual_cancel.set()
                self.update(phase='manual_stopping', last_action='手动动作已终止' if terminate else '已暂停，本次微动已取消')
                if not self.simulate: self.write(b'\x85')
            return self.snapshot()

    def stop(self):
        # Never wait for the worker/operation lock before sending the reset.
        with self.tx_lock:
            state = self.snapshot()
            if state['phase'] == 'stopping': return state
            if state['operation_type'] != 'writing':
                raise ValueError('当前没有可终止的写字任务')
            self.stop_event.set()
            self.pause_event.set()
            self.changed_position()
            self.update(phase='stopping', last_action='正在终止写字任务')
            if not self.simulate:
                try:
                    self.write(b'!')
                    self.write(b'\x18')
                except Exception as error:
                    self.update(error='终止指令发送失败，请在设备端停机：' + str(error))
            return self.snapshot()

    def finish_stop(self):
        with self.tx_lock:
            if self.serial:
                try: self.serial.close()
                except Exception: pass
            self.serial = None
            self.update(connected=False, phase='stopped', operation_type=None, position=None, pen='unknown', pen_top=None, status='',
                        last_action='任务已终止，请重新连接并定位原点；当前任务不能继续')

    def check_origin(self, metadata):
        if self.simulate: return {'simulation': True}
        status = self.status()
        if not status.startswith('<Idle'): raise ValueError('机器未空闲：' + status)
        position = re.search(r'MPos:([\d.-]+),([\d.-]+),([\d.-]+)', status)
        if not position or abs(float(position[3])) > .01:
            raise ValueError('请先抬笔至 Z0；当前位置将作为本次书写原点，无需将 XY 机器坐标归零')
        modal = self.command('$G')
        if not any(re.search(r'\bG54\b', line) for line in modal):
            raise ValueError('工作坐标系不是已验证的 G54')
        parameters = self.command('$#')
        offsets = {m[1]: [float(v) for v in m[2].split(',')] for line in parameters
                   if (m := re.fullmatch(r'\[(G54|G92|TLO):([\d.,-]+)\]', line))}
        if (any(k not in offsets or any(abs(v) > .01 for v in offsets[k]) for k in ('G54', 'TLO'))
                or len(offsets.get('G92', [])) != 3 or abs(offsets['G92'][2]) > .01):
            raise ValueError('工作坐标偏移或刀具偏移不是零，请在设备端处理后重试')
        replies = self.command('$$')
        settings = {int(m[1]): float(m[2]) for line in replies if (m := re.fullmatch(r'\$(\d+)=([\d.]+)', line))}
        machine_paper = metadata.get('machine_paper', metadata['paper'])
        limits = {130: machine_paper[0], 131: machine_paper[1], 132: 10, 110: 3000, 111: 3000, 112: 3000}
        if any(settings.get(k, 0) < v for k, v in limits.items()):
            raise ValueError('机器行程或速度范围不足')
        if any(settings.get(k) != 0 for k in (20, 21, 22)):
            raise ValueError('限位/回零配置与实测配置不同')
        if any(settings.get(k) != v for k, v in {3: 7, 100: 80, 101: 80, 102: 100}.items()):
            raise ValueError('轴方向或每毫米步数与实测配置不同')
        startup = self.command('$N')
        if not all(line in ('$N0=', '$N1=', 'ok') for line in startup):
            raise ValueError('检测到未经验证的启动指令')
        return dict(status=status, settings=replies, modal=modal, parameters=parameters, startup=startup)

    def start(self, job_id, folder, result):
        with self.operation:
            if not self.snapshot()['connected']: raise ValueError('请先连接机器')
            if self.active(): raise ValueError('已有写字任务')
            if self.snapshot()['phase'] == 'failed': raise ValueError('请先处理控制器中断状态并重启服务')
            try:
                probe = self.check_origin(result['metadata'])
            except (RuntimeError, OSError, TimeoutError) as error:
                self.update(phase='failed', error=str(error))
                raise
            (folder / 'probe.json').write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding='utf8')
            self.pause_event.set()
            self.stop_event.clear()
            self.update(phase='running', operation_type='writing', job_id=job_id, acknowledged=0,
                        total=result['metadata']['commands'], error=None, started_at=datetime.datetime.now().astimezone().isoformat())
            self.worker = threading.Thread(target=self.run, args=(folder, result), daemon=True)
            self.worker.start()
            return self.snapshot()

    def pause(self):
        with self.operation:
            with self.tx_lock:
                if not self.active() or self.snapshot()['operation_type'] != 'writing': raise ValueError('没有运行中的写字任务')
                if self.snapshot()['phase'] not in ('running', 'draining', 'paused') or self.stop_event.is_set():
                    raise ValueError('当前状态不能暂停')
                self.pause_event.clear()
                if not self.simulate: self.write(b'!')
                self.update(phase='paused')
            return self.snapshot()

    def resume(self):
        with self.operation:
            with self.tx_lock:
                if not self.active() or self.snapshot()['phase'] != 'paused' or self.stop_event.is_set(): raise ValueError('当前任务未暂停')
                if not self.simulate: self.write(b'~')
                self.update(phase='running'); self.pause_event.set()
            return self.snapshot()

    def run(self, folder, result):
        try:
            self.log = (folder / 'serial.jsonl').open('a', encoding='utf8')
            last_poll = time.monotonic()
            for i, line in enumerate(result['gcode'].splitlines(), 1):
                self.check_cancelled()
                while not self.pause_event.wait(.2):
                    self.check_cancelled()
                    if not self.simulate: self.status()
                self.check_cancelled()
                if self.simulate:
                    time.sleep(.003)
                else:
                    self.command(line, running=True)
                    if time.monotonic() - last_poll > .5:
                        self.status(); last_poll = time.monotonic()
                self.check_cancelled()
                self.update(acknowledged=i)
                if i % 25 == 0: self.save(folder)
            with self.operation:
                self.check_cancelled()
                if self.pause_event.is_set(): self.update(phase='draining')
            deadline = time.monotonic() + 90
            while not self.simulate:
                self.check_cancelled()
                status = self.status()
                if status.startswith('<Idle') and self.pause_event.is_set(): break
                if status.startswith(('<Alarm', '<Door')): raise RuntimeError(status)
                if not self.pause_event.is_set(): deadline = time.monotonic() + 90
                elif time.monotonic() > deadline: raise TimeoutError('等待机器完成超时')
                time.sleep(.2)
            if not self.simulate:
                position = re.search(r'MPos:([\d.-]+),([\d.-]+),([\d.-]+)', status)
                # G92 X0 Y0 anchors this job at the physical pen position. Read
                # the controller's temporary offset rather than storing an XY origin.
                parameters = self.command('$#')
                offset = next((list(map(float, m.groups())) for line in parameters
                               if (m := re.fullmatch(r'\[G92:([\d.-]+),([\d.-]+),([\d.-]+)\]', line))), None)
                if not position or offset is None or abs(offset[2]) > .01 or any(abs(float(v)-target) > .01 for v, target in zip(position.groups(), [*offset[:2], 0])):
                    raise RuntimeError('机器已停止，但最终坐标未返回原点：' + status)
            with self.operation:
                with self.tx_lock:
                    self.check_cancelled()
                    if self.simulate: self.update_position([*(self.snapshot()['position'] or [0., 0.])[:2], 0.])
                    self.update(phase='complete', operation_type=None, completed_at=datetime.datetime.now().astimezone().isoformat())
        except MotionCancelled:
            pass
        except Exception as error:
            if self.serial and not self.stop_event.is_set():
                try: self.write(b'!')
                except Exception: pass
            if not self.stop_event.is_set(): self.update(phase='failed', operation_type=None, error=str(error))
        finally:
            if self.stop_event.is_set(): self.finish_stop()
            self.save(folder)
            with self.tx_lock:
                if self.log: self.log.close()
                self.log = None

    def save(self, folder):
        temp = folder / 'state.tmp'
        temp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding='utf8')
        temp.replace(folder / 'state.json')
