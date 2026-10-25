"""Document pagination, reference grids, formula geometry and cached page bytes."""
import contextlib
import hashlib
import io
import json
import pathlib
import struct
import unittest
import xml.etree.ElementTree as ET
import zipfile

from backend.document import configuration, coordinate_preview, generate_document
from backend.coordinates import Coordinates
from backend.server import Application
from tests.legacy_machine import TestController
from tests.test_service import FONT, FONT_DIR, test_temp
from scripts.generate_demo_font import glyph_record


class DocumentTests(unittest.TestCase):
    def data(self, **extra):
        return dict(layout_mode='notebook', width=100, height=100, margin=10, rows=4,
                    size=5, spacing=.5, seed='document-test', text='AB', **extra)

    def generate(self, **extra):
        data = self.data(); data.update(extra)
        return generate_document(FONT, data)

    def test_automatic_pages_preserve_text_and_explicit_lines(self):
        text = '\n'.join('AB' + str(i) for i in range(13))
        pages = self.generate(text=text)
        self.assertEqual(len(pages), 4)
        self.assertEqual('\n'.join(line for p in pages for line in p['metadata']['lines']), text)
        self.assertEqual([p['metadata']['page_number'] for p in pages], [1,2,3,4])
        self.assertTrue(all(p['metadata']['page_count'] == 4 for p in pages))

    def test_leading_blank_lines_and_spaces_keep_layout_positions(self):
        plain = self.generate(text='AB')[0]
        padded = self.generate(text='\n AB')[0]
        self.assertAlmostEqual(padded['metadata']['bounds'][1] - plain['metadata']['bounds'][1], 20.)
        self.assertGreater(padded['metadata']['bounds'][0], plain['metadata']['bounds'][0])

    def test_explicit_pages_blank_page_and_pagebreak_commands(self):
        pages = self.generate(pages=[{'text':'AB'}, {'text':''}, {'text':r'A\newpage B'}])
        self.assertEqual(len(pages), 4)
        self.assertTrue(pages[1]['metadata']['empty'])
        self.assertEqual([p['metadata']['source_page'] for p in pages], [1,2,3,3])
        self.assertNotIn('Z10', pages[1]['gcode'])

    def test_fixed_page_overflow_is_actionable(self):
        with self.assertRaisesRegex(ValueError, '预设第 1 页内容溢出'):
            self.generate(text='A\n' * 8, pagination='fixed')

    def test_line_slots_use_row_pitch_and_ignore_a_final_line_terminator(self):
        pages=self.generate(rows=3,text='A;\nA;\nA;\n')
        self.assertEqual(len(pages),1)
        self.assertEqual(pages[0]['metadata']['lines'],['A;','A;','A;'])

    def test_existing_spacing_variation_still_applies_to_notebook(self):
        config=dict(enabled=True,vertical=0,scale=0,rotation=0,baseline=0,spacing=1)
        first=self.generate(text='AAAAAA',humanization=config)[0]
        same=self.generate(text='AAAAAA',humanization=config)[0]
        other=self.generate(text='AAAAAA',humanization=config,seed='other')[0]
        self.assertEqual(first['gcode'],same['gcode'])
        self.assertNotEqual(first['gcode'],other['gcode'])

    def test_grid_dimensions_match_reference_and_line_gaps(self):
        c = configuration(dict(layout_mode='grid', width=157, height=215, margin=0, rows=20,
                               grid=dict(columns=20, row_gap=2.9, column_gap=0)))
        self.assertAlmostEqual(c['grid']['cell_width'], 7.85)
        self.assertAlmostEqual(c['grid']['cell_height'], 7.995)
        guide = coordinate_preview(dict(layout_mode='grid', width=157, height=215, margin=0, rows=20))
        self.assertIn('grid-guides', guide['svg'])
        self.assertNotIn('row-guides', guide['svg'])

    def test_grid_wraps_one_character_per_cell_and_preserves_spaces(self):
        pages = self.generate(layout_mode='grid', rows=2, grid=dict(columns=3), text='AB ABAB')
        self.assertEqual(len(pages), 2)
        self.assertEqual([p['metadata']['lines'] for p in pages], [['AB ', 'ABA'], ['B']])
        newline = self.generate(layout_mode='grid', rows=2, grid=dict(columns=3), text='ABA\nBAB')
        self.assertEqual(len(newline), 1)
        self.assertEqual(newline[0]['metadata']['lines'], ['ABA', 'BAB'])

    def test_grid_guides_do_not_draw_unless_requested(self):
        reference = self.generate(layout_mode='grid', rows=2, grid=dict(columns=3), text='AB')[0]
        drawn = self.generate(layout_mode='grid', rows=2, grid=dict(columns=3, draw_lines=True), text='AB')[0]
        self.assertGreater(drawn['metadata']['strokes'], reference['metadata']['strokes'])
        self.assertEqual(drawn['metadata']['bounds'], [10.,10.,90.,90.])
        self.assertIn('grid-guides', reference['svg'])
        self.assertEqual(reference['metadata']['grid']['draw_lines'], False)

    def test_formula_paths_are_reproducible_and_contribute_height(self):
        source = r'AB $x^2 + \frac{1}{2} = \sqrt{4} + \alpha$'
        first, same = self.generate(text=source)[0], self.generate(text=source)[0]
        self.assertEqual(first['gcode'], same['gcode'])
        self.assertEqual(first['metadata']['formula_count'], 1)
        self.assertNotIn('frac', first['gcode'])
        self.assertGreater(first['metadata']['points'], self.generate(text='AB')[0]['metadata']['points'])
        self.assertLessEqual(first['metadata']['bounds'][3], 90.)

    def test_formula_consumes_multiple_line_slots_without_overlap(self):
        source = r'A $$\frac{1}{\frac{2}{3}}$$ B'
        pages = self.generate(text=source, rows=12)
        self.assertTrue(any(p['metadata']['formula_count'] for p in pages))
        for page in pages:
            self.assertLessEqual(page['metadata']['bounds'][3], 90.001)

    def test_formula_is_atomic_in_grid(self):
        pages = self.generate(layout_mode='grid', rows=4, grid=dict(columns=10), text=r'A$x^2+1$B')
        self.assertEqual(sum(p['metadata']['formula_count'] for p in pages), 1)
        self.assertEqual(''.join(line for p in pages for line in p['metadata']['lines']), r'A$x^2+1$B')

    def test_display_formula_has_no_extra_blank_row_and_text_keeps_spaces(self):
        pages=self.generate(text='A\n$$x^2$$\nB',rows=3)
        self.assertEqual(len(pages),1)
        self.assertEqual(pages[0]['metadata']['lines'],['A','$x^2$','B'])
        plain=self.generate(text=r'$\text{AB}$')[0]['metadata']['bounds'][2]
        spaced=self.generate(text=r'$\text{A B}$')[0]['metadata']['bounds'][2]
        self.assertGreater(spaced,plain)

    def test_markdown_import_retains_formula_and_removes_basic_markup(self):
        pages = self.generate(input_format='markdown', text='# Hello\n**AB** [A](https://example.com) $x^2$')
        text = '\n'.join(line for p in pages for line in p['metadata']['lines'])
        self.assertNotIn('**', text); self.assertNotIn('https', text); self.assertNotIn('#', text)
        self.assertIn('$x^2$', text)

    def test_widening_reserves_space_without_distorting_punctuation(self):
        with test_temp() as temp:
            font = pathlib.Path(temp) / 'punctuation.gfont'
            with font.open('wb') as stream:
                stream.write(struct.pack('>I',5))
                with zipfile.ZipFile(stream,'w') as archive:
                    for char, strokes in {'A':[[(0,0),(4,6)]], '。':[[(0,5.8),(.2,6)]]}.items():
                        archive.writestr(str(ord(char)),glyph_record(char,strokes))
            small = generate_document(font,{**self.data(), 'text':'A。A', 'punctuation_width':0})[0]
            wide = generate_document(font,{**self.data(), 'text':'A。A', 'punctuation_width':1})[0]
            self.assertGreater(wide['paths'][-1][0][0],small['paths'][-1][0][0])
            delta = lambda s:(round(s[-1][0]-s[0][0],3),round(s[-1][1]-s[0][1],3))
            for a,b in zip(delta(wide['paths'][1]),delta(small['paths'][1])):
                self.assertAlmostEqual(a,b,delta=.0011)  # G-code rounds absolute coordinates to 0.001 mm.

    def test_invalid_formula_and_geometry_are_rejected(self):
        for source in (r'$\unknown{x}$', '$x', r'$\frac{1}$', r'$x^^2$', r'$\sqrt{$', r'$\sqrt[3]{x}$', '${x$', '$x}$', '$'+('{'*20)+'x'+('}'*20)+'$'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.generate(text=source)
        for extra in (dict(rows=2.5), dict(punctuation_width=float('nan')), dict(layout_mode='other'),
                      dict(layout_mode='grid',grid=dict(columns=100,row_gap=100)),dict(grid=dict(draw_lines=1)),
                      dict(pages=[{}]),dict(text='A'*30001),dict(seed=True)):
            with self.subTest(extra=extra), self.assertRaises((ValueError, TypeError)):
                self.generate(**extra)

    def test_mapped_svg_download_and_metadata_match(self):
        data=self.data();data.update(text=r'AB $\frac{1}{2}$',coordinate_system=dict(reverse_y=True,swap_xy=True))
        result=generate_document(FONT,data)[0]
        svg=ET.fromstring(result['svg']);coords=Coordinates.from_data(data,100,100)
        ink=svg.find('.//*[@id="writing-paths"]')
        points=[tuple(map(float,p.split(','))) for node in ink for p in node.get('points').split()]
        self.assertEqual([coords.transform(*p) for p in points],[p for s in result['paths'] for p in s])
        self.assertEqual(hashlib.sha256(result['gcode'].encode('ascii')).hexdigest(),result['metadata']['sha256'])

    def test_more_than_eight_pages_remain_cached_and_archive_is_exact(self):
        with test_temp() as temp:
            app=Application(FONT_DIR,pathlib.Path(temp),controller=TestController())
            data=self.data();data.update(font_id=next(iter(app.fonts)),pages=[dict(text='AB') for _ in range(10)])
            preview=app.preview(data)
            self.assertEqual(preview['page_count'],10)
            archive=zipfile.ZipFile(io.BytesIO(app.archive(preview['document_id'])))
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['page_count'],10)
            for i,page in enumerate(preview['pages']):
                folder,cached=app.job(page['job_id'])
                self.assertEqual(archive.read(f'page-{i+1:03d}.gcode'),cached['gcode'].encode('ascii'))
                self.assertEqual((folder/'job.gcode').read_bytes(),archive.read(f'page-{i+1:03d}.gcode'))

    def test_blank_page_cannot_start_and_whole_documents_expire_together(self):
        with test_temp() as temp:
            app=Application(FONT_DIR,pathlib.Path(temp),controller=TestController())
            data=self.data();data.update(font_id=next(iter(app.fonts)),pages=[dict(text='AB'),dict(text='')])
            first=app.preview(data)
            with self.assertRaisesRegex(ValueError,'没有可书写'):
                app.api('/api/start',dict(job_id=first['pages'][1]['job_id'],origin_confirmed=True))
            for i in range(8):app.preview(data)
            for page in first['pages']:
                with self.assertRaisesRegex(ValueError,'过期'):app.job(page['job_id'])
            with self.assertRaisesRegex(ValueError,'过期'):app.archive(first['document_id'])


if __name__=='__main__':unittest.main()
