"""Manual control isolation and cancellation without physical hardware."""
import json
import pathlib
import re
import threading
import time
import unittest
import urllib.error
import urllib.request

from backend.layout import generate
from tests.legacy_machine import Machine, TestController
from backend.server import Application, create_server
from tests.test_service import FONT, FONT_DIR, FakeSerial, test_temp


class JogSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.jogging = False
        self.target = [0., 0., 0.]
        self.steps = 0
        self.started = threading.Event()

    def write(self, raw):
        if raw.startswith(b'$J='):
            self.received.append(raw)
            self.target = list(map(float, self.position.split(',')))
            absolute = b'G90' in raw
            for axis, value in re.findall(r'([XYZ])(-?[\d.]+)', raw.decode()):
                index = 'XYZ'.index(axis)
                self.target[index] = float(value) if absolute else self.target[index] + float(value)
            self.steps = 0
            self.jogging = True
            self.started.set()
            self.replies.put(b'ok\r\n')
            return
        if raw == b'\x85':
            self.received.append(raw)
            self.jogging = False
            return
        if raw == b'?' and self.jogging:
            self.received.append(raw)
            self.steps += 1
            if self.steps >= 4:
                self.position = ','.join(f'{v:.3f}' for v in self.target)
                self.jogging = False
            phase = 'Jog' if self.jogging else 'Idle'
            self.replies.put(f'<{phase}|MPos:{self.position}|FS:0,0>\r\n'.encode())
            return
        super().write(raw)


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.machine = Machine()
        self.fake = JogSerial()
        self.machine.serial = self.fake
        self.machine.update(connected=True, phase='idle')
        self.temp = test_temp()
        self.folder = pathlib.Path(self.temp.name)

    def tearDown(self):
        if self.machine.active():
            if self.machine.snapshot()['operation_type'] == 'writing': self.machine.stop()
            elif self.machine.snapshot()['operation_type'] == 'manual': self.machine.cancel_manual(True)
            self.machine.worker.join(5)
        self.temp.cleanup()

    def test_jog_positive_negative_distance_and_pen_absolute_machine_z(self):
        for axis, distance in (('X', 1.5), ('X', -.5), ('Y', -2)):
            self.machine.jog(axis, distance, 300)
            self.machine.worker.join(3)
            self.assertEqual(self.machine.snapshot()['phase'], 'idle')
        self.assertEqual(self.machine.snapshot()['position'], [1., -2., 0.])
        for _ in range(2):
            self.machine.set_pen('down')
            self.machine.worker.join(3)
            self.assertEqual(self.machine.snapshot()['position'], [1., -2., 0])
            self.assertEqual(self.machine.snapshot()['pen'], 'up')
            self.assertTrue(self.machine.snapshot()['pen_top'])
        before = len(self.fake.received)
        self.machine.set_pen('up')
        self.assertFalse(any(raw.startswith(b'$J=') for raw in self.fake.received[before:]))
        commands = [raw.decode().strip() for raw in self.fake.received if raw.startswith(b'$J=')]
        self.assertEqual(commands, ['$J=G21 G91 X1.500 F300', '$J=G21 G91 X-0.500 F300', '$J=G21 G91 Y-2.000 F300'] + ['$J=G21 G90 G53 Z10 F600', '$J=G21 G90 G53 Z0 F600'] * 2)
        self.assertFalse(any(raw.startswith(b'G') for raw in self.fake.received))

    def test_invalid_manual_inputs_never_send_motion(self):
        for distance in (None, True, '1', 0, .001, 21, -21, float('nan'), float('inf'), 10**400):
            with self.subTest(distance=distance), self.assertRaises(ValueError): self.machine.jog('X', distance)
        for feed in (None, True, '300', 49, 1001, float('nan'), float('inf'), 10**400):
            with self.subTest(feed=feed), self.assertRaises(ValueError): self.machine.jog('Y', 1, feed)
        for axis in ('Z', 'X1\nG0', None, []):
            with self.subTest(axis=axis), self.assertRaises(ValueError): self.machine.jog(axis, 1)
        for position in (None, 'G1 Z100', []):
            with self.assertRaises(ValueError): self.machine.set_pen(position)
        self.assertEqual(self.fake.received, [])

    def test_manual_controls_rejected_during_writing_and_paused(self):
        result = generate(FONT, dict(text='Hello world! ' * 8))
        self.machine.start('lock-test', self.folder, result)
        for paused in (False, True):
            if paused: self.machine.pause()
            for callback in (lambda: self.machine.jog('X', 1), lambda: self.machine.set_pen('up'),
                             lambda: self.machine.cancel_manual(), lambda: self.machine.cancel_manual(True)):
                with self.assertRaises(ValueError): callback()
            self.assertFalse(any(raw.startswith(b'$J=') for raw in self.fake.received))
        self.machine.stop()
        self.machine.worker.join(3)
        self.assertEqual(self.machine.snapshot()['phase'], 'stopped')

    def test_manual_pause_or_stop_cancel_jog_and_preserve_connection(self):
        for terminate in (False, True):
            self.fake.started.clear()
            self.machine.jog('X', 10)
            self.assertTrue(self.fake.started.wait(1))
            self.machine.cancel_manual(terminate)
            self.machine.worker.join(3)
            self.assertEqual(self.machine.snapshot()['phase'], 'idle')
            self.assertTrue(self.machine.snapshot()['connected'])
            self.assertIn(b'\x85', self.fake.received)
            self.assertNotIn(b'\x18', self.fake.received)
            with self.assertRaises(ValueError): self.machine.resume()

    def test_terminate_writing_clears_queue_and_never_sends_later_gcode(self):
        for when in ('running', 'paused', 'draining'):
            with self.subTest(when=when):
                machine = Machine()
                fake = FakeSerial()
                machine.serial = fake
                machine.update(connected=True, phase='idle')
                folder = self.folder / when
                folder.mkdir()
                result = generate(FONT, dict(text='Hello world!'))
                machine.start(when, folder, result)
                if when == 'paused': machine.pause()
                if when == 'draining':
                    deadline = time.monotonic() + 3
                    while machine.snapshot()['phase'] != 'draining' and time.monotonic() < deadline: time.sleep(.005)
                    self.assertEqual(machine.snapshot()['phase'], 'draining')
                machine.stop()
                machine.worker.join(3)
                self.assertFalse(machine.active())
                self.assertEqual(machine.snapshot()['phase'], 'stopped')
                self.assertFalse(machine.snapshot()['connected'])
                self.assertIn(b'!', fake.received)
                reset = fake.received.index(b'\x18')
                self.assertFalse(any(raw.startswith(b'G') for raw in fake.received[reset + 1:]))
                with self.assertRaises(ValueError): machine.resume()
                self.assertEqual(json.loads((folder / 'state.json').read_text(encoding='utf8'))['phase'], 'stopped')

    def test_manual_origin_adjustment_preserves_preview(self):
        app = Application(FONT_DIR, self.folder / 'jobs', controller=TestController(True))
        app.machine.connect('SIMULATOR')
        data = dict(font_id=next(iter(app.fonts)), text='Hello')
        preview = app.preview(data)
        app.machine.jog('X', 5)
        app.machine.worker.join(3)
        app.api('/api/start', dict(job_id=preview['job_id'], origin_confirmed=True))
        app.machine.worker.join(5)
        self.assertEqual(app.machine.snapshot()['phase'], 'complete')

    def test_conflicting_manual_operations_and_writing_start_rejected(self):
        self.machine.jog('X', 10)
        self.assertTrue(self.fake.started.wait(1))
        with self.assertRaises(ValueError): self.machine.jog('Y', 1)
        with self.assertRaises(ValueError): self.machine.set_pen('down')
        with self.assertRaises(ValueError): self.machine.start('conflict', self.folder, generate(FONT, dict(text='A')))
        with self.assertRaises(ValueError): self.machine.disconnect()
        self.machine.cancel_manual(True)
        self.machine.worker.join(3)


