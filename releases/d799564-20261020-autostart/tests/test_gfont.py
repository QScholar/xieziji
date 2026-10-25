import pathlib
import struct
import unittest
from backend.gfont import GFont, parse_glyph

FIXTURE = pathlib.Path(__file__).parent / 'fixtures' / 'fixture.gfont'


class ParserTests(unittest.TestCase):
    def test_generated_glyph_keeps_pen_lifts(self):
        with GFont(FIXTURE) as font:
            glyph = font.glyph('H')
            self.assertEqual(glyph.codepoint, ord('H'))
            self.assertEqual(len(glyph.strokes()), 3)
            self.assertEqual(glyph.strokes()[0], [(0, 0), (0, 6)])
            self.assertTrue(font.glyph('你').coordinates)

    def test_truncated_unknown_nonfinite_records_rejected(self):
        with self.assertRaises(ValueError): parse_glyph(b'\0')
        with self.assertRaises(ValueError): parse_glyph(struct.pack('>HI2fI',65,2,0,0,1)+b'\xff')
        with self.assertRaises(ValueError): parse_glyph(struct.pack('>HI2fI',65,2,float('nan'),0,1)+b'\0')
        with self.assertRaises(ValueError): parse_glyph(struct.pack('>HI2fI',65,2,0,0,2)+b'\0')


if __name__ == '__main__': unittest.main()
