"""Boundary, HTTP isolation and simulated serial control integration tests."""
import json
import pathlib
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from plotter.layout import generate
from plotter.machine import Machine
from plotter.server import Application, ROOT, create_server

FONT = ROOT / 'gcodeFonts' / 'blood8-2_kvenjoy_blood8.gfont'

# 第三方字体不随仓库分发；缺字体时整组旧版测试跳过。
needs_font = unittest.skipUnless(FONT.exists(), '需要本机 gcodeFonts 真实字体')


def test_temp():
    base = (ROOT / 'plotter' / '.testdata').resolve()
    base.mkdir(exist_ok=True)
    temp = tempfile.TemporaryDirectory(dir=base)
    if not pathlib.Path(temp.name).resolve().is_relative_to(base):
        raise RuntimeError('Test cleanup target is outside the test workspace')
    return temp


class FakeSerial:
    def __init__(self):
        self.received = []; self.replies = queue.Queue(); self.hold = False
        self.position = '0.000,0.000,0.000'; self.offset = '0.000,0.000,0.000'
        self.alarm = False; self.finished = False; self.drain = 0
    def write(self, raw):
        self.received.append(raw)
        if raw == b'!': self.hold = True; return
        if raw == b'~': self.hold = False; return
        if raw == b'?':
            phase = 'Hold:0' if self.hold else 'Run' if self.finished and self.drain < 3 else 'Idle'
            if self.finished: self.drain += 1
            lines = [f'<{phase}|MPos:{self.position}|FS:0,0>']
        elif raw == b'$$\n':
            settings = {3:7,20:0,21:0,22:0,100:80,101:80,102:100,110:5000,111:5000,112:8000,130:300,131:300,132:300}
            lines = [f'${k}={v}' for k, v in settings.items()] + ['ok']
        elif raw == b'$G\n': lines = ['[GC:G1 G54 G17 G21 G90 G94 M5]', 'ok']
        elif raw == b'$#\n': lines = ['[G54:'+self.offset+']','[G92:0.000,0.000,0.000]','[TLO:0.000]','ok']
        elif raw == b'$N\n': lines = ['$N0=', '$N1=', 'ok']
        elif raw.startswith(b'G'):
            time.sleep(.003)
            if raw == b'G1 X0 Y0 F3000\n': self.finished = True
            lines = ['ALARM:1'] if self.alarm and b'Z10' in raw else ['ok']
        else: lines = ['ok']
        for line in lines: self.replies.put((line + '\r\n').encode('ascii'))
    def readline(self):
        try: return self.replies.get(timeout=.01)
        except queue.Empty: return b''
    def close(self): pass