class ManualHTTPTests(unittest.TestCase):
    def test_manual_http_routes_and_cross_origin_lock(self):
        with test_temp() as temp:
            app = Application(FONT_DIR, temp, controller=TestController(True))
            server = create_server(app, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def call(path, data, headers=None):
                h = {'Content-Type':'application/json'}
                h.update(headers or {})
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                                                 data=json.dumps(data).encode(), headers=h)
                with urllib.request.urlopen(request, timeout=5) as response: return json.loads(response.read())
            try:
                call('/api/connect', dict(port='SIMULATOR'))
                with self.assertRaises(urllib.error.HTTPError) as failure: call('/api/jog', dict(axis='X',distance=1), {'Origin':'https://example.com'})
                self.assertEqual(failure.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as failure: call('/api/jog', dict(axis='X',distance=100))
                self.assertEqual(failure.exception.code, 409)
                call('/api/jog', dict(axis='X',distance=.1))
                app.machine.worker.join(3)
                self.assertEqual(app.machine.snapshot()['position'], [.1, 0., 0.])
                call('/api/pen', dict(position='down'))
                app.machine.worker.join(3)
                self.assertEqual(app.machine.snapshot()['pen'], 'up')
                self.assertTrue(app.machine.snapshot()['pen_top'])
                call('/api/jog', dict(axis='Y',distance=10))
                call('/api/manual-stop', {})
                app.machine.worker.join(3)
                self.assertEqual(app.machine.snapshot()['phase'], 'idle')
            finally:
                if app.machine.active(): app.machine.worker.join(5)
                server.shutdown()
                server.server_close()


if __name__ == '__main__': unittest.main()
