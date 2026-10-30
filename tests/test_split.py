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

    def test_c_real_process_preserves_preview_and_uses_distinct_execution_ids(self):
        with self.call('/api/ports') as r:
            self.assertEqual(json.load(r)['ports'][0]['device'], 'SIMULATOR')
        with self.call('/api/connect', {'port': 'SIMULATOR'}):
            pass
        with self.call('/api/preview', dict(font_id=next(iter(self.app.fonts)), text='Hello world! ' * 8, seed='split', feed=12000, move_feed=12000)) as r:
            preview = json.load(r)
        job_id = preview['job_id']
        with self.call('/api/jobs/' + job_id + '/gcode') as r:
            downloaded = r.read()
        with self.call('/api/check', dict(job_id=job_id)):
            pass
        with self.call('/api/start', dict(job_id=job_id, origin_confirmed=True)) as r:
            execution_id = json.load(r)['job_id']
        self.assertNotEqual(execution_id, job_id)
        with self.call('/api/state') as r:
            eta = json.load(r)['eta']
            self.assertIsNotNone(eta['remaining_seconds'])
            self.assertGreater(eta['remaining_seconds'], 0)
        with self.call('/api/pause', {}) as r:
            paused = json.load(r)
            self.assertEqual(paused['phase'], 'paused')
            self.assertEqual(paused['eta']['status'], 'paused')
            self.assertIsNone(paused['eta']['estimated_finish_at'])
        # Repeating a start while this preview is active is idempotent.
        with self.call('/api/start', dict(job_id=job_id, origin_confirmed=True)) as r:
            self.assertEqual(json.load(r)['job_id'], execution_id)
        with self.call('/api/speed', dict(percent=63, job_id=execution_id)) as r:
            self.assertEqual(json.load(r)['feed_override_requested'], 63)
        deadline = time.monotonic() + 5
        while self.client.snapshot()['feed_override'] != 63:
            self.assertLess(time.monotonic(), deadline, 'Speed feedback timed out')
            time.sleep(.02)
        self.assertEqual(self.client.snapshot()['phase'], 'paused')
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/speed', dict(percent=201, job_id=execution_id))
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/jog', dict(axis='X', distance=1, feed=300))
        with self.call('/api/resume', {}):
            pass
        state = self.wait('complete')
        self.assertEqual(state['acknowledged'], preview['metadata']['commands'])
        with self.call('/api/state') as r:
            self.assertEqual(json.load(r)['eta']['remaining_seconds'], 0)
        folder = pathlib.Path(self.temp.name) / 'device' / execution_id
        self.assertEqual((folder / 'job.gcode').read_bytes(), downloaded)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), preview['metadata']['sha256'])
        def motion():
            records = [json.loads(line) for line in (folder / 'serial.jsonl').read_text(encoding='utf8').splitlines()]
            return [bytes.fromhex(v['hex']).decode('ascii') for v in records if v['direction'] == 'tx' and bytes.fromhex(v['hex']).startswith(b'G')]
        self.assertEqual(''.join(motion()).encode(), downloaded)
        before = len(motion())
        with self.call('/api/start', dict(job_id=job_id, origin_confirmed=True)) as r:
            second_execution = json.load(r)['job_id']
        self.assertNotIn(second_execution, (job_id, execution_id))
        self.wait('complete')
        second_folder = pathlib.Path(self.temp.name) / 'device' / second_execution
        self.assertEqual((second_folder / 'job.gcode').read_bytes(), downloaded)
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
                self.assertNotIn(second_id, self.app.executions.values())
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)) as r:
                execution_id=json.load(r)['job_id']
            self.wait('complete')
            folder=pathlib.Path(self.temp.name)/'device'/execution_id
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
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)) as r:
                execution_id=json.load(r)['job_id']
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
            folder=pathlib.Path(self.temp.name)/'device'/execution_id
            records=[json.loads(line) for line in (folder/'serial.jsonl').read_text(encoding='utf8').splitlines()]
            sent=[bytes.fromhex(v['hex']) for v in records if v['direction']=='tx']
            hold=sent.index(b'!')
            reset=sent.index(b'\x18',hold)
            z=sent.index(b'$J=G21 G90 G53 Z0 F600\n',reset)
            xy=next(i for i in range(z+1,len(sent)) if sent[i].startswith(b'$J=G21 G90 G53 X'))
            self.assertLess(hold,reset);self.assertLess(reset,z);self.assertLess(z,xy)
            self.assertFalse(any(raw.startswith(b'G1 ') for raw in sent[hold:]))
            with self.call('/api/start',dict(job_id=job_id,origin_confirmed=True)) as r:
                restarted=json.load(r)
            self.assertNotEqual(restarted['job_id'],execution_id)
            self.assertIn(restarted['phase'],('running','draining','complete'))
            with self.call('/api/stop',{}): pass
            self.wait('stopped')
            with self.call('/api/check',{}): pass
        with self.call('/api/disconnect',{}): pass

    def test_f_second_page_executes_after_cache_eviction_and_backend_restart(self):
        import io
        import zipfile
        data=dict(font_id=next(iter(self.app.fonts)),layout_mode='grid',width=100,height=100,
                  margin=10,rows=4,grid=dict(columns=10),seed='restart-pages',
                  pages=[dict(text='AB'),dict(text='BA')])
        with self.call('/api/preview',data) as r:
            document=json.load(r)
        second=document['pages'][1]
        with self.call('/api/jobs/'+second['job_id']+'/gcode') as r:
            expected=r.read()
        for i in range(9):
            self.app.preview(dict(data,pages=[dict(text='A')]))
        self.assertNotIn(document['job_id'],self.app.cache)
        with self.call('/api/connect',{'port':'SIMULATOR'}): pass
        with self.call('/api/start',dict(job_id=document['job_id'],origin_confirmed=True)): pass
        self.wait('complete')
        original=self.server.app
        restored=Application(FONT_DIR,self.app.jobs_dir,controller=self.client)
        self.server.app=restored
        try:
            with self.call('/api/documents/'+document['document_id']+'/archive') as r:
                archive=zipfile.ZipFile(io.BytesIO(r.read()))
            self.assertEqual(archive.read('page-002.gcode'),expected)
            with self.call('/api/check',dict(job_id=second['job_id'])): pass
            with self.call('/api/start',dict(job_id=second['job_id'],origin_confirmed=True)) as r:
                execution=json.load(r)['job_id']
            self.wait('complete')
            folder=pathlib.Path(self.temp.name)/'device'/execution
            self.assertEqual((folder/'job.gcode').read_bytes(),expected)
            records=[json.loads(line) for line in (folder/'serial.jsonl').read_text(encoding='utf8').splitlines()]
            sent=b''.join(bytes.fromhex(v['hex']) for v in records if v['direction']=='tx' and bytes.fromhex(v['hex']).startswith(b'G'))
            self.assertEqual(sent,expected)
            self.assertEqual(hashlib.sha256(sent).hexdigest(),second['metadata']['sha256'])
        finally:
            self.server.app=original
            with self.call('/api/disconnect',{}): pass


