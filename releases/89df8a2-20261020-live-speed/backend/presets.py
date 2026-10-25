"""Shared presets, atomic disk commits and idempotent browser imports."""
import copy
import hashlib
import json
import math
import os
import pathlib
import threading
import uuid

KINDS = ('layout', 'grid', 'font')
BUILTINS = {'a4-portrait', 'a4-landscape', 'a4-grid'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def validate(preset, kind):
    if not isinstance(preset, dict):
        raise ValueError('预设必须是对象')
    for field, limit in (('id', 100), ('name', 40)):
        value = preset.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('预设编号或名称无效')
    if preset['id'] in BUILTINS:
        raise ValueError('不能覆盖内置预设')
    if len(encoded(preset).encode('utf8')) > 16000:
        raise ValueError('预设内容过大')
    if kind == 'font':
        mix = preset.get('mix')
        if not isinstance(mix, list) or not 1 <= len(mix) <= 16:
            raise ValueError('字体预设需要 1–16 种字体')
        ids = set()
        for font in mix:
            if not isinstance(font, dict):
                raise ValueError('字体组合无效')
            fid, weight = font.get('font_id'), font.get('weight')
            if not isinstance(fid, str) or not 1 <= len(fid) <= 200 or fid in ids:
                raise ValueError('字体编号无效或重复')
            if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1000000:
                raise ValueError('字体权重无效')
            if font.get('scope', 'all') not in ('all', 'zh', 'en'):
                raise ValueError('字体适用范围无效')
            ids.add(fid)
        if not any(f['weight'] > 0 for f in mix):
            raise ValueError('至少一种字体权重大于 0')
    else:
        values = preset.get('values')
        if not isinstance(values, dict) or not all(k in values for k in ('width', 'height', 'rows', 'size')):
            raise ValueError('排版参数缺失')
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000 for v in values.values()):
            raise ValueError('排版参数必须是有限数值')
        if not (0 < values['width'] <= 1000 and 0 < values['height'] <= 1000 and
                1 <= values['rows'] <= 500 and int(values['rows']) == values['rows'] and 0 < values['size'] <= 100):
            raise ValueError('纸张、行数或字高无效')
        for axis, margins in (('width', ('marginLeft', 'marginRight')), ('height', ('marginTop', 'marginBottom'))):
            if any(values.get(k, 0) < 0 for k in margins) or sum(values.get(k, 0) for k in margins) >= values[axis]:
                raise ValueError('页边距无效')
        limits = {'width': (1, 600), 'height': (1, 600), 'rows': (1, 200), 'size': (2, 20),
                  'latinSize': (2, 20), 'spacing': (0, 10), 'feed': (100, 12000), 'moveFeed': (100, 12000),
                  'punctuationWidth': (0, 2), 'gridColumns': (1, 100), 'gridRowGap': (0, 100),
                  'gridColumnGap': (0, 100), 'gridSizePercent': (10, 100), 'gridPadding': (0, 10)}
        if any(key in values and not lo <= values[key] <= hi for key, (lo, hi) in limits.items()):
            raise ValueError('排版参数超出范围')
        if 'gridColumns' in values and int(values['gridColumns']) != values['gridColumns']:
            raise ValueError('格子列数必须是整数')
        for key in ('allowOverlap', 'smartBalance', 'drawLines'):
            if key in preset and type(preset[key]) is not bool:
                raise ValueError('预设开关无效')
        if preset.get('lineSizing', 'rows') not in ('rows', 'size'):
            raise ValueError('排版方式无效')
        for key, lo, hi in (('lineLooseness', 1, 4), ('letterScale', 50, 150), ('digitScale', 50, 150)):
            if key in preset and (type(preset[key]) not in (int, float) or not lo <= preset[key] <= hi):
                raise ValueError('排版松紧或缩放无效')
        if 'humanization' in preset:
            human = preset['humanization']
            if not isinstance(human, dict) or type(human.get('enabled')) is not bool:
                raise ValueError('仿真参数无效')
            for key, maximum in (('vertical', 2), ('scale', 20), ('rotation', 10), ('spacing', 2), ('baseline', 2)):
                value = human.get(key)
                if type(human.get(key+'_enabled')) is not bool or type(value) not in (int, float) or not 0 <= value <= maximum:
                    raise ValueError('仿真参数无效')


