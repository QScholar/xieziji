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
from urllib.parse import urlsplit, unquote
from backend.layout import generate, coordinate_preview
from backend.controller_client import ControllerClient, ControllerUnavailable
from backend.mixing import font_choices
from backend.document import generate_document, predict_line_pitch, coordinate_preview as document_coordinate_preview
from backend.eta import TaskETA
from backend.font_library import FontLibrary
from backend.device_profiles import DeviceProfiles
from backend.font_updates import FontUpdates
from backend.preview_store import PreviewStore, valid_id
from backend.presets import Presets
from backend.workspaces import Workspaces
from backend.watchdog import ControllerWatchdog

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / 'frontend'
# Controller phases in which a task is still owned by a single execution id.
EXECUTING = frozenset({'running', 'paused', 'draining', 'stopping', 'returning'})


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
        self.previews = PreviewStore(self.jobs_dir)
        # A preview is immutable content; every start mints a new controller
        # execution id so the same page can be written again from a new origin.
        self.executions = collections.OrderedDict()
        try:
            saved = json.loads((self.jobs_dir / '.executions.json').read_text(encoding='utf8'))
            entries = saved.items() if isinstance(saved, dict) else ()
            self.executions.update((k, v) for k, v in entries
                                   if isinstance(k, str) and re.fullmatch(r'[a-f0-9]{32}', k)
                                   and isinstance(v, str) and re.fullmatch(r'[a-f0-9]{32}', v))
        except (OSError, ValueError):
            pass
        self.start_lock = threading.Lock()
        self.lock = threading.Lock()
        self.eta = TaskETA(self.jobs_dir, self.execution_path)
        self.font_library = FontLibrary(self.fonts)
        self.font_updates = FontUpdates(self.fonts_dir, self.jobs_dir / '.font-analysis', self._publish_fonts)
        self.device_profiles = DeviceProfiles(self.jobs_dir)
        self.presets = Presets(self.jobs_dir)
        self.workspaces = Workspaces(self.jobs_dir)
        self.watchdog = ControllerWatchdog(self.machine, self.device_profiles, self.jobs_dir)

    def _publish_fonts(self, library):
        # Each request gets an immutable font mapping / library generation.
        self.font_library = library
        self.fonts = library.fonts

    def preview(self, data):
        library=self.font_library
        choices, primary = font_choices(data, library.fonts)
        if choices is None:choices=[dict(font_id=data['font_id'],path=primary,weight=100.)]
        document = 'layout_mode' in data or 'pages' in data
        results = generate_document(primary, data, choices, library) if document else [generate(primary, data, choices, library)]
        group_id = uuid.uuid4().hex
        entries, public = [], []
        for result in results:
            job_id = uuid.uuid4().hex
            folder = self.jobs_dir / job_id
            folder.mkdir()
            result['metadata']['preview_group_id'] = group_id
            # Preserve the exact bytes hashed and previewed, including on Windows.
            (folder / 'job.gcode').write_bytes(result['gcode'].encode('ascii'))
            (folder / 'preview.svg').write_text(result['svg'], encoding='utf8')
            (folder / 'metadata.json').write_text(json.dumps(result['metadata'], ensure_ascii=False, indent=2), encoding='utf8')
            entries.append((job_id, folder, result))
            public.append(dict(job_id=job_id, metadata=result['metadata'], svg=result['svg']))
        manifest = self.previews.save_group(group_id, [entry[0] for entry in entries],
                                           results[0]['metadata']['seed'], document)
        with self.lock:
            for job_id, folder, result in entries:
                self.cache[job_id] = (folder, result)
            self.groups[group_id] = [entry[0] for entry in entries]
            if document:
                self.documents[group_id] = manifest
            self._trim_cache()
        response = public[0].copy()
        if document:
            response.update(document_id=group_id, page_count=len(public), pages=public)
        return response

    def archive(self, document_id):
        if not valid_id(document_id):
            raise ValueError('文档编号无效')
        with self.lock:
            manifest = self.documents.get(document_id)
        manifest = dict(manifest or self.previews.group(document_id))
        if not manifest['document']:
            raise ValueError('该预览不是多页文档，请单独下载')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for index, job_id in enumerate(manifest['job_ids']):
                folder, result = self.job(job_id)
                prefix = f'page-{index + 1:03d}'
                archive.writestr(prefix + '.gcode', result['gcode'].encode('ascii'))
                archive.writestr(prefix + '.svg', result['svg'])
                archive.writestr(prefix + '.json', json.dumps(result['metadata'], ensure_ascii=False, indent=2))
        return output.getvalue()

    def job(self, job_id):
        if not valid_id(job_id):
            raise ValueError('任务编号无效')
        with self.lock:
            cached = self.cache.get(job_id)
            if cached is not None:
                group_id = cached[1]['metadata'].get('preview_group_id', job_id)
                self.groups.move_to_end(group_id)
                return cached
        # No font loading, layout or randomness: recover the exact saved bytes.
        folder, result = self.previews.job(job_id)
        group_id = result['metadata'].get('preview_group_id', job_id)
        with self.lock:
            self.cache[job_id] = (folder, result)
            ids = self.groups.setdefault(group_id, [])
            if job_id not in ids:
                ids.append(job_id)
            self.groups.move_to_end(group_id)
            self._trim_cache()
            return folder, result

    def _trim_cache(self):
        # Eviction limits RAM only. Saved pages and their document index remain
        # available for downloads, origin checks and subsequent page executions.
        while len(self.groups) > 8 or (len(self.groups) > 1 and sum(r['metadata']['points'] for f,r in self.cache.values()) > 2000000):
            expired, ids = self.groups.popitem(last=False)
            self.documents.pop(expired, None)
            for job_id in ids:
                self.cache.pop(job_id, None)

    def execution_path(self, job_id):
        """G-code for an execution id; previews keep their own file."""
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            return None
        with self.lock:
            source = self.executions.get(job_id)
        if source is None:
            # Survives a backend restart while the controller still executes.
            try:
                saved = json.loads((self.jobs_dir / '.executions.json').read_text(encoding='utf8'))
            except (OSError, ValueError):
                saved = None
            candidate = saved.get(job_id) if isinstance(saved, dict) else None
            source = candidate if isinstance(candidate, str) and re.fullmatch(r'[a-f0-9]{32}', candidate) else None
        # Unmapped ids are pre-upgrade executions, whose folder is the id itself.
        return self.jobs_dir / (source or job_id) / 'job.gcode'

    def start_execution(self, preview_id):
        # Content is reusable; the controller executes a task id only once. Each
        # start therefore claims a fresh execution id for the same preview, so a
        # page can be written again after completion, a stop, or a fault.
        with self.start_lock:
            state = self.machine.refresh_status()
            running = state.get('job_id')
            if isinstance(running, str) and state.get('phase') in EXECUTING:
                with self.lock:
                    if self.executions.get(running) == preview_id: return running
                raise ValueError('已有任务正在执行，请等待完成或终止后再开始')
            execution = uuid.uuid4().hex
            with self.lock:
                self.executions[execution] = preview_id
                while len(self.executions) > 512: self.executions.popitem(last=False)
                temp = self.jobs_dir / '.executions.tmp'
                temp.write_text(json.dumps(dict(self.executions)), encoding='utf8')
                temp.replace(self.jobs_dir / '.executions.json')
            return execution

    def api(self, path, data=None):
        if path.startswith('/api/workspaces/'):
            device = unquote(path[len('/api/workspaces/'):])
            if data is None:
                return self.workspaces.read(device)
            render = data.get('render')
            if render is not None and (not isinstance(render, dict) or 'pages' not in render):
                raise ValueError('需要文档排版参数')
            return self.workspaces.update(device, data, (lambda: self.preview(render)) if render is not None else None)
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
        if path == '/api/presets':
            return dict(groups=self.presets.all() if data is None else self.presets.save(data))
        if path == '/api/presets/migrate': return self.presets.migrate(data)
        if path == '/api/preview': return self.preview(data)
        if path == '/api/line-pitch':
            # Prediction must not create a job: it only reads font metrics.
            choices, primary = font_choices(data, self.fonts)
            return predict_line_pitch(data, choices or [dict(font_id='primary', path=primary, weight=100.)])
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
            preview_id = data.get('job_id')
            folder, result = self.job(preview_id)
            if result['metadata'].get('empty'):
                raise ValueError('本页没有可书写内容，请选择其他页面')
            execution = self.start_execution(preview_id)
            return self.eta.decorate(self.machine.start(execution, folder, result))
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
                if path.startswith('/api/documents/') and path.endswith('/preview'):
                    document_id = path[len('/api/documents/'):-len('/preview')]
                    manifest = self.server.app.previews.group(document_id)
                    pages = [dict(job_id=job, metadata=result['metadata'], svg=result['svg'])
                             for job in manifest['job_ids'] for _, result in [self.server.app.job(job)]]
                    self.respond(200, dict(pages[0], document_id=document_id, page_count=len(pages), pages=pages)); return
                if path.startswith('/api/workspaces/'):
                    self.respond(200, self.server.app.api(path)); return
                if path not in ('/api/fonts', '/api/font-status', '/api/font-recommendations', '/api/ports', '/api/state', '/api/device-profiles', '/api/presets'): raise KeyError(path)
                self.respond(200, self.server.app.api(path)); return
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'), '/workbench.css': ('workbench.css', 'text/css')}
            files['/config.js'] = ('config.js', 'text/javascript')
            files['/notebook-sizing.js'] = ('notebook-sizing.js', 'text/javascript')
            files['/preset-store.js'] = ('preset-store.js', 'text/javascript')
            files['/workspace-sync.js'] = ('workspace-sync.js', 'text/javascript')
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
            if path.startswith('/api/workspaces/'):
                self.respond(200, self.server.app.api(path, data)); return
            if path not in ('/api/presets', '/api/presets/migrate', '/api/preview', '/api/line-pitch', '/api/font-refresh', '/api/font-previews', '/api/coordinate-preview', '/api/device-profiles', '/api/connect', '/api/disconnect', '/api/check', '/api/start', '/api/pause', '/api/resume', '/api/speed', '/api/jog', '/api/pen', '/api/manual-pause', '/api/manual-stop', '/api/stop'):
                raise KeyError(path)
            self.respond(200, self.server.app.api(path, data))
        except KeyError: self.respond(404, dict(error='接口不存在'))
        except ControllerUnavailable as error: self.respond(503, dict(error=str(error)))
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error: self.respond(409, dict(error=str(error)))


class WebServer(ThreadingHTTPServer):
    # A page loads multiple scripts/styles in parallel, alongside state polls
    # from other browsers. Avoid the stdlib's five-connection accept backlog.
    request_queue_size = 64


def create_server(app, host='127.0.0.1', port=8080, extra_hosts=(), extra_ports=(), frontend_origins=(), serve_frontend=True):
    server = WebServer((host, port), Handler)
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
    app.watchdog.start()
    print(f'Plotter backend: http://{args.host}:{server.server_port} | fonts={len(app.fonts)} | controller={args.controller_url}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        app.watchdog.stop()
        app.font_updates.stop()
        # The independently running controller owns the task lifecycle.
        server.server_close()


if __name__ == '__main__': main()
