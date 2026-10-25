import pathlib
import tempfile
import unittest

from backend.device_profiles import DeviceProfiles, control_settings, validate_profile
from backend.document import generate_document
from backend.layout import generate
from tests.test_service import FONT_DIR


class DeviceProfileTests(unittest.TestCase):
    def test_profiles_survive_restart_and_ports_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DeviceProfiles(pathlib.Path(folder))
            first = store.save(dict(port='COM5', name='书桌', coordinate_system=dict(swap_xy=True),
                                    device_control=dict(pen_up=-1.25, pen_down=-3.5, controller_type='grbl')))
            second = store.save(dict(port='COM1', name='备用', coordinate_system=dict(reverse_x=True)))
            self.assertEqual(DeviceProfiles(pathlib.Path(folder)).all(), {'COM5': first, 'COM1': second})
            self.assertFalse(second['coordinate_system']['swap_xy'])
            self.assertEqual(first['device_control']['pen_down'], -3.5)

    def test_invalid_motion_parameters_are_rejected(self):
        for value in [dict(pen_up=True), dict(pen_down=float('nan')), dict(pen_up=301),
                      dict(pen_feed=0), dict(pen_down=0), dict(controller_type='unknown')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                control_settings(value)
        for value in [dict(port='COM5', coordinate_system=dict(reverse_x=1)),
                      dict(port='COM5', jog_distance=100), dict(port='', name='a')]:
            with self.assertRaises(ValueError):
                validate_profile(value)

    def test_each_generator_uses_device_z_and_binds_output_to_port(self):
        font = next(FONT_DIR.glob('*.gfont'))
        data = dict(text='Hello', device_port='COM1', device_control=dict(controller_type='grbl', pen_up=-1.25, pen_down=-3.5), feed=2400, move_feed=3000)
        for result in [generate(font, data), *generate_document(font, {**data, 'layout_mode': 'notebook'})]:
            self.assertIn('G1 Z-1.25 F3000', result['gcode'])
            self.assertIn('G1 Z-3.5 F3000', result['gcode'])
            self.assertNotIn('G1 Z10 ', result['gcode'])
            self.assertEqual(result['metadata']['device_port'], 'COM1')
            self.assertEqual(result['metadata']['device_control']['pen_up'], -1.25)
