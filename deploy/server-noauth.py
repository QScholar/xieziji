"""Local HTTP service, stdlib + optional pyserial. Run: python -m plotter.server."""
import argparse
import collections
import hashlib
import hmac
import json
import os
import pathlib
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from plotter.layout import generate
from plotter.machine import Machine

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = pathlib.Path(__file__).resolve().parent / 'static'


class Application:
    def __init__(self, fonts, jobs, simulate=False, token=''):
        self.fonts_dir = pathlib.Path(fonts).resolve()
        self.jobs_dir = pathlib.Path(jobs).resolve()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.fonts = {hashlib.sha256(p.name.encode('utf8')).hexdigest()[:16]: p
                      for p in sorted(self.fonts_dir.glob('*.gfont'))}
        if not self.fonts: raise ValueError('字体目录中没有 .gfont 文件')
        self.machine = Machine(simulate)
        self.token = token
        self.cache = collections.OrderedDict()
        self.lock = threading.Lock()

    def preview(self, data):
        font_id = data.get('font_id')
        if not isinstance(font_id, str) or font_id not in self.fonts:
            raise ValueError('请选择列表中的字体')
        result = generate(self.fonts[font_id], data)
        job_id = uuid.uuid4().hex
        folder = self.jobs_dir / job_id
        folder.mkdir()
        (folder / 'job.gcode').write_text(result['gcode'], encoding='ascii')
        (folder / 'preview.svg').write_text(result['svg'], encoding='utf8')
        (folder / 'metadata.json').write_text(json.dumps(result['metadata'], ensure_ascii=False, indent=2), encoding='utf8')
        with self.lock:
            self.cache[job_id] = (folder, result)
            while len(self.cache) > 8: self.cache.popitem(last=False)
        return dict(job_id=job_id, metadata=result['metadata'], svg=result['svg'])

    def job(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('任务编号无效')
        with self.lock:
            if job_id not in self.cache: raise ValueError('预览已过期，请重新生成')
            return self.cache[job_id]

    def api(self, path, data=None):
        if path == '/api/fonts':
            return dict(fonts=[dict(id=k, name=''.join(c for c in p.stem if ord(c) >= 32 and ord(c) != 127))
                               for k, p in self.fonts.items()])
        if path == '/api/state': return self.machine.snapshot()
        if path == '/api/ports':
            if self.machine.simulate: return dict(ports=[dict(device='SIMULATOR', description='模拟写字机')])
            from serial.tools import list_ports
            return dict(ports=[dict(device=p.device, description=p.description) for p in list_ports.comports()])
        if path == '/api/preview': return self.preview(data)
        if path == '/api/connect':
            port = data.get('port')
            if not self.machine.simulate:
                from serial.tools import list_ports
                if not isinstance(port, str) or port not in {p.device for p in list_ports.comports()}:
                    raise ValueError('串口不存在，请刷新列表')
            return self.machine.connect(port)
        if path == '/api/disconnect': return self.machine.disconnect()
        if path == '/api/check':
            with self.machine.operation:
                if not self.machine.snapshot()['connected']: raise ValueError('请先连接机器')
                if self.machine.active(): raise ValueError('任务运行中，请等待完成后检查原点')
                if self.machine.snapshot()['phase'] == 'failed': raise ValueError('请先处理控制器中断状态')
                metadata = self.job(data['job_id'])[1]['metadata'] if data.get('job_id') else {'paper': [210, 297]}
                self.machine.check_origin(metadata)
                return self.machine.snapshot()
        if path == '/api/start':
            if data.get('origin_confirmed') is not True:
                raise ValueError('请确认纸张方向、机器零坐标和笔的位置')
            folder, result = self.job(data.get('job_id'))
            if (folder / 'state.json').exists(): raise ValueError('此预览已执行，请重新生成后再写')
            return self.machine.start(data['job_id'], folder, result)
        if path == '/api/pause': return self.machine.pause()
        if path == '/api/resume': return self.machine.resume()
        raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalPlotter/1.0'

    def respond(self, status, content, kind='application/json; charset=utf-8', filename=None):
        if not isinstance(content, bytes):
            content = (json.dumps(content, ensure_ascii=False) if kind.startswith('application/json') else content).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename: self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def allowed(self):
        host = self.headers.get('Host', '')
        if host not in self.server.allowed_hosts:
            self.respond(403, dict(error='请求的 Host 不在允许列表')); return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            self.respond(403, dict(error='拒绝跨站请求')); return False
        if self.server.app.token:
            actual = self.headers.get('Authorization', '')
            if not hmac.compare_digest(actual, 'Bearer ' + self.server.app.token):
                self.respond(401, dict(error='请输入服务访问令牌')); return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path.startswith('/api/'):
                if not self.allowed(): return
                if path.startswith('/api/jobs/') and path.endswith('/gcode'):
                    job_id = path[len('/api/jobs/'):-len('/gcode')]
                    folder, result = self.server.app.job(job_id)
                    self.respond(200, result['gcode'], 'text/plain; charset=utf-8', 'writing.gcode'); return
                if path not in ('/api/fonts', '/api/ports', '/api/state'): raise KeyError(path)
                self.respond(200, self.server.app.api(path)); return
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if path not in files: raise KeyError(path)
            name, kind = files[path]
            self.respond(200, (STATIC / name).read_bytes(), kind + '; charset=utf-8')
        except KeyError: self.respond(404, dict(error='页面或接口不存在'))
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error: self.respond(409, dict(error=str(error)))
        except ImportError: self.respond(503, dict(error='串口依赖未安装，请运行 pip install -r plotter/requirements.txt'))

    def do_POST(self):
        if not self.allowed(): return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.respond(415, dict(error='需要 application/json')); return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536: raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError('需要 JSON 对象')
            path = urlsplit(self.path).path
            if path not in ('/api/preview', '/api/connect', '/api/disconnect', '/api/check', '/api/start', '/api/pause', '/api/resume'):
                raise KeyError(path)
            self.respond(200, self.server.app.api(path, data))
        except KeyError: self.respond(404, dict(error='接口不存在'))
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error: self.respond(409, dict(error=str(error)))
        except ImportError: self.respond(503, dict(error='串口依赖未安装，请运行 pip install -r plotter/requirements.txt'))


def create_server(app, host='127.0.0.1', port=8080, extra_hosts=(), extra_ports=()):
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app
    actual_port = server.server_port
    server.allowed_hosts = {f'{h}:{p}' for h in ('127.0.0.1', 'localhost', host, *extra_hosts)
                            for p in (actual_port, *extra_ports)}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--font-dir', type=pathlib.Path, default=ROOT / 'gcodeFonts')
    parser.add_argument('--jobs-dir', type=pathlib.Path, default=ROOT / 'plotter' / 'jobs')
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--allow-unauthenticated', action='store_true', help='Explicitly allow network access without a token')
    parser.add_argument('--allow-host', action='append', default=[], help='LAN hostname/IP without port')
    parser.add_argument('--allow-origin-port', action='append', type=int, default=[], help='Explicit browser-side SSH tunnel port')
    args = parser.parse_args()
    token = os.environ.get('PLOTTER_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost') and len(token) < 16 and not (args.allow_unauthenticated and not token):
        parser.error('局域网监听需要设置至少 16 位的 PLOTTER_TOKEN 环境变量')
    if any(not 1 <= port <= 65535 for port in args.allow_origin_port):
        parser.error('转发端口必须在 1–65535 之间')
    app = Application(args.font_dir, args.jobs_dir, args.simulate, token)
    server = create_server(app, args.host, args.port, args.allow_host, args.allow_origin_port)
    print(f'Local plotter: http://{args.host}:{server.server_port} | fonts={len(app.fonts)} | mode={app.machine.state["mode"]}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if app.machine.active() and app.machine.serial:
            app.machine.write(b'!')
        server.server_close()


if __name__ == '__main__': main()
