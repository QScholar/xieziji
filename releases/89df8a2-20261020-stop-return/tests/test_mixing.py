from tests.legacy_machine import TestController
"""Weighted assignment, missing-glyph fallback and cached task consistency."""
import hashlib
import json
import pathlib
import struct
import tempfile
import unittest
import zipfile

from backend.layout import generate
from backend.mixing import font_choices
from backend.server import Application
from scripts.generate_demo_font import glyph_record


class MixingTests(unittest.TestCase):
    def setUp(self):
        base = pathlib.Path(__file__).resolve().parent / '.mixdata'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = pathlib.Path(self.temp.name)
        self.a = self.make_font('a', 'AB', 3)
        self.b = self.make_font('b', 'A你', 7)
        self.available = {'a': self.a, 'b': self.b}

    def tearDown(self):
        self.temp.cleanup()

    def make_font(self, name, characters, width):
        path = self.root / (name + '.gfont')
        with path.open('wb') as stream:
            stream.write(struct.pack('>I', 5))
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for char in characters:
                    archive.writestr(str(ord(char)), glyph_record(char, [[(0, 0), (width, 6), (0, 6)]]))
        return path

    def generate(self, text='A' * 1000, weights=(70, 30), **extra):
        data = dict(text=text, font_mix=[dict(font_id='a', weight=weights[0]), dict(font_id='b', weight=weights[1])],
                    seed='stable-test', size=2, pitch=3, width=297, height=297, margin=5)
        data.update(extra)
        choices, primary = font_choices(data, self.available)
        return generate(primary, data, choices)

    def test_weighted_choice_is_repeatable_and_seed_changes_result(self):
        first = self.generate()
        again = self.generate()
        self.assertEqual(first['gcode'], again['gcode'])
        self.assertEqual(first['svg'], again['svg'])
        summary = first['metadata']['font_mix']
        self.assertEqual(sum(c['used'] for c in summary), 1000)
        self.assertAlmostEqual(summary[0]['probability'], .7)
        self.assertGreater(summary[0]['actual_probability'], .65)
        self.assertLess(summary[0]['actual_probability'], .75)
        self.assertNotEqual(first['gcode'], self.generate(seed='another-test')['gcode'])
        narrow = self.generate(width=80)
        assignments = lambda result: [i for line in result['metadata']['character_fonts'] for i in line if i is not None]
        self.assertEqual(assignments(first), assignments(narrow))
        self.assertLessEqual(narrow['metadata']['bounds'][2], 75)
        self.assertGreater(len(narrow['metadata']['lines']), 1)

    def test_missing_glyph_fallback_and_zero_weight_exclusion(self):
        result = self.generate('B你A B你', weights=(1, 1))
        self.assertEqual(result['metadata']['fallback_characters'], 4)
        self.assertEqual(result['metadata']['character_fonts'][0][:2], [0, 1])
        self.assertEqual(sum(c['used'] for c in result['metadata']['font_mix']), 5)
        zero = self.generate(weights=(0, 1))
        self.assertEqual([c['used'] for c in zero['metadata']['font_mix']], [0, 1000])
        with self.assertRaisesRegex(ValueError, '缺少.*B'):
            self.generate('B', weights=(0, 1))
        with self.assertRaisesRegex(ValueError, '缺少'):
            self.generate('😀')

    def test_mixed_wrap_uses_real_glyph_widths_and_keeps_characters(self):
        text = 'AA BBB A你\n\tBA你  ' * 4
        result = self.generate(text, width=80, size=5, pitch=8)
        original = ''.join(c for c in text if not c.isspace())
        rendered = ''.join(c for line in result['metadata']['lines'] for c in line if not c.isspace())
        self.assertEqual(original, rendered)
        for line, assignments in zip(result['metadata']['lines'], result['metadata']['character_fonts']):
            self.assertEqual(len(line), len(assignments))
        x0, y0, x1, y1 = result['metadata']['bounds']
        self.assertGreaterEqual(x0, 5); self.assertGreaterEqual(y0, 5)
        self.assertLessEqual(x1, 75); self.assertLessEqual(y1, 292)

    def test_single_selected_font_matches_original_layout(self):
        data = dict(text='AB AB\nBABA', width=80, margin=10, seed='single')
        choices, primary = font_choices(dict(data, font_mix=[dict(font_id='a', weight=100)]), self.available)
        self.assertEqual(generate(self.a, data)['gcode'], generate(primary, data, choices)['gcode'])

    def test_invalid_weights_selections_and_seeds_rejected(self):
        for weights in ((0, 0), (-1, 1), (float('nan'), 1), (float('inf'), 1), (True, 1), ('70', 30), (1000001, 1), (10**400, 1)):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                self.generate(weights=weights)
        for entries in ([], 'a', [dict(font_id='unknown', weight=1)], [dict(font_id='a', weight=1)] * 2,
                        [dict(font_id='a', weight=1)] * 17, [None]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                font_choices(dict(font_mix=entries), self.available)
        for seed in ('', 'x' * 65, [], True, None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                self.generate(seed=seed)

    def test_preview_download_and_simulated_machine_keep_same_random_result(self):
        from tests.test_service import FakeSerial
        app = Application(self.root, self.root / 'jobs', controller=TestController())
        ids = {path.stem: key for key, path in app.fonts.items()}
        data = dict(text='AB A你AB', seed='cached-job',
                    font_mix=[dict(font_id=ids['a'], weight=70), dict(font_id=ids['b'], weight=30)])
        preview = app.preview(data)
        folder, cached = app.job(preview['job_id'])
        self.assertEqual(hashlib.sha256(cached['gcode'].encode('ascii')).hexdigest(), preview['metadata']['sha256'])
        self.assertEqual((folder / 'job.gcode').read_text(encoding='ascii'), cached['gcode'])
        self.assertEqual(json.loads((folder / 'metadata.json').read_text(encoding='utf8'))['font_mix'], preview['metadata']['font_mix'])
        fake = FakeSerial()
        app.machine.serial = fake
        app.machine.update(connected=True, phase='idle')
        app.api('/api/start', dict(job_id=preview['job_id'], origin_confirmed=True))
        app.machine.worker.join(10)
        self.assertEqual(app.machine.snapshot()['phase'], 'complete')
        self.assertEqual([line.decode('ascii').strip() for line in fake.received if line.startswith(b'G')], cached['gcode'].splitlines())


if __name__ == '__main__':
    unittest.main()