@unittest.skipUnless(controller_binary(), 'Build C++ controller first')
class ContactStartupIntegrationTests(unittest.TestCase):
    def test_session_calibration_and_logical_z_job_over_real_http(self):
        base=ROOT/'controller/.testdata';base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            token='contact-test-token-123456'
            with open(pathlib.Path(folder)/'process.log','wb') as log:
                process=subprocess.Popen([str(controller_binary()),'--simulate','--port',str(port),'--jobs-dir',str(pathlib.Path(folder)/'device')],
                    env={**os.environ,'CONTROLLER_TOKEN':token,'PLOTTER_CONTACT_STARTUP':'1'},stdout=log,stderr=log,cwd=ROOT)
                try:
                    client=ControllerClient(f'http://127.0.0.1:{port}',token)
                    deadline=time.monotonic()+15
                    while True:
                        try:client.snapshot();break
                        except OSError:
                            if time.monotonic()>deadline:raise
                            time.sleep(.05)
                    def wait(phase):
                        deadline=time.monotonic()+15
                        while time.monotonic()<deadline:
                            state=client.snapshot()
                            if state['phase']==phase:return state
                            self.assertNotEqual(state['phase'],'failed',state)
                            time.sleep(.02)
                        self.fail('Contact startup timeout')
                    state=client.connect('SIMULATOR')
                    self.assertFalse(state['calibrated']);self.assertEqual(state['position'][2],0)
                    with self.assertRaises(ValueError):client.set_pen('down')
                    with self.assertRaises(ValueError):client.control('prepare',dict(power_cycled=True))
                    client.control('prepare',dict(power_cycled=True,pen_on_paper=True,bottom_confirmed=True))
                    state=wait('idle');self.assertTrue(state['calibrated']);self.assertEqual(state['logical_z'],0)
                    self.assertEqual(state['position'][2],-13.5)
                    app=Application(FONT_DIR,pathlib.Path(folder)/'business',controller=client)
                    preview=app.preview(dict(font_id=next(iter(app.fonts)),text='Hello',device_control=state['device_control']))
                    app.api('/api/start',dict(job_id=preview['job_id'],origin_confirmed=True))
                    final=wait('complete');self.assertEqual(final['logical_z'],0)
                    client.disconnect();state=client.connect('SIMULATOR')
                    self.assertFalse(state['calibrated']);self.assertIsNone(state['logical_z'])
                finally:
                    process.terminate();process.wait(10)


if __name__ == '__main__':
    unittest.main()
