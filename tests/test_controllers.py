"""Multi-controller HTTP routing must never use a shared mutable target."""
import json
import pathlib
import tempfile
import threading
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from backend.server import Application, ROOT, create_server


class Controller:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.state = dict(connected=False, phase='disconnected', job_id=None,
                          operation_type=None, total=0, acknowledged=0)

    def snapshot(self):
        return dict(self.state, device=self.name)

    refresh_status = snapshot

    def ports(self):
        return dict(ports=[dict(device='USB0', description=self.name)])

    def connect(self, port, control=None):
        self.calls.append(('connect', port, control))
        self.state.update(connected=True, phase='idle', port=port)
        return self.snapshot()

    def control(self, action, data):
        self.calls.append((action, data))
        return self.snapshot()


class ControllerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.apps = {}
        for key in ('windows', 'raspberrypi'):
            self.apps[key] = Application(ROOT/'tests/fixtures', pathlib.Path(self.temp.name)/key,
                                         controller=Controller(key))
        self.server = create_server(self.apps['windows'], port=0, controllers={
            key: dict(name=key, app=app, contact_startup=key=='raspberrypi')
            for key, app in self.apps.items()})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, target=None, data=None):
        headers = {'Content-Type': 'application/json'}
        if target is not None:
            headers['X-Plotter-Controller'] = target
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,
            data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_explicit_target_and_public_inventory(self):
        registry = self.call('/api/controllers')
        self.assertEqual([c['id'] for c in registry['controllers']], ['windows', 'raspberrypi'])
        self.assertNotIn('token', json.dumps(registry))
        self.assertNotIn('url', json.dumps(registry))
        for target in (None, 'missing'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.call('/api/connect', target, dict(port='USB0'))
            self.assertEqual(error.exception.code, 409)
        self.call('/api/connect', 'raspberrypi', dict(port='USB0'))
        self.assertEqual(self.apps['windows'].machine.calls, [])
        self.assertEqual(self.call('/api/state', 'raspberrypi')['device'], 'raspberrypi')
        self.assertFalse(self.call('/api/state', 'windows')['connected'])

    def test_profiles_workspaces_and_previews_are_isolated(self):
        self.call('/api/device-profiles', 'windows', dict(port='USB0', name='first'))
        self.call('/api/device-profiles', 'raspberrypi', dict(port='USB0', name='second'))
        for target, name in [('windows','first'), ('raspberrypi','second')]:
            self.assertEqual(self.call('/api/device-profiles',target)['profiles']['USB0']['name'], name)
        font = next(iter(self.apps['windows'].fonts))
        preview = self.call('/api/preview','windows',dict(font_id=font,text='Hello'))
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/jobs/'+preview['job_id']+'/gcode', 'raspberrypi')
        a=self.apps['windows']; b=self.apps['raspberrypi']
        a.machine.state.update(phase='running',job_id='f'*32,operation_type='writing')
        with self.assertRaises(ValueError): a.start_execution(preview['job_id'])
        self.assertEqual(len(b.start_execution(preview['job_id'])),32)
        self.assertEqual(a.machine.state['phase'],'running')

    def test_main_starts_with_a_private_bom_registry(self):
        registry=pathlib.Path(self.temp.name)/'controllers.json'
        registry.write_text(json.dumps([
            dict(id='windows',name='Windows',url='http://127.0.0.1:1',token=''),
            dict(id='raspberrypi',name='Pi',url='http://127.0.0.1:2',token='test-private-token-123456',contact_startup=True)
        ]),encoding='utf-8-sig')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with open(pathlib.Path(self.temp.name)/'boot.log','wb') as log:
            process=subprocess.Popen([sys.executable,'-m','backend.server','--port',str(port),
                '--font-dir',str(ROOT/'tests/fixtures'),'--jobs-dir',str(pathlib.Path(self.temp.name)/'boot'),
                '--controllers-file',str(registry)],cwd=ROOT,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+10
                while True:
                    if process.poll() is not None:self.fail('Backend exited while loading private registry')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/controllers',timeout=1) as response:
                            result=json.load(response)
                        break
                    except OSError:
                        if time.monotonic()>deadline:raise
                        time.sleep(.05)
                self.assertEqual(len(result['controllers']),2)
                self.assertTrue(result['controllers'][1]['contact_startup'])
                self.assertNotIn('test-private-token',json.dumps(result))
            finally:
                process.terminate();process.wait(10)


if __name__ == '__main__': unittest.main()
