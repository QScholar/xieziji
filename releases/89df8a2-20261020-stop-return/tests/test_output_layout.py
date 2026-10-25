"""Measured output rectangle, independent margins, and row capacity."""
import unittest
import xml.etree.ElementTree as ET

from backend.layout import generate, coordinate_preview
from tests.test_service import FONT


class OutputLayoutTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(width=150, height=100, margin_left=13, margin_right=17,
                         margin_top=7, margin_bottom=11, rows=4, size=5,
                         spacing=1, text='AB\nAB\nAB\nAB')

    def test_asymmetric_margins_rows_and_preview_match_paths(self):
        result = generate(FONT, self.data)
        m = result['metadata']
        self.assertEqual(m['writable'], [120, 82])
        self.assertAlmostEqual(m['pitch'], 82 / 4)
        self.assertEqual(m['bounds'][:2], [13, 7])
        self.assertLessEqual(m['bounds'][2], 133)
        self.assertLessEqual(m['bounds'][3], 89.001)
        svg = ET.fromstring(result['svg'])
        rect = svg.find('.//*[@id="writable-area"]')
        self.assertEqual([float(rect.get(k)) for k in ('x', 'y', 'width', 'height')], [13, 7, 120, 82])
        self.assertEqual(len(svg.find('.//*[@id="row-guides"]')), 4)
        guide = coordinate_preview(self.data)
        self.assertEqual(guide['metadata']['pitch'], m['pitch'])
        self.assertEqual(guide['metadata']['margins'], m['margins'])

    def test_short_text_keeps_configured_row_pitch(self):
        result = generate(FONT, {**self.data, 'text': 'AB\nAB'})
        self.assertEqual(len(result['metadata']['lines']), 2)
        self.assertEqual(result['metadata']['rows'], 4)
        self.assertLess(result['metadata']['bounds'][3], 50)

    def test_single_row_zero_margins_and_small_measured_area(self):
        data = dict(width=40, height=10, margin=0, rows=1, size=5, text='AB')
        result = generate(FONT, data)
        self.assertEqual(result['metadata']['bounds'][:2], [0, 0])
        with self.assertRaisesRegex(ValueError, '超过设定'):
            generate(FONT, {**data, 'text': 'AB\nAB'})

    def test_invalid_geometry_and_row_count_rejected(self):
        for extra in (dict(rows=0), dict(rows=2.5), dict(rows=20),
                      dict(margin_left=140), dict(margin_bottom=95),
                      dict(width=float('nan')), dict(margin_top=float('inf'))):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                coordinate_preview({**self.data, **extra})
        with self.assertRaisesRegex(ValueError, '超过设定'):
            generate(FONT, {**self.data, 'text': 'AB\n' * 5})

    def test_mixed_font_output_obeys_same_margins_and_rows(self):
        choices = [dict(font_id='fixture', path=FONT, weight=100)]
        result = generate(FONT, self.data, choices)
        self.assertEqual(result['metadata']['bounds'][:2], [13, 7])
        self.assertLessEqual(result['metadata']['bounds'][3], 89.001)
        self.assertEqual(result['metadata']['rows'], 4)
