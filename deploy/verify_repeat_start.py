"""Deployment assertion: one preview must be startable more than once.

Runs inside the deployed release with a stub controller, so no serial port is
opened and no motion is commanded. Mirrors the C++ rule that a task id is
claimed exactly once, which is what made a finished page unstartable before.
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The deployed release is whatever the service task runs; locally that is the
# current checkout's live release directory.
RELEASE = pathlib.Path(json.loads((ROOT / 'releases' / '89df8a2-20261020-live-speed' / 'deploy' / 'services.json')
                                   .read_text(encoding='utf-8-sig'))['Release'])
if not (RELEASE / 'backend' / 'server.py').is_file():
    raise SystemExit('deployed release is missing backend/server.py: ' + str(RELEASE))
sys.path.insert(0, str(RELEASE))
from backend.server import Application  # noqa: E402


class StubController:
    def __init__(self):
        self.claims = []
        self.state = dict(connected=True, phase='idle', job_id=None, acknowledged=0, total=0,
                          mode='simulation', status='<Idle|MPos:0.000,0.000,0.000|FS:0,0>', port='STUB',
                          operation_type=None, error=None, position=[0., 0., 0.], pen='up', pen_top=True,
                          motion_revision=0, last_action='')

    def refresh_status(self):
        return dict(self.state)

    snapshot = refresh_status

    def request(self, path, data=None, timeout=10):
        if path.endswith('/begin'): return dict(offset=0, ready=False, consumed=False)
        if path.endswith('/chunk'): return dict(offset=data['offset'] + len(data['content']))
        if path.endswith('/seal'): return dict(ready=True, consumed=False)
        raise AssertionError(path)

    def start(self, job_id, folder, result):
        if job_id in self.claims:
            raise AssertionError('Task already executed; generate a new task')
        assert (folder / 'job.gcode').is_file(), 'execution has no G-code source'
        self.claims.append(job_id)
        self.state.update(phase='running', job_id=job_id, operation_type='writing',
                          acknowledged=0, total=result['metadata']['commands'])
        return self.complete()

    def complete(self):
        self.state.update(phase='complete', operation_type=None, acknowledged=self.state['total'])
        return dict(self.state)


def main():
    fonts = RELEASE.parents[1] / 'gcodeFonts'
    with tempfile.TemporaryDirectory() as jobs:
        jobs = pathlib.Path(jobs)
        controller = StubController()
        app = Application(fonts, jobs, controller=controller)
        font_id = next(iter(app.fonts))
        preview_id = app.api('/api/preview', dict(font_id=font_id, text='repeat start'))['job_id']

        first = app.api('/api/start', dict(job_id=preview_id, origin_confirmed=True))
        if first['phase'] != 'complete' or first['job_id'] == preview_id:
            raise AssertionError('first start did not claim a fresh execution id: ' + repr(first.get('job_id')))
        if app.execution_path(first['job_id']) != jobs / preview_id / 'job.gcode':
            raise AssertionError('execution id is not mapped to the preview G-code')
        running = app.eta.decorate(dict(controller.state, phase='running', acknowledged=5))['eta']
        if running['status'] not in ('running', 'draining', 'calibrating'):
            raise AssertionError('ETA cannot read the mapped G-code: ' + repr(running))

        second = app.api('/api/start', dict(job_id=preview_id, origin_confirmed=True))
        if second['job_id'] in (preview_id, first['job_id']) or controller.claims != [first['job_id'], second['job_id']]:
            raise AssertionError('second start reused an execution id: ' + repr(controller.claims))
        if controller.state['phase'] != 'complete':
            raise AssertionError('second start did not complete')

        # A running task of the same preview is reused; any other task blocks.
        controller.state.update(phase='running', job_id=second['job_id'], operation_type='writing')
        if app.start_execution(preview_id) != second['job_id']:
            raise AssertionError('running execution of the same preview was duplicated')
        controller.state.update(job_id='f' * 32)
        try:
            app.start_execution(preview_id)
        except ValueError as error:
            if '已有任务正在执行' not in str(error): raise
        else:
            raise AssertionError('a foreign running task must block a new start')
        controller.state.update(job_id=second['job_id'])

        # Blank pages still never start.
        blank = app.api('/api/preview', dict(font_id=font_id, layout_mode='notebook',
                                           pages=[dict(text='\n'), dict(text='A')]))['pages'][0]['job_id']
        try:
            app.api('/api/start', dict(job_id=blank, origin_confirmed=True))
        except ValueError as error:
            if '没有可书写内容' not in str(error): raise
        else:
            raise AssertionError('a blank page must not start')

        # The mapping is durable so a restart mid-task still renders the ETA.
        restarted = Application(fonts, jobs, controller=controller)
        if restarted.execution_path(first['job_id']) != jobs / preview_id / 'job.gcode':
            raise AssertionError('execution mapping did not survive a backend restart')

        print('REPEAT_START_OK', first['job_id'], second['job_id'], preview_id)


if __name__ == '__main__':
    main()