class Presets:
    def __init__(self, directory):
        self.path = pathlib.Path(directory) / '.presets.json'
        self.lock = threading.Lock()
        self.state = {'groups': {k: {'revision': 0, 'presets': []} for k in KINDS}, 'imports': {k: {} for k in KINDS}}
        if self.path.exists():
            # A corrupt file must be reported, never silently replaced with an empty store.
            self.state = json.loads(self.path.read_text(encoding='utf8'))
            for kind in KINDS:
                for preset in self.state['groups'][kind]['presets']:
                    validate(preset, kind)

    def all(self):
        with self.lock:
            return copy.deepcopy(self.state['groups'])

    def commit(self, state):
        temporary = self.path.with_name('.presets-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf8') as stream:
                stream.write(encoded(state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.state = state

    def save(self, data):
        kind = data.get('kind')
        if kind not in KINDS:
            raise ValueError('预设类型无效')
        presets = data.get('presets')
        if not isinstance(presets, list) or len(presets) > 500:
            raise ValueError('每类最多保存 500 个预设')
        ids, names = set(), set()
        for preset in presets:
            validate(preset, kind)
            if preset['id'] in ids or preset['name'] in names:
                raise ValueError('预设编号或名称重复')
            ids.add(preset['id']); names.add(preset['name'])
        with self.lock:
            group = self.state['groups'][kind]
            if type(data.get('revision')) is not int or data['revision'] != group['revision']:
                raise ValueError('其他页面已修改预设，请刷新页面后重试；本次修改未覆盖服务器')
            state = copy.deepcopy(self.state)
            state['groups'][kind] = {'revision': group['revision'] + 1, 'presets': copy.deepcopy(presets)}
            self.commit(state)
            return copy.deepcopy(state['groups'])

    def migrate(self, data):
        incoming = data.get('groups')
        if not isinstance(incoming, dict) or any(k not in KINDS for k in incoming):
            raise ValueError('迁移数据无效')
        for kind, group in incoming.items():
            if not isinstance(group, dict) or not isinstance(group.get('presets'), list) or len(group['presets']) > 500:
                raise ValueError('迁移预设数量无效')
            for preset in group['presets']:
                validate(preset, kind)
        with self.lock:
            state = copy.deepcopy(self.state)
            selected, imported = {}, 0
            for kind, group in incoming.items():
                target = state['groups'][kind]
                ledger = state['imports'][kind]
                changed = False
                for original in group['presets']:
                    fingerprint = hashlib.sha256(encoded(original).encode('utf8')).hexdigest()
                    if fingerprint in ledger:
                        if group.get('selected') == original['id']:
                            selected[kind] = ledger[fingerprint]
                        continue
                    preset = copy.deepcopy(original)
                    identical = next((p for p in target['presets'] if p == preset), None)
                    if identical is None:
                        if len(target['presets']) >= 500:
                            raise ValueError('服务器预设已达 500 个，请整理后重新迁移；本地预设仍保留')
                        ids = {p['id'] for p in target['presets']}
                        names = {p['name'] for p in target['presets']}
                        if preset['id'] in ids:
                            preset['id'] = 'import-' + uuid.uuid4().hex
                        n, name = 1, preset['name']
                        while preset['name'] in names:
                            suffix = f'（迁移 {n}）'
                            preset['name'] = name[:40-len(suffix)] + suffix
                            n += 1
                        target['presets'].append(preset)
                        imported += 1
                        changed = True
                    ledger[fingerprint] = preset['id']
                    if group.get('selected') == original['id']:
                        selected[kind] = preset['id']
                if changed:
                    target['revision'] += 1
            if state != self.state:
                self.commit(state)
            return {'groups': copy.deepcopy(state['groups']), 'selected': selected, 'imported': imported}
