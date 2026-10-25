"""Calibrate Latin letters and digits against the selected Chinese ink heights."""
import functools
import pathlib
import statistics

from backend.gfont import GFont
from backend.mixing import scoped


SAMPLES = dict(zh='国田日目中永你好字体写字机大小天地文学习生年月光速度',
               letters='AEFHIKLMNTUVWXZ', digits='0123456789')


@functools.lru_cache(maxsize=512)
def _measure(path, size, modified, mode):
    # Cache by immutable file identity, so font replacement invalidates the sample.
    with GFont(path) as font:
        result = {}
        for group, sample in SAMPLES.items():
            reference = font.cjk_metrics if group == 'zh' and mode == 'notebook' else font.metrics
            heights = []
            for char in sample:
                if ord(char) not in font.index:
                    continue
                points = [p for s in font.glyph(char).strokes(reference['reference_height'] * .003)
                          if len(s) >= 2 for p in s]
                if points:
                    height = (max(y for x, y in points) - min(y for x, y in points)) / reference['reference_height']
                    if height > .05:
                        heights.append(height)
            result[group] = dict(height=statistics.median(heights) if heights else None, samples=len(heights))
        return result


def measure(font, mode):
    path = pathlib.Path(font.path).resolve()
    stat = path.stat()
    return _measure(str(path), stat.st_size, stat.st_mtime_ns, mode)


class FontBalance:
    def __init__(self, choices, fonts, config):
        self.config = config
        self.enabled = config['smart_balance']
        self.records = {}
        self.target = .9
        self.reference = 'nominal'
        if not self.enabled:
            return
        samples = [measure(font, config['mode']) for font in fonts]
        references = [(sample['zh']['height'], choice['weight']) for choice, sample in zip(choices, samples)
                      if scoped(choice['scope'], 'zh') and sample['zh']['height'] is not None]
        if references:
            # Weight each font once, rather than letting sample coverage change its probability.
            total = sum(weight for height, weight in references)
            self.target = .9 * sum(height * weight / total for height, weight in references)
            self.reference = 'selected_chinese'
        for choice, sample in zip(choices, samples):
            self.records[choice['font_id']] = self.record(sample)

    def record(self, sample):
        result = {}
        for group, setting in [('letters', 'letter_scale'), ('digits', 'digit_scale')]:
            measured = sample[group]['height']
            auto = max(.35, min(1.4, self.target / measured)) if measured else 1.
            result[group] = dict(scale=auto * self.config[setting] / 100, automatic_scale=auto,
                                 samples=sample[group]['samples'])
        return result

    def factor(self, font_id, font, char):
        group = 'letters' if char.isascii() and char.isalpha() else 'digits' if char in '0123456789' else None
        if not self.enabled or group is None:
            return 1.
        if font_id not in self.records:
            # A missing glyph uses the actual replacement font's own calibration.
            self.records[font_id] = self.record(measure(font, self.config['mode']))
        return self.records[font_id][group]['scale']

    def metadata(self, font_ids):
        return dict(enabled=self.enabled, letter_scale=self.config['letter_scale'], digit_scale=self.config['digit_scale'],
                    reference=self.reference, target_ink_ratio=.9,
                    fonts=[dict(font_id=key, **self.records[key]) for key in sorted(font_ids) if key in self.records])
