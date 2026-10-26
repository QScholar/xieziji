"""Validated per-port device profiles, persisted beside business jobs."""
import json
import math
import os
import threading


def control_settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError('设备控制参数必须为对象')
    kind = value.get('controller_type', 'kenjoy-grbl')
    if kind not in ('kenjoy-grbl', 'grbl'):
        raise ValueError('暂只支持奎享 GRBL 和通用 GRBL 协议')
    result = dict(controller_type=kind)
    for key, default, low, high in [('pen_up', 0, -300, 300), ('pen_down', 10, -300, 300),
                                   ('pen_feed', 600, 100, 3000)]:
        n = value.get(key, default)
        if type(n) not in (int, float) or not math.isfinite(n) or not low <= n <= high:
            raise ValueError('设备参数超出范围：' + key)
        result[key] = round(n, 3)
    if abs(result['pen_up'] - result['pen_down']) < .01:
        raise ValueError('抬笔与落笔位置不能相同')
    return result


def validate_profile(data):
    if not isinstance(data, dict):
        raise ValueError('设备档案必须为对象')
    port, name = data.get('port'), data.get('name', '')
    if not isinstance(port, str) or not 1 <= len(port) <= 200 or any(ord(c) < 32 for c in port):
        raise ValueError('设备端口无效')
    if not isinstance(name, str) or len(name.strip()) > 60:
        raise ValueError('设备名称最多 60 字')
    coordinates = data.get('coordinate_system', {})
    if not isinstance(coordinates, dict):
        raise ValueError('坐标参数无效')
    result = dict(port=port, name=name.strip(), device_control=control_settings(data.get('device_control')))
    result['coordinate_system'] = {key: coordinates.get(key, False) for key in ('reverse_x', 'reverse_y', 'swap_xy')}
    if any(type(v) is not bool for v in result['coordinate_system'].values()):
        raise ValueError('轴映射必须为布尔值')
    origin = coordinates.get('origin', 'top-left')
    if origin not in ('top-left', 'top-right', 'bottom-left', 'bottom-right'):
        raise ValueError('起点方向无效')
    result['coordinate_system']['origin'] = origin
    for key, default, low, high in [('feed', 10000, 100, 12000), ('move_feed', 10000, 100, 12000),
                                   ('jog_distance', 1, .01, 20), ('jog_feed', 300, 50, 1000)]:
        n = data.get(key, default)
        if type(n) not in (int, float) or not math.isfinite(n) or not low <= n <= high:
            raise ValueError('设备参数超出范围：' + key)
        result[key] = n
    return result


class DeviceProfiles:
    def __init__(self, folder):
        self.path = folder / 'device-profiles.json'
        self.lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding='utf8'))
        return {port: validate_profile(profile) for port, profile in value.items()}

    def all(self):
        with self.lock:
            return self._read()

    def save(self, data):
        profile = validate_profile(data)
        with self.lock:
            profiles = self._read()
            profiles[profile['port']] = profile
            temp = self.path.with_suffix('.tmp')
            with temp.open('w', encoding='utf8') as stream:
                json.dump(profiles, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        return profile
