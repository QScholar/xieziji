"""Restart the controller service and reconnect after a serial power fault.

A serial-level fault (the controller's 500 ms status poller losing the device)
puts the controller into its locked `failed` phase: the API then refuses both
disconnect and connect until the process restarts. This watchdog watches for
that state, restarts the scheduled controller task, and reconnects the last
port once the device re-enumerates after power returns.
"""
import pathlib
import subprocess
import threading
import time
from urllib.parse import urlparse


class ControllerWatchdog:
    def __init__(self, client, profiles, jobs_dir, task='Xieziji-Controller',
                 interval=5., stable_seconds=12., reconnect_interval=5., reconnect_deadline=600.,
                 note=None):
        self.client = client
        self.profiles = profiles
        self.jobs_dir = pathlib.Path(jobs_dir)
        self.task = task
        self.interval = interval
        self.stable_seconds = stable_seconds
        self.reconnect_interval = reconnect_interval
        self.reconnect_deadline = reconnect_deadline
        self.note = note or (lambda message: print(f'controller-watchdog: {time.strftime("%m-%d %H:%M:%S")} {message}', flush=True))
        self.stopped = threading.Event()
        self.worker = None

    def start(self):
        self.worker = threading.Thread(target=self._run, name='controller-watchdog', daemon=True)
        self.worker.start()

    def stop(self):
        self.stopped.set()

    def _run(self):
        fault_since = None
        cooldown = 0.
        while not self.stopped.wait(self.interval):
            try:
                state = self.client.snapshot()
            except Exception:
                # Unreachable covers the restart gap this watchdog causes and a
                # crashed process; neither is a serial fault to recover from.
                fault_since = None
                continue
            if not self._is_serial_fault(state):
                fault_since = None
                continue
            now = time.monotonic()
            fault_since = now if fault_since is None else fault_since
            if now - fault_since < self.stable_seconds or now < cooldown:
                continue
            fault_since = None
            try:
                self._recover(state)
            except Exception as error:
                self.note(f'自动恢复失败：{error}')
            cooldown = time.monotonic() + 60.

    @staticmethod
    def _is_serial_fault(state):
        error = state.get('error')
        # Operation errors (jog on a moving machine, unconfirmed origins) also
        # set phase failed; only controller SerialError messages mean the
        # device connection itself died.
        return (state.get('phase') == 'failed' and state.get('connected') is True
                and state.get('operation_type') is None and isinstance(error, str)
                and error.startswith('Serial'))

    def _recover(self, faulted):
        if (self.jobs_dir / '.speed-limit-update-pending').is_file():
            self.note('控制端更新待执行，跳过自动恢复，由更新流程接管控制端')
            return
        port = faulted.get('port') or ''
        profile = self.profiles.all().get(port) if port else None
        control = profile.get('device_control') if profile else None
        self.note(f'串口故障（{faulted.get("error")}）持续 {self.stable_seconds:.0f} 秒，重启控制端任务 {self.task}')
        self._restart_service()
        if not port:
            self.note('控制端已重启；故障状态未记录端口，请在网页端手动连接')
            return
        self._reconnect(port, control)

    def _reconnect(self, port, control):
        self.note(f'等待设备重新上线后重连 {port}')
        deadline = time.monotonic() + self.reconnect_deadline
        last_error = None
        while not self.stopped.is_set():
            try:
                self.client.connect(port, control)
                self.note(f'已重连 {port}，设备恢复在线')
                return
            except Exception as error:
                message = str(error)
                if 'Disconnect the current device first' in message:
                    self.note('设备已被人工连接，停止自动重连')
                    return
                if message != last_error:
                    self.note(f'重连等待中：{message}')
                    last_error = message
            if time.monotonic() >= deadline:
                self.note('等待设备上线超时，停止自动重连；设备上电后可在网页端手动连接')
                return
            self.stopped.wait(self.reconnect_interval)

    def _restart_service(self):
        port = urlparse(getattr(self.client, 'url', '')).port or 8090
        subprocess.run(['schtasks', '/End', '/TN', self.task], capture_output=True, timeout=30)
        self._kill_listener(port)
        subprocess.run(['schtasks', '/Run', '/TN', self.task], capture_output=True, timeout=30)
        deadline = time.monotonic() + 30.
        while time.monotonic() < deadline:
            try:
                self.client.snapshot()
                return
            except Exception:
                time.sleep(.5)
        raise RuntimeError('控制端重启后 30 秒内未恢复监听')

    def _kill_listener(self, port):
        # /End usually stops the whole task tree, but the controller exe can
        # survive it and keep 8090 bound; then /Run would start a second copy.
        try:
            # netstat emits locale-encoded headers; the fields this parses are
            # ASCII, so replacement chars are harmless.
            rows = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                  text=True, encoding='utf-8', errors='replace',
                                  timeout=15, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for row in rows.splitlines():
            fields = row.split()
            if len(fields) >= 5 and fields[3] == 'LISTENING' and fields[1].endswith(f':{port}'):
                subprocess.run(['taskkill', '/PID', fields[4], '/F'], capture_output=True, timeout=15)
