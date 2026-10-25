"""Service-to-service controller protocol. No serial driver or motion worker here."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ControllerUnavailable(OSError):
    pass


class ControllerClient:
    def __init__(self, url=None, token=None):
        self.url = (url or os.environ.get('PLOTTER_CONTROLLER_URL', 'http://127.0.0.1:8090')).rstrip('/')
        parsed = urllib.parse.urlsplit(self.url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('控制端地址必须是 http(s)://主机:端口')
        self.token = os.environ.get('CONTROLLER_TOKEN', '') if token is None else token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None, timeout=10):
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode('ascii')
        req = urllib.request.Request(self.url + '/v1/' + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                message = json.load(error).get('error', str(error))
            except (ValueError, OSError):
                message = str(error)
            raise ValueError('控制端：' + message) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ControllerUnavailable('无法连接 C++ 控制端，请检查控制服务地址和运行状态') from error
        if not isinstance(result, dict):
            raise ControllerUnavailable('控制端返回格式不正确')
        return result

    def snapshot(self):
        return self.request('state', timeout=5)

    refresh_status = snapshot

    def ports(self):
        return self.request('ports')

    def control(self, action, data=None):
        # No automatic retries for movement commands. Task start is idempotent
        # on the controller; callers can retry explicitly after a timeout.
        return self.request('control/' + action, {} if data is None else data, timeout=240)

    def connect(self, port, device_control=None):
        data = {'port': port}
        if device_control is not None:
            data['device_control'] = device_control
        return self.control('connect', data)

    def disconnect(self):
        return self.control('disconnect')

    def jog(self, axis, distance, feed=300):
        return self.control('jog', dict(axis=axis, distance=distance, feed=feed))

    def set_pen(self, position):
        return self.control('pen', dict(position=position))

    def cancel_manual(self, terminate=False):
        return self.control('manual-stop' if terminate else 'manual-pause')

    def stop(self):
        return self.control('stop')

    def pause(self):
        return self.control('pause')

    def resume(self):
        return self.control('resume')

    def set_speed(self, percent, job_id):
        return self.control('speed', dict(percent=percent, job_id=job_id))

    def check_origin(self, metadata):
        return self.control('check', dict(metadata=metadata))

    def start(self, job_id, folder, result):
        # Transfer only execution metadata. Fonts, text, preview and full paths
        # stay on the business backend. Each request has bounded memory usage.
        meta = {key: result['metadata'][key] for key in
                ('sha256', 'commands', 'machine_paper', 'machine_page_bounds')}
        if 'device_control' in result['metadata']:
            meta['device_control'] = result['metadata']['device_control']
        if 'device_port' in result['metadata']:
            meta['device_port'] = result['metadata']['device_port']
        base = 'jobs/' + job_id + '/'
        upload = self.request(base + 'begin', meta)
        if not upload['consumed'] and not upload['ready']:
            with (folder / 'job.gcode').open('rb') as stream:
                stream.seek(upload['offset'])
                offset = upload['offset']
                while chunk := stream.read(32768):
                    uploaded = self.request(base + 'chunk', dict(offset=offset, content=chunk.decode('ascii')))
                    offset = uploaded['offset']
            self.request(base + 'seal', {})
        return self.control('start', dict(job_id=job_id, origin_confirmed=True))
