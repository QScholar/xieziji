"""Daily font discovery and background, device-independent analysis."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from backend.font_library import FontLibrary
from backend.gfont import GFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTERVAL = 24 * 60 * 60


class FontUpdates:
    def __init__(self, directory, storage, publish):
        self.directory = pathlib.Path(directory)
        self.storage = pathlib.Path(storage)
        self.publish = publish
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.state = dict(running=False, phase='idle', checked_at=None, analyzed_at=None,
                          next_check_at=None, error=None, invalid_fonts=[])
        self.fingerprint = None
        self.report_dir = ROOT / 'backend'
        try:
            saved = json.loads((self.storage / 'current.json').read_text(encoding='utf8'))
            generation = saved['generation']
            if not isinstance(generation, str) or len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation):
                raise ValueError('Invalid analysis generation')
            reports = self.storage / generation
            for name in ('font_similarity.json', 'font_speed.json'):
                json.loads((reports / name).read_text(encoding='utf8'))
            self.report_dir = reports
            self.fingerprint = saved['fingerprint']
            self.state['analyzed_at'] = saved['analyzed_at']
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def status(self):
        with self.lock:
            return dict(self.state)

    def request(self):
        with self.lock:
            if self.state['running']:
                return dict(self.state)
            self.state.update(running=True, phase='checking', error=None)
            self.worker = threading.Thread(target=self._run, name='font-analysis', daemon=True)
            self.worker.start()
            return dict(self.state)

    def start(self):
        self.request()
        def daily():
            while not self.stopped.wait(min(60, INTERVAL)):
                checked = self.status()['checked_at']
                if checked is None or time.time() - checked >= INTERVAL:
                    self.request()
        self.scheduler = threading.Thread(target=daily, name='daily-font-check', daemon=True)
        self.scheduler.start()

    def stop(self):
        self.stopped.set()

    def _scan(self):
        records = []
        for path in sorted(self.directory.glob('*.gfont')):
            stat = path.stat()
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else self._digest(stream)
            if path.stat().st_mtime_ns != stat.st_mtime_ns or path.stat().st_size != stat.st_size:
                raise ValueError('字体文件正在写入，请稍后刷新')
            records.append(dict(file=path.name, bytes=stat.st_size, sha256=digest))
        return records

    @staticmethod
    def _digest(stream):
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()

    def _library(self, records, reports, invalid=()):
        excluded = {item['file'] for item in invalid}
        fonts = {hashlib.sha256(r['file'].encode('utf8')).hexdigest()[:16]: self.directory / r['file']
                 for r in records if r['file'] not in excluded}
        return FontLibrary(fonts, reports / 'font_similarity.json', reports / 'font_speed.json',
                           fingerprints={r['file']: r['sha256'] for r in records})

    def _run(self):
        try:
            records = self._scan()
            now = time.time()
            invalid = []
            for record in records:
                try:
                    with GFont(self.directory / record['file']):
                        pass
                except Exception as error:
                    invalid.append(dict(file=record['file'], error=str(error)))
            with self.lock:
                self.state.update(checked_at=now, next_check_at=now + INTERVAL, invalid_fonts=invalid,
                                  font_count=len(records) - len(invalid))
            # Refresh discovery immediately. Never let a modified font inherit an old score.
            self.publish(self._library(records, self.report_dir, invalid))
            try:current_version=json.loads((self.report_dir/'font_similarity.json').read_text(encoding='utf8')).get('version',0)
            except (OSError,ValueError):current_version=0
            if records == self.fingerprint and current_version>=2:
                return
            with self.lock:
                self.state['phase'] = 'analyzing'
            self.storage.mkdir(parents=True, exist_ok=True)
            generation = uuid.uuid4().hex
            output = self.storage / generation
            output.mkdir()
            valid = [r for r in records if r['file'] not in {i['file'] for i in invalid}]
            if not valid:
                (output / 'font_similarity.json').write_text(json.dumps(dict(fonts=[], recommendations=[],
                    sample_text='', duplicate_pairs=[], errors=invalid)), encoding='utf8')
                (output / 'font_speed.json').write_text('{"fonts": []}', encoding='utf8')
            else:
                # Immutable inputs keep an upload or replacement from mixing generations.
                with tempfile.TemporaryDirectory(prefix='fonts-', dir=self.storage) as snapshot:
                    snapshot = pathlib.Path(snapshot)
                    for record in valid:
                        content = (self.directory / record['file']).read_bytes()
                        if hashlib.sha256(content).hexdigest() != record['sha256']:
                            raise ValueError('字库在检查后发生变化，请重新刷新')
                        (snapshot / record['file']).write_bytes(content)
                    self._command(['scripts/analyze_font_similarity.py', '--font-dir', str(snapshot),
                                   '--output', str(output / 'font_similarity.json'), '--report', str(output / 'similarity.md')], output)
                    self._command(['scripts/analyze_font_speed.py', '--font-dir', str(snapshot),
                                   '--similarity', str(output / 'font_similarity.json'), '--output', str(output / 'font_speed.json'),
                                   '--report', str(output / 'speed.md')], output)
            if self._scan() != records:
                raise ValueError('分析期间字库发生变化，请重新刷新')
            for filename in ('font_similarity.json', 'font_speed.json'):
                path = output / filename
                data = json.loads(path.read_text(encoding='utf8'))
                fingerprints = {r['file']: r['sha256'] for r in records}
                for info in data['fonts']:
                    info['sha256'] = fingerprints[info['file']]
                path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
            completed = time.time()
            manifest = dict(generation=generation, fingerprint=records, analyzed_at=completed)
            pending = self.storage / 'current.tmp'
            pending.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf8')
            pending.replace(self.storage / 'current.json')
            self.fingerprint, self.report_dir = records, output
            self.publish(self._library(records, output, invalid))
            with self.lock:
                self.state['analyzed_at'] = completed
        except Exception as error:
            with self.lock:
                self.state.update(phase='error', error=str(error))
        finally:
            with self.lock:
                self.state['running'] = False
                if self.state['phase'] != 'error':
                    self.state['phase'] = 'idle'

    def _command(self, arguments, output):
        with (output / 'analysis.log').open('ab') as log:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, stdout=log, stderr=log,
                                    timeout=3600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                    | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
        if result.returncode:
            raise RuntimeError('字体分析失败，请检查 NumPy/Pillow 依赖及分析日志：' + str(output / 'analysis.log'))