@needs_font
class LayoutTests(unittest.TestCase):
    def test_chinese_and_english_paths_fit_real_margins(self):
        font = ROOT / 'gcodeFonts' / '方正兰亭超细黑体_kvenjoy_奎享科技.gfont'
        result = generate(font, dict(text='你好世界。Hello world!\n中文与英文可以一起排版。', width=210, height=297))
        x0,y0,x1,y1 = result['metadata']['bounds']
        self.assertEqual((x0,y0), (30,30))
        self.assertLessEqual(x1,180); self.assertLessEqual(y1,267)
        self.assertTrue(result['gcode'].endswith('G1 Z0 F3000\nG1 X0 Y0 F3000\n'))
    def test_wrap_long_word_and_overflow_rejected(self):
        result = generate(FONT, dict(text='Learning ' * 30, width=80, margin=10))
        self.assertGreater(len(result['metadata']['lines']), 1)
        with self.assertRaisesRegex(ValueError, '超出'):
            generate(FONT, dict(text='Hello\n' * 80))
    def test_nonfinite_dimensions_and_missing_glyphs_rejected(self):
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError): generate(FONT, dict(text='A', margin=value))
        with self.assertRaisesRegex(ValueError, '缺少'): generate(FONT, dict(text='A😀'))
    def test_chinese_and_english_sizes_scale_independently(self):
        font = ROOT / 'gcodeFonts' / '方正兰亭超细黑体_kvenjoy_奎享科技.gfont'
        settings = dict(width=200, height=200, rows=10, size=6, latin_size=2)
        def height(text):
            b = generate(font, dict(text=text, **settings))['metadata']['bounds']
            return b[3] - b[1]
        self.assertAlmostEqual(height('国'), 6, delta=.7)
        self.assertAlmostEqual(height('A'), 2, delta=.3)
        mixed = generate(font, dict(text='国A', **settings))['metadata']
        self.assertEqual((mixed['size'], mixed['latin_size']), (6., 2.))
        self.assertAlmostEqual(mixed['bounds'][3] - mixed['bounds'][1], 6, delta=.7)
        shared = generate(font, dict(text='国A', width=200, height=200, rows=10, size=4))['metadata']
        self.assertEqual((shared['size'], shared['latin_size']), (4., 4.))
        wider = generate(font, dict(text='A', width=200, height=200, rows=10, size=2, latin_size=6))['metadata']
        self.assertAlmostEqual(wider['bounds'][3] - wider['bounds'][1], 6, delta=.5)
    def test_scoped_fonts_route_each_script_only(self):
        from plotter.mixing import font_choices
        zh = ROOT / 'gcodeFonts' / '1111122_kvenjoy_1223.gfont'
        available = {'zh': zh, 'en': FONT}
        settings = dict(text='中文 English', width=200, height=200, rows=10, size=5, latin_size=5, seed='scoped')
        split = [dict(font_id='zh', path=zh, weight=1., scope='zh'),
                 dict(font_id='en', path=FONT, weight=1., scope='en')]
        result = generate(FONT, settings, split)['metadata']
        self.assertEqual([c['scope'] for c in result['font_mix']], ['zh', 'en'])
        self.assertEqual([c['used'] for c in result['font_mix']], [2, 7])
        self.assertEqual(result['font_mix'][0]['probabilities'], {'zh': 1.})
        self.assertEqual(result['fallback_characters'], 0)
        # Without a scope the entries stay 中英通用 and the routing is unchanged.
        legacy = [dict(c, path=available[c['font_id']]) for c in
                  (dict(font_id='zh', weight=1.), dict(font_id='en', weight=1.))]
        plain = generate(FONT, settings, legacy)['metadata']
        self.assertEqual({c['scope'] for c in plain['font_mix']}, {'all'})
        self.assertNotEqual(plain['sha256'], result['sha256'])
        for mix in ([dict(font_id='en', weight=1., scope='en')], [dict(font_id='zh', weight=1., scope='zh')]):
            with self.assertRaisesRegex(ValueError, '没有可用的中文字体|没有可用的英文字体'):
                generate(FONT, settings, [dict(c, path=available[c['font_id']]) for c in mix])
        with self.assertRaisesRegex(ValueError, '适用范围'):
            font_choices(dict(font_mix=[dict(font_id='zh', weight=1, scope='klingon')]), available)


@needs_font
class MachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_temp(); self.folder = pathlib.Path(self.temp.name)
        self.machine = Machine(); self.fake = FakeSerial(); self.machine.serial = self.fake
        self.machine.update(connected=True, phase='idle')
        self.result = generate(FONT, dict(text='Hello world!'))
    def tearDown(self):
        if self.machine.active():
            self.machine.pause_event.set(); self.machine.worker.join(10)
        self.temp.cleanup()
    def test_nonzero_origin_rejected_without_motion(self):
        self.fake.position = '1.000,0.000,0.000'
        with self.assertRaisesRegex(ValueError, '坐标'): self.machine.start('test',self.folder,self.result)
        self.assertFalse(any(raw.startswith(b'G') for raw in self.fake.received))
    def test_coordinate_offset_rejected_without_motion(self):
        self.fake.offset = '0.000,0.000,2.000'
        with self.assertRaisesRegex(ValueError, '偏移'): self.machine.start('test',self.folder,self.result)
        self.assertFalse(any(raw.startswith(b'G') for raw in self.fake.received))
    def test_pause_resume_and_wait_for_physical_idle(self):
        self.machine.start('test',self.folder,self.result)
        time.sleep(.05); self.machine.pause(); time.sleep(.04)
        count = self.machine.snapshot()['acknowledged']; time.sleep(.08)
        self.assertEqual(self.machine.snapshot()['acknowledged'], count)
        with self.assertRaises(ValueError): self.machine.disconnect()
        with self.assertRaises(ValueError): self.machine.start('second',self.folder,self.result)
        self.machine.resume(); self.machine.worker.join(10)
        state = self.machine.snapshot()
        self.assertEqual(state['phase'], 'complete'); self.assertEqual(state['acknowledged'],state['total'])
        self.assertGreaterEqual(self.fake.drain,4)
        self.assertIn(b'!', self.fake.received); self.assertIn(b'~',self.fake.received)
        self.assertEqual([r.decode('ascii').strip() for r in self.fake.received if r.startswith(b'G')],self.result['gcode'].splitlines())
    def test_alarm_stops_stream_and_holds(self):
        self.fake.alarm = True
        self.machine.start('test',self.folder,self.result); self.machine.worker.join(10)
        self.assertEqual(self.machine.snapshot()['phase'],'failed')
        self.assertEqual(self.fake.received[-1],b'!')
        with self.assertRaises(ValueError): self.machine.start('second',self.folder,self.result)


