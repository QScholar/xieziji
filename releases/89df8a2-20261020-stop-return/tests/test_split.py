"""Real C++ process + Python gateway integration, without physical hardware."""
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from backend.controller_client import ControllerClient
from backend.server import Application, ROOT, create_server
from tests.test_service import FONT_DIR


def controller_binary():
    if os.environ.get('PLOTTER_CONTROLLER_BINARY'):
        return pathlib.Path(os.environ['PLOTTER_CONTROLLER_BINARY']).resolve()
    name = 'plotter-controller.exe' if os.name == 'nt' else 'plotter-controller'
    for path in (ROOT / 'controller' / 'build' / name, ROOT / 'controller' / name):
        if path.is_file():
            return path
    return None


@unittest.skipUnless(controller_binary(), 'Build the C++ controller first for cross-process tests')
class SplitIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / 'controller' / '.testdata'
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.token = 'controller-test-token-123456'
        cls.log = open(pathlib.Path(cls.temp.name) / 'process.log', 'wb')
        cls.process = subprocess.Popen(
            [str(controller_binary()), '--simulate', '--port', str(cls.port), '--jobs-dir', str(pathlib.Path(cls.temp.name) / 'device')],
            env={**os.environ, 'CONTROLLER_TOKEN': cls.token}, stdout=cls.log, stderr=cls.log,
            cwd=ROOT)
        cls.client = ControllerClient(f'http://127.0.0.1:{cls.port}', cls.token)
        try:
            deadline = time.monotonic() + 15
            while True:
                if cls.process.poll() is not None:
                    raise RuntimeError('C++ process exited; inspect process.log')
                try:
                    cls.client.snapshot()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.05)
            cls.app = Application(FONT_DIR, pathlib.Path(cls.temp.name) / 'business', controller=cls.client)
            cls.server = create_server(cls.app, port=0, frontend_origins=['http://localhost:8081'])
            cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.thread.start()
            cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        except BaseException:
            cls.process.terminate(); cls.process.wait(10); cls.log.close(); cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        cls.process.terminate(); cls.process.wait(10); cls.log.close(); cls.temp.cleanup()

    def call(self, path, data=None, headers=None, method=None):
        h = {'Content-Type': 'application/json', **(headers or {})}
        req = urllib.request.Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=h, method=method)
        try:
            return urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as error:
            error.msg = str(error.reason) + ': ' + error.read().decode('utf8', errors='replace')
            raise

    def wait(self, phase):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            s = self.client.snapshot()
            if s['phase'] == phase:
                return s
            self.assertNotEqual(s['phase'], 'failed', s)
            time.sleep(.02)
        self.fail('Controller state timeout: ' + repr(s))

    def test_a_frontend_and_business_are_independent_of_controller(self):
        with self.call('/') as r:
            self.assertIn(b'/config.js', r.read())
        with self.call('/config.js') as r:
            self.assertIn(b'PLOTTER_API_BASE', r.read())
        original = self.app.machine
        self.app.machine = ControllerClient('http://127.0.0.1:1', '')
        try:
            preview = self.app.api('/api/preview', dict(font_id=next(iter(self.app.fonts)), text='Hello'))
            self.assertIn('svg', preview)
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.call('/api/state')
            self.assertEqual(e.exception.code, 503)
        finally:
            self.app.machine = original

    def test_b_authentication_and_cors(self):
        with self.assertRaises(ValueError):
            ControllerClient(f'http://127.0.0.1:{self.port}', '').snapshot()
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/v1/state', headers={'Authorization': 'Bearer ' + self.token, 'Origin': 'http://localhost:8081'})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/v1/state', headers={'Authorization': 'Bearer ' + self.token, 'Host': 'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        with self.call('/api/fonts', headers={'Origin': 'http://localhost:8081'}) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://localhost:8081')
        with self.call('/api/start', headers={'Origin': 'http://localhost:8081'}, method='OPTIONS') as response:
            self.assertEqual(response.status, 204)
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/fonts', headers={'Origin': 'http://evil.example'})
        self.app.token = 'business-test-token-123456'
        try:
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.call('/api/fonts')
            self.assertEqual(e.exception.code, 401)
            with self.call('/api/fonts', headers={'Authorization': 'Bearer ' + self.app.token}) as r:
                self.assertIn('fonts', json.load(r))
        finally:
            self.app.token = ''

    def test_c_real_process_preserves_preview_and_executes_once(self):
        with self.call('/api/ports') as r:
            self.assertEqual(json.load(r)['ports'][0]['device'], 'SIMULATOR')
        with self.call('/api/connect', {'port': 'SIMULATOR'}):
            pass
        with self.call('/api/preview', dict(font_id=next(iter(self.app.fonts)), text='Hello world! ' * 8, seed='split')) as r:
            preview = json.load(r)
        job_id = preview['job_id']
        with self.call('/api/jobs/' + job_id + '/gcode') as r:
            downloaded = r.read()
        with self.call('/api/check', dict(job_id=job_id)):
            pass
        with self.call('/api/start', dict(job_id=job_id, origin_confirmed=True)):
            pass
        with self.call('/api/pause', {}) as r:
            self.assertEqual(json.load(r)['phase'], 'paused')
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/jog', dict(axis='X', distance=1, feed=300))
        with self.call('/api/resume', {}):
            pass
        state = self.wait('complete')
        self.assertEqual(state['acknowledged'], preview['metadata']['commands'])
        folder = pathlib.Path(self.temp.name) / 'device' / job_id
        self.assertEqual((folder / 'job.gcode').read_bytes(), downloaded)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), preview['metadata']['sha256'])
        def motion():
            records = [json.loads(line) for line in (folder / 'serial.jsonl').read_text(encoding='utf8').splitlines()]
            return [bytes.fromhex(v['hex']).decode('ascii') for v in records if v['direction'] == 'tx' and bytes.fromhex(v['hex']).startswith(b'G')]
        self.assertEqual(''.join(motion()).encode(), downloaded)
        before = len(motion())
        with self.call('/api/start', dict(job_id=job_id, origin_confirmed=True)):
            pass
        self.assertEqual(len(motion()), before)
        with self.call('/api/pen', {'position': 'down'}):
            pass
        self.wait('idle')
        self.assertTrue(self.client.snapshot()['pen_top'])
        with self.call('/api/disconnect', {}):
            pass

    def test_d_document_pages_are_independent_and_match_archive_and_serial(self):
        import io
        import zipfile
        with self.call('/api/connect', {'port':'SIMULATOR'}): pass
        data=dict(font_id=next(iter(self.app.fonts)),layout_mode='grid',width=100,height=100,
                  margin=10,rows=4,grid=dict(columns=10),seed='document-integration',
                  pages=[dict(text=r'A $x^2$'),dict(text=r'B $\frac{1}{2}$')])
        with self.call('/api/preview',data) as r: document=json.load(r)
        self.assertEqual(document['page_count'],2)
        with self.call('/api/documents/'+document['document_id']+'/archive') as r:
            archive=zipfile.ZipFile(io.BytesIO(r.read()))
        second_id=document['pages'][1]['job_id']
        for i,page in enumerate(document['pages']):
            job_id=page['job_id']
            if i == 0:
                self.assertFalse((pathlib.Path(self.temp.name)/'device'/second_id).exists())
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)): pass
            self.wait('complete')
            folder=pathlib.Path(self.temp.name)/'device'/job_id
            expected=archive.read(f'page-{i+1:03d}.gcode')
            self.assertEqual((folder/'job.gcode').read_bytes(),expected)
            records=[json.loads(line) for line in (folder/'serial.jsonl').read_text(encoding='utf8').splitlines()]
            sent=b''.join(bytes.fromhex(v['hex']) for v in records if v['direction']=='tx' and bytes.fromhex(v['hex']).startswith(b'G'))
            self.assertEqual(sent,expected)
        with self.call('/api/disconnect',{}): pass

    def test_e_stop_raises_pen_and_returns_to_each_task_origin(self):
        with self.call('/api/connect', {'port':'SIMULATOR'}): pass
        for paused in (False, True):
            with self.call('/api/jog',dict(axis='X',distance=14,feed=300)): pass
            self.wait('idle')
            with self.call('/api/jog',dict(axis='Y',distance=-16,feed=300)): pass
            origin=self.wait('idle')['position']
            with self.call('/api/preview',dict(font_id=next(iter(self.app.fonts)),text='Hello world! '*8,seed='stop-return')) as r:
                preview=json.load(r)
            job_id=preview['job_id']
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)): pass
            deadline=time.monotonic()+5
            while self.client.snapshot()['acknowledged']<6 and time.monotonic()<deadline: time.sleep(.005)
            if paused:
                with self.call('/api/pause',{}): pass
            with self.call('/api/stop',{}) as r:
                self.assertIn(json.load(r)['phase'],('stopping','returning','stopped'))
            state=self.wait('stopped')
            self.assertTrue(state['connected'])
            self.assertTrue(state['returned_to_origin'])
            self.assertTrue(state['pen_top'])
            self.assertEqual(state['position'],origin)
            self.assertEqual(state['task_origin'],origin)
            self.assertLess(state['acknowledged'],state['total'])
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)) as r:
                self.assertEqual(json.load(r)['phase'],'stopped')
            folder=pathlib.Path(self.temp.name)/'device'/job_id
            records=[json.loads(line) for line in (folder/'serial.jsonl').read_text(encoding='utf8').splitlines()]
            sent=[bytes.fromhex(v['hex']) for v in records if v['direction']=='tx']
            hold=sent.index(b'!')
            reset=sent.index(b'\x18',hold)
            z=sent.index(b'$J=G21 G90 G53 Z0 F600\n',reset)
            xy=next(i for i in range(z+1,len(sent)) if sent[i].startswith(b'$J=G21 G90 G53 X'))
            self.assertLess(hold,reset);self.assertLess(reset,z);self.assertLess(z,xy)
            self.assertFalse(any(raw.startswith(b'G1 ') for raw in sent[hold:]))
            with self.call('/api/check',{}): pass
        with self.call('/api/disconnect',{}): pass


if __name__ == '__main__':
    unittest.main()
