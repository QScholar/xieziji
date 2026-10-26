"""Coordinate output, visible axes and controller travel validation."""
import itertools
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET

from backend.coordinates import Coordinates
from backend.layout import generate, coordinate_preview
from tests.legacy_machine import Machine
from tests.test_service import FONT, FakeSerial, test_temp


class CoordinateTests(unittest.TestCase):
    def test_all_origins_and_axis_combinations_match_output_and_guide(self):
        baseline = generate(FONT, dict(text='AB你\nBA', width=210, height=297))
        ns = {'s': 'http://www.w3.org/2000/svg'}
        original_svg = ET.fromstring(baseline['svg'])
        original_points = [p.attrib['points'] for p in original_svg.findall('.//s:polyline', ns)]
        for origin, reverse_x, reverse_y, swap_xy in itertools.product(
                ('top-left', 'top-right', 'bottom-left', 'bottom-right'), (False, True), (False, True), (False, True)):
            settings = dict(origin=origin, reverse_x=reverse_x, reverse_y=reverse_y, swap_xy=swap_xy)
            with self.subTest(**settings):
                result = generate(FONT, dict(text='AB你\nBA', coordinate_system=settings))
                ox = 210 if origin.endswith('right') else 0
                oy = 297 if origin.startswith('bottom') else 0
                expected = []
                for stroke in baseline['paths']:
                    transformed = []
                    for x, y in stroke:
                        a, b = x - ox, y - oy
                        if swap_xy: a, b = b, a
                        transformed.append((round(-a if reverse_x else a, 3), round(-b if reverse_y else b, 3)))
                    expected.append(transformed)
                self.assertEqual(result['paths'], expected)
                output = [tuple(map(float, match.groups())) for line in result['gcode'].splitlines()
                          if (match := re.match(r'G1 X(-?[\d.]+) Y(-?[\d.]+)', line))]
                self.assertEqual(output[:-1], [p for stroke in expected for p in stroke])
                self.assertEqual(output[-1], (0., 0.))
                self.assertTrue(result['gcode'].endswith('G1 Z0 F3000\nG1 X0 Y0 F3000\n'))
                svg = ET.fromstring(result['svg'])
                self.assertEqual([p.attrib['points'] for p in svg.findall('.//s:polyline', ns)], original_points)
                guide = coordinate_preview(dict(coordinate_system=settings))
                self.assertEqual(guide['metadata']['coordinate_system'], settings)
                self.assertEqual(result['metadata']['machine_paper'], [297, 210] if swap_xy else [210, 297])
                vectors = ((0, -1 if reverse_x else 1), (-1 if reverse_y else 1, 0)) if swap_xy else ((-1 if reverse_x else 1, 0), (0, -1 if reverse_y else 1))
                axes = svg.findall('.//s:line', ns)
                self.assertEqual(len(axes), 2)
                for line, (dx, dy) in zip(axes, vectors):
                    self.assertEqual((float(line.attrib['x1']), float(line.attrib['y1'])), (ox, oy))
                    self.assertEqual((float(line.attrib['x2']), float(line.attrib['y2'])), (ox + dx * 20, oy + dy * 20))

    def test_default_is_identity_and_reversed_y_produces_negative_coordinates(self):
        cs = Coordinates.from_data({}, 210, 297)
        self.assertEqual(cs.transform(30, 40), (30, 40))
        result = generate(FONT, dict(text='Hello', coordinate_system=dict(reverse_y=True)))
        self.assertLess(result['metadata']['machine_bounds'][3], 0)
        self.assertIn(' Y-', result['gcode'])
        self.assertEqual(result['metadata']['bounds'][:2], [30, 30])
        self.assertEqual(result['metadata']['axis_directions'], ['向右', '向上'])

    def test_invalid_settings_rejected(self):
        for settings in (None, [], 'left', dict(origin='center'), dict(origin=[]),
                         dict(reverse_x=1), dict(reverse_y='false'), dict(swap_xy=None)):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                generate(FONT, dict(text='A', coordinate_system=settings))
        for data in (dict(width=float('nan')), dict(margin=60, width=80), dict(height=0)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                coordinate_preview(data)

    def test_machine_checks_travel_after_swapping_axes(self):
        class LimitedX(FakeSerial):
            def write(self, raw):
                super().write(raw)
                if raw == b'$$\n':
                    items = []
                    while not self.replies.empty():
                        item = self.replies.get()
                        items.append(item.replace(b'$130=300', b'$130=250'))
                    for item in items: self.replies.put(item)
        machine = Machine()
        machine.serial = LimitedX()
        normal = generate(FONT, dict(text='A'))
        machine.check_origin(normal['metadata'])
        swapped = generate(FONT, dict(text='A', coordinate_system=dict(swap_xy=True)))
        with self.assertRaisesRegex(ValueError, '行程'):
            machine.check_origin(swapped['metadata'])
        self.assertFalse(any(raw.startswith(b'G') for raw in machine.serial.received))

    def test_transformed_job_streams_exact_preview_gcode_to_fake_machine(self):
        result = generate(FONT, dict(text='AB', coordinate_system=dict(reverse_y=True,swap_xy=True,origin='bottom-right')))
        machine = Machine()
        fake = FakeSerial()
        machine.serial = fake
        machine.update(connected=True, phase='idle')
        with test_temp() as folder:
            machine.start('axis-test', pathlib.Path(folder), result)
            machine.worker.join(10)
            self.assertEqual(machine.snapshot()['phase'], 'complete')
            self.assertEqual([line.decode('ascii').strip() for line in fake.received if line.startswith(b'G')], result['gcode'].splitlines())


if __name__ == '__main__':
    unittest.main()