@needs_font
class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=test_temp()
        cls.app=Application(ROOT/'gcodeFonts',cls.temp.name,True)
        cls.server=create_server(cls.app,port=0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.font_id=next(k for k,v in cls.app.fonts.items() if v==FONT)
    @classmethod
    def tearDownClass(cls):
        if cls.app.machine.active():cls.app.machine.worker.join(10)
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()
    def call(self,path,data=None,headers=None):
        h={'Content-Type':'application/json'};h.update(headers or {})
        request=urllib.request.Request(self.url+path,headers=h,data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(request,timeout=10) as response:return response.read()
    def test_http_preview_export_simulated_write(self):
        preview=json.loads(self.call('/api/preview',dict(font_id=self.font_id,text='Hello world!')))
        self.assertTrue(self.call('/api/jobs/'+preview['job_id']+'/gcode').startswith(b'G21\n'))
        self.call('/api/connect',dict(port='SIMULATOR'))
        self.assertTrue(json.loads(self.call('/api/check',dict(job_id=preview['job_id'])))['connected'])
        with self.assertRaises(urllib.error.HTTPError) as failure:self.call('/api/start',dict(job_id=preview['job_id']))
        self.assertEqual(failure.exception.code,409)
        first=json.loads(self.call('/api/start',dict(job_id=preview['job_id'],origin_confirmed=True)))
        self.app.machine.worker.join(10)
        state=json.loads(self.call('/api/state'));self.assertEqual(state['phase'],'complete')
        # Content stays writable: the same preview runs again from a new origin.
        second=json.loads(self.call('/api/start',dict(job_id=preview['job_id'],origin_confirmed=True)))
        self.assertNotEqual(second['job_id'],first['job_id'])
        self.assertNotEqual(second['job_id'],preview['job_id'])
        self.app.machine.worker.join(10)
        self.assertEqual(json.loads(self.call('/api/state'))['phase'],'complete')
        self.call('/api/disconnect',{})
        with self.assertRaises(urllib.error.HTTPError):self.call('/api/start',dict(job_id=preview['job_id'],origin_confirmed=True))
    def test_cross_origin_host_and_path_traversal_rejected(self):
        for headers in ({'Origin':'https://example.com'},{'Host':'evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as failure:self.call('/api/connect',{},headers)
            self.assertEqual(failure.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError):self.call('/api/preview',dict(font_id='../analysis',text='Hello'))
        with self.assertRaises(urllib.error.HTTPError):self.call('/../analysis/gfont.py')
    def test_wrong_method_and_malformed_input_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):self.call('/api/start')
        with self.assertRaises(urllib.error.HTTPError):self.call('/api/preview',dict(font_id=self.font_id,text='Hello',size=None))
    def test_token_required_when_configured(self):
        self.app.token='test-token-123456789'
        try:
            with self.assertRaises(urllib.error.HTTPError) as failure:self.call('/api/fonts')
            self.assertEqual(failure.exception.code,401)
            self.assertIn(b'fonts',self.call('/api/fonts',headers={'Authorization':'Bearer '+self.app.token}))
        finally:self.app.token=''


if __name__=='__main__':unittest.main()
