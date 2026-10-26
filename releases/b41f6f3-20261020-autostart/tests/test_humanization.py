from tests.legacy_machine import TestController
"""Whole-glyph variation, measured wrapping, bounds and exact cached output."""
import hashlib
import math
import pathlib
import unittest
import xml.etree.ElementTree as ET

from backend.coordinates import Coordinates
from backend.humanization import settings, Variation
from backend.layout import generate, coordinate_preview
from backend.server import Application
from tests.test_service import FONT, FONT_DIR, FakeSerial, test_temp


class HumanizationTests(unittest.TestCase):
    def data(self, **extra):
        data = dict(text='ABABAB ABABAB\nABABAB ABABAB', width=80, height=200,
                    margin=10, rows=12, size=5, spacing=.5, seed='repeatable',
                    humanization=dict(enabled=True))
        data.update(extra)
        return data

    def test_disabled_and_zero_effects_preserve_original_gcode(self):
        data = self.data(humanization={})
        original = generate(FONT, data)
        for config in (dict(enabled=False, rotation=10), dict(enabled=True, vertical=0, scale=0, rotation=0, spacing=0, baseline=0),
                       dict(enabled=True, **{key+'_enabled':False for key in ('vertical','scale','rotation','spacing','baseline')})):
            self.assertEqual(generate(FONT, {**data, 'humanization':config})['gcode'], original['gcode'])

    def test_seed_repeats_paths_and_new_seed_changes_them(self):
        first = generate(FONT, self.data())
        same = generate(FONT, self.data())
        self.assertEqual(first['svg'], same['svg'])
        self.assertEqual(first['gcode'], same['gcode'])
        self.assertNotEqual(first['gcode'], generate(FONT, self.data(seed='new-seed'))['gcode'])
        changes = [v for row in first['metadata']['character_variations'] for v in row if v]
        self.assertGreater(len({v['scale'] for v in changes}), 1)
        for v in changes:
            self.assertTrue(.95 <= v['scale'] <= 1.05)
            self.assertLessEqual(abs(v['rotation']), 2)
            self.assertLessEqual(abs(v['vertical']), .3)
            self.assertLessEqual(abs(v['baseline']), .4)
            self.assertTrue(.3 <= v['gap'] <= .7)

    def test_scale_and_rotation_preserve_shape_and_stroke_order(self):
        token = dict(char='A', font=0, paths=[[(0.,0.),(3.,4.),(6.,0.)]], bounds=(0.,6.), advance=6.)
        model = Variation(settings(dict(humanization=dict(enabled=True, scale=20, rotation=10, spacing=2))), 'shape')
        result = model.glyph(token,5,0)
        self.assertEqual(len(result['paths']), 1)
        self.assertEqual(len(result['paths'][0]), 3)
        for a,b,c,d in zip(token['paths'][0],token['paths'][0][1:],result['paths'][0],result['paths'][0][1:]):
            self.assertAlmostEqual(math.dist(c,d)/math.dist(a,b),result['variation']['scale'])
        self.assertGreaterEqual(result['variation']['gap'],0)
        self.assertGreaterEqual(result['advance'],result['bounds'][1])

    def test_individual_switches_and_smooth_baseline(self):
        config=dict(enabled=True,scale_enabled=False,rotation_enabled=False,vertical_enabled=False,spacing_enabled=False)
        result=generate(FONT,self.data(humanization=config))
        for row in result['metadata']['character_variations']:
            for v in row:
                if v:
                    self.assertEqual(v['scale'],1);self.assertEqual(v['rotation'],0);self.assertEqual(v['vertical'],0);self.assertEqual(v['gap'],.5)
        model=Variation(settings(dict(humanization=config)),'smooth')
        phase=model.line_phase()
        self.assertLessEqual(abs(model.baseline(phase,20.1,60)-model.baseline(phase,20,60)),.4*math.pi*.1/60+1e-9)

    def test_transformed_glyph_widths_wrap_without_losing_text(self):
        text='AB'*50
        result=generate(FONT,self.data(text=text,width=45,size=3,rows=30,height=250,
                        humanization=dict(enabled=True,scale=20,rotation=10,spacing=1)))
        self.assertEqual(''.join(result['metadata']['lines']),text)
        x0,y0,x1,y1=result['metadata']['bounds']
        self.assertGreaterEqual(x0,10);self.assertGreaterEqual(y0,10)
        self.assertLessEqual(x1,35.001);self.assertLessEqual(y1,240.001)
        self.assertGreater(len(result['metadata']['lines']),1)

    def test_overflow_after_variation_is_rejected(self):
        config=dict(enabled=True,vertical=2,scale=0,rotation=0,spacing=0,baseline=0)
        with self.assertRaisesRegex(ValueError,'超出'):
            generate(FONT,self.data(text='AAAAAAAA',width=80,height=5,margin=0,rows=1,humanization=config))

    def test_mixed_font_assignments_do_not_change_with_geometry_rng(self):
        choices=[dict(font_id='a',path=FONT,weight=70),dict(font_id='b',path=FONT,weight=30)]
        varied=generate(FONT,self.data(),choices)
        plain=generate(FONT,self.data(humanization=dict(enabled=False)),choices)
        assignments=lambda r:[i for row in r['metadata']['character_fonts'] for i in row if i is not None]
        self.assertEqual(assignments(varied),assignments(plain))

    def test_invalid_configuration_and_seed_rejected(self):
        for config in (None,[],dict(enabled=1),dict(vertical=True),dict(scale=-1),dict(rotation=11),dict(spacing='1'),
                       dict(baseline=float('nan')),dict(vertical=float('inf')),dict(scale=10**400),dict(rotation_enabled='false')):
            with self.subTest(config=config),self.assertRaises(ValueError):
                coordinate_preview(self.data(humanization=config))
        with self.assertRaises(ValueError):generate(FONT,self.data(seed=True))

    def test_svg_download_and_machine_use_same_varied_points(self):
        with test_temp() as temp:
            app=Application(FONT_DIR,pathlib.Path(temp),controller=TestController())
            font_id=next(k for k,v in app.fonts.items() if v==FONT)
            data=self.data(font_id=font_id,coordinate_system=dict(reverse_y=True,swap_xy=True))
            preview=app.preview(data);folder,cached=app.job(preview['job_id'])
            ns={'s':'http://www.w3.org/2000/svg'}
            svg=ET.fromstring(preview['svg']);coordinates=Coordinates.from_data(data,80,200)
            paper_points=[tuple(map(float,point.split(','))) for stroke in svg.findall('.//s:polyline',ns) for point in stroke.get('points').split()]
            self.assertEqual([coordinates.transform(*p) for p in paper_points],[p for stroke in cached['paths'] for p in stroke])
            self.assertEqual(hashlib.sha256(cached['gcode'].encode('ascii')).hexdigest(),preview['metadata']['sha256'])
            self.assertEqual((folder/'job.gcode').read_text(encoding='ascii'),cached['gcode'])
            fake=FakeSerial();app.machine.serial=fake;app.machine.update(connected=True,phase='idle')
            app.api('/api/start',dict(job_id=preview['job_id'],origin_confirmed=True));app.machine.worker.join(10)
            self.assertEqual(app.machine.snapshot()['phase'],'complete')
            self.assertEqual([raw.decode('ascii').strip() for raw in fake.received if raw.startswith(b'G')],cached['gcode'].splitlines())
