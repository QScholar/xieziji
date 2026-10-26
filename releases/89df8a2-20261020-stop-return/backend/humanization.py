"""Bounded, reproducible variation of whole glyphs, with smooth line drift."""
import math
import random


DEFAULTS = dict(enabled=False, vertical=.3, scale=5., rotation=2., spacing=.2, baseline=.4)
LIMITS = dict(vertical=2., scale=20., rotation=10., spacing=2., baseline=2.)


def settings(data):
    raw = data.get('humanization', {})
    if not isinstance(raw, dict): raise ValueError('仿真设置必须为对象')
    result = {}
    for key in ('enabled', *(name + '_enabled' for name in LIMITS)):
        value = raw.get(key, False if key == 'enabled' else True)
        if type(value) is not bool: raise ValueError('仿真开关必须为布尔值')
        result[key] = value
    for key, maximum in LIMITS.items():
        value = raw.get(key, DEFAULTS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum or not math.isfinite(value):
            raise ValueError(f'仿真参数 {key} 必须为 0–{maximum:g} 之间的有限数字')
        result[key] = float(value)
    return result


def active(config):
    return config['enabled'] and any(config[key + '_enabled'] and config[key] > 0 for key in LIMITS)


class Variation:
    def __init__(self, config, seed):
        self.config = config
        self.rng = random.Random('glyph-variation:' + seed)
        self.line_rng = random.Random('line-variation:' + seed)

    def amplitude(self, key):
        return self.config[key] if self.config['enabled'] and self.config[key + '_enabled'] else 0.

    def glyph(self, token, size, spacing):
        if not token['paths']: return dict(token)
        scale = 1 + self.rng.uniform(-1, 1) * self.amplitude('scale') / 100
        degrees = self.rng.uniform(-1, 1) * self.amplitude('rotation')
        vertical = self.rng.uniform(-1, 1) * self.amplitude('vertical')
        gap = max(0., spacing + self.rng.uniform(-1, 1) * self.amplitude('spacing'))
        if not self.amplitude('scale') and not self.amplitude('rotation'):
            return dict(token, paths=[[(x, y+vertical) for x, y in stroke] for stroke in token['paths']],
                        advance=token['advance']+gap-spacing,
                        variation=dict(scale=scale, rotation=degrees, vertical=vertical, gap=gap))
        points = [p for stroke in token['paths'] for p in stroke]
        cx = (min(x for x, y in points) + max(x for x, y in points)) / 2
        cy = (min(y for x, y in points) + max(y for x, y in points)) / 2
        radians = math.radians(degrees)
        cos, sin = math.cos(radians), math.sin(radians)
        paths = [[(cx + scale * ((x-cx)*cos - (y-cy)*sin),
                   cy + scale * ((x-cx)*sin + (y-cy)*cos) + vertical)
                  for x, y in stroke] for stroke in token['paths']]
        xmin = min(x for stroke in paths for x, y in stroke)
        xmax = max(x for stroke in paths for x, y in stroke)
        # Lay out each transformed ink footprint, so rotated/scaled letters are
        # measured before wrapping and adjacent glyph boxes never cross.
        paths = [[(x-xmin, y) for x, y in stroke] for stroke in paths]
        return dict(token, paths=paths, bounds=(0., xmax-xmin),
                    advance=max(xmax-xmin, size*scale*.12)+gap,
                    variation=dict(scale=scale, rotation=degrees, vertical=vertical, gap=gap))

    def line_phase(self):
        return self.line_rng.uniform(0., 2*math.pi)

    def baseline(self, phase, x, usable):
        return self.amplitude('baseline') * math.sin(phase + math.pi*x/usable)
