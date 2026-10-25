"""Business API and optional frontend hosting. Run: python -m backend.server."""
import argparse
import collections
import hashlib
import hmac
import io
import json
import os
import pathlib
import re
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from backend.layout import generate, coordinate_preview
from backend.controller_client import ControllerClient, ControllerUnavailable
from backend.mixing import font_choices
from backend.document import generate_document, coordinate_preview as document_coordinate_preview
from backend.eta import TaskETA
from backend.font_library import FontLibrary
from backend.device_profiles import DeviceProfiles
from backend.font_updates import FontUpdates

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / 'frontend'


class Application:
    def __init__(self, fonts, jobs, token='', controller=None):
        self.fonts_dir = pathlib.Path(fonts).resolve()
        self.jobs_dir = pathlib.Path(jobs).resolve()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.fonts = {hashlib.sha256(p.name.encode('utf8')).hexdigest()[:16]: p
                      for p in sorted(self.fonts_dir.glob('*.gfont'))}
        if not self.fonts: raise ValueError('字体目录中没有 .gfont 文件')
        self.machine = controller if controller is not None else ControllerClient()
        self.token = token
        self.cache = collections.OrderedDict()
        self.groups = collections.OrderedDict()
        self.documents = {}
        self.lock = threading.Lock()
        self.eta = TaskETA(self.jobs_dir)
        self.font_library = FontLibrary(self.fonts)
        self.font_updates = FontUpdates(self.fonts_dir, self.jobs_dir / '.font-analysis', self._publish_fonts)
        self.device_profiles = DeviceProfiles(self.jobs_dir)

    def _publish_fonts(self, library):
        # Each request gets an immutable font mapping / library generation.
        self.font_library = library
        self.fonts = library.fonts

    def preview(self, data):
        choices, primary = font_choices(data, self.fonts)
        document = 'layout_mode' in data or 'pages' in data
        results = generate_document(primary, data, choices) if document else [generate(primary, data, choices)]
        group_id = uuid.uuid4().hex
        entries, public = [], []
        for result in results:
            job_id = uuid.uuid4().hex
            folder = self.jobs_dir / job_id
            folder.mkdir()
            # Preserve the exact bytes hashed and previewed, including on Windows.
            (folder / 'job.gcode').write_bytes(result['gcode'].encode('ascii'))
            (folder / 'preview.svg').write_text(result['svg'], encoding='utf8')
            (folder / 'metadata.json').write_text(json.dumps(result['metadata'], ensure_ascii=False, indent=2), encoding='utf8')
            entries.append((job_id, folder, result))
            public.append(dict(job_id=job_id, metadata=result['metadata'], svg=result['svg']))
        with self.lock:
            for job_id, folder, result in entries:
                self.cache[job_id] = (folder, result)
            self.groups[group_id] = [entry[0] for entry in entries]
            if document:
                self.documents[group_id] = dict(document_id=group_id, page_count=len(public),
                                                 job_ids=self.groups[group_id], seed=results[0]['metadata']['seed'])
            # Evict whole documents: a document with >8 pages stays usable.
            while len(self.groups) > 8 or (len(self.groups) > 1 and sum(r['metadata']['points'] for f,r in self.cache.values()) > 2000000):
                expired, ids = self.groups.popitem(last=False)
                self.documents.pop(expired, None)
                for job_id in ids: self.cache.pop(job_id, None)
        response = public[0].copy()
        if document:
            response.update(document_id=group_id, page_count=len(public), pages=public)
        return response

    def archive(self, document_id):
        with self.lock:
            if document_id not in self.documents:
                raise ValueError('文档预览已过期，请重新生成')
            manifest = dict(self.documents[document_id])
            entries = [self.cache[job_id] for job_id in manifest['job_ids']]
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for index, (folder, result) in enumerate(entries):
                prefix = f'page-{index + 1:03d}'
                archive.writestr(prefix + '.gcode', result['gcode'].encode('ascii'))
                archive.writestr(prefix + '.svg', result['svg'])
                archive.writestr(prefix + '.json', json.dumps(result['metadata'], ensure_ascii=False, indent=2))
        return output.getvalue()

    def job(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('任务编号无效')
        with self.lock:
            if job_id not in self.cache: raise ValueError('预览已过期，请重新生成')
            return self.cache[job_id]

    def api(self, path, data=None):
        if path in ('/api/connect','/api/jog','/api/pen','/api/check') and (self.jobs_dir / '.speed-limit-update-pending').is_file():
            raise ValueError('控制端将在当前任务结束后更新，请等待更新完成后操作')
        if path == '/api/fonts':
            return dict(fonts=[dict(id=k, name=''.join(c for c in p.stem if ord(c) >= 32 and ord(c) != 127))
                               for k, p in self.fonts.items()])
        if path == '/api/font-previews': return self.font_library.previews(data)
        if path == '/api/font-recommendations': return self.font_library.recommendations()
        if path == '/api/font-refresh': return self.font_updates.request()
        if path == '/api/font-status': return self.font_updates.status()
        if path == '/api/state':
            state = self.machine.refresh_status()
            state['speed_limit_update_pending'] = (self.jobs_dir / '.speed-limit-update-pending').is_file()
            return self.eta.decorate(state)
        if path == '/api/ports':
            return self.machine.ports()
        if path == '/api/device-profiles':
            return dict(profiles=self.device_profiles.all()) if data is None else dict(profile=self.device_profiles.save(data))
        if path == '/api/preview': return self.preview(data)
        if path == '/api/coordinate-preview':
            return document_coordinate_preview(data) if 'layout_mode' in data else coordinate_preview(data)
        if path == '/api/connect':
            profile = self.device_profiles.all().get(data.get('port'))
            return self.machine.connect(data.get('port'), profile['device_control']) if profile else self.machine.connect(data.get('port'))
        if path == '/api/disconnect': return self.machine.disconnect()
        if path == '/api/jog': return self.machine.jog(data.get('axis'), data.get('distance'), data.get('feed', 300))
        if path == '/api/pen': return self.machine.set_pen(data.get('position'))
        if path == '/api/manual-pause': return self.machine.cancel_manual()
        if path == '/api/manual-stop': return self.machine.cancel_manual(terminate=True)
        if path == '/api/stop': return self.eta.decorate(self.machine.stop())
        if path == '/api/check':
            metadata = self.job(data['job_id'])[1]['metadata'] if data.get('job_id') else {'paper': [210, 297]}
            return self.machine.check_origin(metadata)
        if path == '/api/start':
            if (self.jobs_dir / '.speed-limit-update-pending').is_file():
                raise ValueError('控制端将在当前任务结束后更新速度上限，更新完成后可开始新任务')
            if data.get('origin_confirmed') is not True:
                raise ValueError('请确认纸张方向、机器零坐标和笔的位置')
            folder, result = self.job(data.get('job_id'))
            if result['metadata'].get('empty'):
                raise ValueError('本页没有可书写内容，请选择其他页面')
            return self.eta.decorate(self.machine.start(data['job_id'], folder, result))
        if path == '/api/pause': return self.eta.decorate(self.machine.pause())
        if path == '/api/resume': return self.eta.decorate(self.machine.resume())
        if path == '/api/speed': return self.eta.decorate(self.machine.set_speed(data.get('percent'), data.get('job_id')))
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
        origin = self.headers.get('Origin')
        if origin in self.server.allowed_origins:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename: self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def allowed(self):
        host = self.headers.get('Host', '')
        if host not in self.server.allowed_hosts:
            self.respond(403, dict(error='请求的 Host 不在允许列表')); return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host and origin not in self.server.allowed_origins:
            self.respond(403, dict(error='拒绝跨站请求')); return False
        if self.server.app.token:
            actual = self.headers.get('Authorization', '')
            if not hmac.compare_digest(actual, 'Bearer ' + self.server.app.token):
                self.respond(401, dict(error='请输入服务访问令牌')); return False
        return True

    def do_OPTIONS(self):
        # Preflight carries no bearer token. Only explicitly configured origins
        # and the API Host allowlist can receive CORS permission.
        origin = self.headers.get('Origin', '')
        if self.headers.get('Host', '') not in self.server.allowed_hosts or origin not in self.server.allowed_origins:
            self.respond(403, dict(error='前端来源未获允许')); return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path.startswith('/api/'):
                if not self.allowed(): return
                if path.startswith('/api/jobs/') and path.endswith('/gcode'):
                    job_id = path[len('/api/jobs/'):-len('/gcode')]
                    folder, result = self.server.app.job(job_id)
                    self.respond(200, result['gcode'], 'text/plain; charset=utf-8', 'writing.gcode'); return
                if path.startswith('/api/documents/') and path.endswith('/archive'):
                    document_id = path[len('/api/documents/'):-len('/archive')]
                    self.respond(200, self.server.app.archive(document_id), 'application/zip', 'writing-pages.zip'); return
                if path not in ('/api/fonts', '/api/font-status', '/api/font-recommendations', '/api/ports', '/api/state', '/api/device-profiles'): raise KeyError(path)
                self.respond(200, self.server.app.api(path)); return
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            files['/config.js'] = ('config.js', 'text/javascript')
            files['/notebook-sizing.js'] = ('notebook-sizing.js', 'text/javascript')
            files['/font-library.js'] = ('font-library.js', 'text/javascript')
            files['/page-notifications.js'] = ('page-notifications.js', 'text/javascript')
            files['/notification-worker.js'] = ('notification-worker.js', 'text/javascript')
            if not self.server.serve_frontend: raise KeyError(path)
            if path not in files: raise KeyError(path)
            name, kind = files[path]
            self.respond(200, (STATIC / name).read_bytes(), kind + '; charset=utf-8')
        except KeyError: self.respond(404, dict(error='页面或接口不存在'))
        except ControllerUnavailable as error: self.respond(503, dict(error=str(error)))
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error: self.respond(409, dict(error=str(error)))

    def do_POST(self):
        if not self.allowed(): return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.respond(415, dict(error='需要 application/json')); return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 524288: raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError('需要 JSON 对象')
            path = urlsplit(self.path).path
            if path not in ('/api/preview', '/api/font-refresh', '/api/font-previews', '/api/coordinate-preview', '/api/device-profiles', '/api/connect', '/api/disconnect', '/api/check', '/api/start', '/api/pause', '/api/resume', '/api/speed', '/api/jog', '/api/pen', '/api/manual-pause', '/api/manual-stop', '/api/stop'):
                raise KeyError(path)
            self.respond(200, self.server.app.api(path, data))
        except KeyError: self.respond(404, dict(error='接口不存在'))
        except ControllerUnavailable as error: self.respond(503, dict(error=str(error)))
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error: self.respond(409, dict(error=str(error)))


def create_server(app, host='127.0.0.1', port=8080, extra_hosts=(), extra_ports=(), frontend_origins=(), serve_frontend=True):
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app
    server.allowed_origins = set(frontend_origins)
    server.serve_frontend = serve_frontend
    actual_port = server.server_port
    server.allowed_hosts = {f'{h}:{p}' for h in ('127.0.0.1', 'localhost', host, *extra_hosts)
                            for p in (actual_port, *extra_ports)}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--font-dir', type=pathlib.Path, default=ROOT / 'gcodeFonts')
    parser.add_argument('--jobs-dir', type=pathlib.Path, default=ROOT / 'backend' / 'jobs')
    parser.add_argument('--controller-url', default=os.environ.get('PLOTTER_CONTROLLER_URL', 'http://127.0.0.1:8090'))
    parser.add_argument('--frontend-origin', action='append', default=[], help='Explicit independent frontend origin, e.g. http://localhost:8081')
    parser.add_argument('--api-only', action='store_true', help='Disable bundled frontend hosting')
    parser.add_argument('--allow-unauthenticated', action='store_true', help='Explicitly allow network access without a token')
    parser.add_argument('--allow-host', action='append', default=[], help='LAN hostname/IP without port')
    parser.add_argument('--allow-origin-port', action='append', type=int, default=[], help='Explicit browser-side SSH tunnel port')
    args = parser.parse_args()
    token = os.environ.get('PLOTTER_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost') and len(token) < 16 and not (args.allow_unauthenticated and not token):
        parser.error('局域网监听需要设置至少 16 位的 PLOTTER_TOKEN 环境变量')
    if any(not 1 <= port <= 65535 for port in args.allow_origin_port):
        parser.error('转发端口必须在 1–65535 之间')
    for origin in args.frontend_origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            parser.error('前端来源必须为完整 http(s)://主机:端口，不含路径')
    app = Application(args.font_dir, args.jobs_dir, token, ControllerClient(args.controller_url))
    server = create_server(app, args.host, args.port, args.allow_host, args.allow_origin_port, args.frontend_origin, not args.api_only)
    app.font_updates.start()
    print(f'Plotter backend: http://{args.host}:{server.server_port} | fonts={len(app.fonts)} | controller={args.controller_url}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        app.font_updates.stop()
        # The independently running controller owns the task lifecycle.
        server.server_close()


if __name__ == '__main__': main()
