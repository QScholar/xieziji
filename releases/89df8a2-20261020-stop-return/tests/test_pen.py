"""Fresh-state top guards and repeatable bounded manual pen cycles."""
import threading
import unittest

from tests.legacy_machine import Machine
from tests.test_manual import JogSerial


class PenTests(unittest.TestCase):
    def setUp(self):
        self.machine = Machine()
        self.fake = JogSerial()
        self.machine.serial = self.fake
        self.machine.update(connected=True, phase='idle')

    def tearDown(self):
        if self.machine.active():
            self.machine.cancel_manual(True)
            self.machine.worker.join(3)

    def commands(self):
        return [raw.decode().strip() for raw in self.fake.received if raw.startswith(b'$J=')]

    def test_already_at_top_up_refreshes_status_without_sending_motion(self):
        self.machine.update_position([0., 0., 10.])
        self.fake.position = '0.000,0.000,0.000'
        result = self.machine.set_pen('up')
        self.assertTrue(result['pen_top'])
        self.assertEqual(result['position'][2], 0)
        self.assertEqual(result['motion_revision'], 0)
        self.assertIsNone(self.machine.worker)
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.fake.received, [b'?'])

    def test_cached_top_cannot_hide_new_nonzero_controller_position(self):
        self.machine.update_position([0., 0., 0.])
        self.fake.position = '0.000,0.000,5.000'
        self.machine.set_pen('up')
        self.machine.worker.join(3)
        self.assertEqual(self.commands(), ['$J=G21 G90 G53 Z0 F600'])
        self.assertTrue(self.machine.snapshot()['pen_top'])

    def test_repeated_down_without_manual_up_always_returns_to_zero(self):
        self.fake.position = '0.000,0.000,10.000'
        for _ in range(3):
            self.machine.set_pen('down')
            self.machine.worker.join(3)
            self.assertEqual(self.machine.snapshot()['phase'], 'idle')
            self.assertEqual(self.machine.snapshot()['position'][2], 0)
            self.assertTrue(self.machine.snapshot()['pen_top'])
        self.assertEqual(self.commands(), ['$J=G21 G90 G53 Z0 F600'] + ['$J=G21 G90 G53 Z10 F600', '$J=G21 G90 G53 Z0 F600'] * 3)

    def test_idle_refresh_reads_current_controller_position(self):
        self.machine.update_position([1., 2., 10.])
        self.fake.position = '1.000,2.000,0.000'
        state = self.machine.refresh_status()
        self.assertEqual(state['position'], [1., 2., 0.])
        self.assertTrue(state['pen_top'])
        self.assertEqual(self.commands(), [])

    def test_automatic_controller_return_does_not_trigger_another_up(self):
        class AutomaticReturn(JogSerial):
            def write(self, raw):
                if raw == b'?' and self.jogging and self.steps == 3 and self.target[2] == 10:
                    self.target[2] = 0
                super().write(raw)
        self.fake = AutomaticReturn()
        self.machine.serial = self.fake
        self.machine.set_pen('down')
        self.machine.worker.join(3)
        self.assertEqual(self.commands(), ['$J=G21 G90 G53 Z10 F600'])
        self.assertTrue(self.machine.snapshot()['pen_top'])

    def test_background_refresh_never_reads_during_worker_or_other_operation(self):
        class ActiveWorker:
            def is_alive(self): return True
        self.machine.worker = ActiveWorker()
        self.machine.refresh_status()
        self.assertEqual(self.fake.received, [])
        self.machine.worker = None
        held, release = threading.Event(), threading.Event()
        def hold():
            with self.machine.operation:
                held.set()
                release.wait(2)
        owner = threading.Thread(target=hold)
        owner.start()
        try:
            self.assertTrue(held.wait(1))
            self.machine.refresh_status()
            self.assertEqual(self.fake.received, [])
        finally:
            release.set()
            owner.join(2)

    def test_cancelled_cycle_does_not_send_automatic_return_motion(self):
        self.machine.set_pen('down')
        self.assertTrue(self.fake.started.wait(1))
        self.machine.cancel_manual(True)
        self.machine.worker.join(3)
        self.assertEqual(self.commands(), ['$J=G21 G90 G53 Z10 F600'])
        self.assertIn(b'\x85', self.fake.received)


if __name__ == '__main__': unittest.main()
