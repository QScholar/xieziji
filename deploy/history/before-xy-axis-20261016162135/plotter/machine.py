"""One serial reader, acknowledged writes, and live GRBL feed hold/resume."""
import datetime
import json
import re
import threading
import time


class Machine:
    def __init__(self, simulate=False, serial_factory=None):
        self.simulate = simulate
        self.factory = serial_factory
        self.serial = None
        self.operation = threading.RLock()
        self.tx_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.worker = None
        self.log = None
        self.state = dict(connected=False, mode='simulation' if simulate else 'serial',
                          phase='disconnected', status='', port='', job_id=None, acknowledged=0, total=0, error=None)

    def update(self, **fields):
        with self.state_lock:
            self.state.update(fields)

    def snapshot(self):
        with self.state_lock:
            return dict(self.state)

    def active(self):
        return self.worker is not None and self.worker.is_alive()

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
        if line.startswith('<'): self.update(status=line)
        if line.startswith(('error:', 'ALARM:')): raise RuntimeError(line)
        return line

    def status(self):
        self.write(b'?')
        end = time.monotonic() + 4
        while time.monotonic() < end:
            line = self.read()
            if line.startswith('<'): return line
        raise TimeoutError('控制器状态查询超时')

    def command(self, line, running=False):
        raw = line.encode('ascii') + b'\n'
        if len(raw) > 78: raise ValueError('指令过长')
        self.write(raw)
        deadline = time.monotonic() + 30
        replies = []; last_poll = time.monotonic()
        while True:
            reply = self.read()
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
            if self.simulate:
                self.update(connected=True, port='SIMULATOR', phase='idle', status='<Idle|MPos:0.000,0.000,0.000|FS:0,0>')
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
            self.update(connected=False, phase='disconnected', port='', status='')
            return self.snapshot()

    def check_origin(self, metadata):
        if self.simulate: return {'simulation': True}
        status = self.status()
        if not status.startswith('<Idle'): raise ValueError('机器未空闲：' + status)
        position = re.search(r'MPos:([\d.-]+),([\d.-]+),([\d.-]+)', status)
        if not position or any(abs(float(v)) > .01 for v in position.groups()):
            raise ValueError('机器坐标不是 (0,0,0)，请先在设备端定位纸张左上角并设定原点')
        modal = self.command('$G')
        if not any(re.search(r'\bG54\b', line) for line in modal):
            raise ValueError('工作坐标系不是已验证的 G54')
        parameters = self.command('$#')
        offsets = {m[1]: [float(v) for v in m[2].split(',')] for line in parameters
                   if (m := re.fullmatch(r'\[(G54|G92|TLO):([\d.,-]+)\]', line))}
        if any(k not in offsets or any(abs(v) > .01 for v in offsets[k]) for k in ('G54', 'G92', 'TLO')):
            raise ValueError('工作坐标偏移或刀具偏移不是零，请在设备端处理后重试')
        replies = self.command('$$')
        settings = {int(m[1]): float(m[2]) for line in replies if (m := re.fullmatch(r'\$(\d+)=([\d.]+)', line))}
        limits = {130: metadata['paper'][0], 131: metadata['paper'][1], 132: 10, 110: 3000, 111: 3000, 112: 3000}
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
            self.update(phase='running', job_id=job_id, acknowledged=0,
                        total=result['metadata']['commands'], error=None, started_at=datetime.datetime.now().astimezone().isoformat())
            self.worker = threading.Thread(target=self.run, args=(folder, result), daemon=True)
            self.worker.start()
            return self.snapshot()

    def pause(self):
        with self.operation:
            if not self.active(): raise ValueError('没有运行中的任务')
            if self.snapshot()['phase'] not in ('running', 'draining', 'paused'):
                raise ValueError('当前状态不能暂停')
            self.pause_event.clear()
            if not self.simulate: self.write(b'!')
            self.update(phase='paused')
            return self.snapshot()

    def resume(self):
        with self.operation:
            if not self.active() or self.snapshot()['phase'] != 'paused': raise ValueError('当前任务未暂停')
            if not self.simulate: self.write(b'~')
            self.update(phase='running'); self.pause_event.set()
            return self.snapshot()

    def run(self, folder, result):
        try:
            self.log = (folder / 'serial.jsonl').open('a', encoding='utf8')
            last_poll = time.monotonic()
            for i, line in enumerate(result['gcode'].splitlines(), 1):
                while not self.pause_event.wait(.2):
                    if not self.simulate: self.status()
                if self.simulate:
                    time.sleep(.003)
                else:
                    self.command(line, running=True)
                    if time.monotonic() - last_poll > .5:
                        self.status(); last_poll = time.monotonic()
                self.update(acknowledged=i)
                if i % 25 == 0: self.save(folder)
            with self.operation:
                if self.pause_event.is_set(): self.update(phase='draining')
            deadline = time.monotonic() + 90
            while not self.simulate:
                status = self.status()
                if status.startswith('<Idle') and self.pause_event.is_set(): break
                if status.startswith(('<Alarm', '<Door')): raise RuntimeError(status)
                if not self.pause_event.is_set(): deadline = time.monotonic() + 90
                elif time.monotonic() > deadline: raise TimeoutError('等待机器完成超时')
                time.sleep(.2)
            if not self.simulate:
                position = re.search(r'MPos:([\d.-]+),([\d.-]+),([\d.-]+)', status)
                if not position or any(abs(float(v)) > .01 for v in position.groups()):
                    raise RuntimeError('机器已停止，但最终坐标未返回原点：' + status)
            with self.operation:
                self.update(phase='complete', completed_at=datetime.datetime.now().astimezone().isoformat())
        except Exception as error:
            if self.serial:
                try: self.write(b'!')
                except Exception: pass
            self.update(phase='failed', error=str(error))
        finally:
            self.save(folder)
            with self.tx_lock:
                if self.log: self.log.close()
                self.log = None

    def save(self, folder):
        temp = folder / 'state.tmp'
        temp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding='utf8')
        temp.replace(folder / 'state.json')
