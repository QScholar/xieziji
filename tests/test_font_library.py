"""Font samples must show the requested font, preserve jobs, and reject stale catalogs."""
import hashlib
import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from unittest.mock import patch

from backend.font_library import FontLibrary, vector_sample
from backend.server import Application, create_server
from tests.test_service import FONT, FONT_DIR, test_temp


class FontLibraryTests(unittest.TestCase):
    def setUp(self):
        self.font_id = hashlib.sha256(FONT.name.encode()).hexdigest()[:16]
        self.library = FontLibrary({self.font_id: FONT})

    def test_actual_paths_missing_boxes_and_safe_xml(self):
        value = vector_sample(FONT, 'AB😀')
        svg = ET.fromstring(value['svg'])
        self.assertGreater(len(list(svg.iter('{http://www.w3.org/2000/svg}polyline'))), 0)
        self.assertEqual(value['missing'], ['😀'])
        self.assertEqual(len(list(svg.iter('{http://www.w3.org/2000/svg}rect'))), 1)
        self.assertNotIn('<text', value['svg'])
        ET.fromstring(vector_sample(FONT, '<&')['svg'])

    def test_validation_and_cache_by_text(self):
        for ids, text in [([], 'A'), ([self.font_id]*2, 'A'), (['../font.gfont'], 'A'),
                          ([self.font_id], ''), ([self.font_id], '\n'), ([self.font_id], 'A'*25)]:
            with self.assertRaises(ValueError):
                self.library.previews(dict(font_ids=ids, text=text))
        with patch('backend.font_library.vector_sample', wraps=vector_sample) as render:
            a = self.library.previews(dict(font_ids=[self.font_id], text='A'))
            self.assertEqual(a, self.library.previews(dict(font_ids=[self.font_id], text='A')))
            self.library.previews(dict(font_ids=[self.font_id], text='B'))
            self.assertEqual(render.call_count, 2)

    def test_catalog_excludes_recommendations_with_changed_files(self):
        with test_temp() as temp:
            report = pathlib.Path(temp)/'catalog.json'
            info = dict(id=self.font_id, file=FONT.name, bytes=FONT.stat().st_size, glyph_count=10)
            data = dict(fonts=[info], sample_text='AB', recommendations=[dict(id='one', mix=[dict(font_id=self.font_id)])])
            report.write_text(json.dumps(data), encoding='utf8')
            library = FontLibrary({self.font_id: FONT}, report)
            self.assertEqual(len(library.recommendations()['recommendations']), 1)
            data['fonts'][0]['bytes'] += 1
            report.write_text(json.dumps(data), encoding='utf8')
            self.assertEqual(library.recommendations()['recommendations'], [])
            report.unlink()
            self.assertEqual(library.recommendations()['recommendations'], [])

    def test_http_preview_is_authenticated_and_never_contacts_controller(self):
        class NoMachineCalls:
            def __getattr__(self, name):
                raise AssertionError('Font browsing contacted controller: '+name)
        with test_temp() as temp:
            app = Application(FONT_DIR, temp, token='font-test-token', controller=NoMachineCalls())
            server = create_server(app, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
            base = 'http://127.0.0.1:'+str(server.server_port)
            def request(path, data=None, authenticated=True):
                headers = {'Content-Type':'application/json'}
                if authenticated: headers['Authorization']='Bearer font-test-token'
                body = json.dumps(data).encode() if data is not None else None
                with urllib.request.urlopen(urllib.request.Request(base+path, data=body, headers=headers)) as response:
                    return response.read()
            try:
                for path, body in [('/api/font-recommendations', None), ('/api/font-previews', dict(font_ids=[self.font_id], text='AB'))]:
                    with self.assertRaises(urllib.error.HTTPError) as error:request(path, body, False)
                    self.assertEqual(error.exception.code, 401)
                    self.assertIsInstance(json.loads(request(path, body)), dict)
                self.assertIn(b'FontBrowser', request('/font-library.js'))
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/font-previews', dict(font_ids=['invalid']))
                self.assertEqual(error.exception.code, 409)
                self.assertEqual([p.name for p in pathlib.Path(temp).iterdir() if p.name != '.workspaces'], [])
                self.assertFalse(app.cache)
            finally:
                server.shutdown();server.server_close();worker.join(2)


if __name__ == '__main__': unittest.main()
