"""Choose a font once per character, then wrap and render those same glyphs."""
import collections
import contextlib
import math
import random
import secrets
from backend.humanization import settings, active, Variation


def font_choices(data, available):
    entries = data.get('font_mix')
    if entries is None:
        font_id = data.get('font_id')
        if not isinstance(font_id, str) or font_id not in available:
            raise ValueError('请选择列表中的字体')
        return None, available[font_id]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise ValueError('请选择 1–16 种混排字体')
    choices = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('混排字体配置无效')
        font_id, weight = entry.get('font_id'), entry.get('weight')
        if not isinstance(font_id, str) or font_id not in available or font_id in seen:
            raise ValueError('混排字体不存在或重复')
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 1000000 or not math.isfinite(weight):
            raise ValueError('字体权重必须是 0–1000000 之间的有限数字')
        choices.append(dict(font_id=font_id, path=available[font_id], weight=float(weight)))
        seen.add(font_id)
    if not any(c['weight'] > 0 for c in choices):
        raise ValueError('至少一种字体的权重必须大于 0')
    return choices, choices[0]['path']


def layout_mixed(choices, data, text, size, spacing, pitch, usable, loader):
    seed = data.get('seed', secrets.token_hex(8))
    if isinstance(seed, bool) or not isinstance(seed, (str, int)) or not 1 <= len(str(seed)) <= 64:
        raise ValueError('混排随机种子必须是 1–64 位文本或整数')
    seed = str(seed)
    rng = random.Random(seed)
    config = settings(data)
    variation = Variation(config, seed) if active(config) else None
    with contextlib.ExitStack() as stack:
        # Zero-weight fonts never participate, including in missing-glyph fallback.
        enabled = [i for i, choice in enumerate(choices) if choice['weight'] > 0]
        fonts = {i: stack.enter_context(loader(choices[i]['path'])) for i in enabled}
        required = set(c for c in text if not c.isspace())
        candidates = {c: [i for i in enabled if ord(c) in fonts[i].index] for c in required}
        missing = sorted(c for c, options in candidates.items() if not options)
        if missing:
            raise ValueError('所选字体缺少字符：' + ''.join(missing))
        cache = {}
        fallback = 0
        paragraphs = [[]]
        for char in text.replace('\r\n', '\n').replace('\r', '\n').expandtabs(4):
            if char == '\n':
                paragraphs.append([])
                continue
            if char.isspace():
                paragraphs[-1].append(dict(char=char, font=None, paths=[], advance=size * .5, bounds=None))
                continue
            options = candidates[char]
            index = rng.choices(options, weights=[choices[i]['weight'] for i in options], k=1)[0]
            if len(options) < len(enabled):
                fallback += 1
            key = (index, char)
            if key not in cache:
                font = fonts[index]
                paths = font.text(char, size, spacing=spacing)
                xs = [x for stroke in paths for x, y in stroke]
                glyph_bounds = font.glyph(char).bounds
                advance = max((glyph_bounds[2] - glyph_bounds[0]) * size / font.metrics['reference_height'], size * .12) + spacing
                cache[key] = dict(char=char, font=index, paths=paths, advance=advance,
                                  bounds=(min(xs), max(xs)) if xs else None)
            paragraphs[-1].append(variation.glyph(cache[key], size, spacing) if variation else cache[key])

        def measure(tokens):
            x = 0.
            xmin, xmax = math.inf, -math.inf
            for token in tokens:
                if token['bounds'] is not None:
                    lo, hi = token['bounds']
                    xmin = min(xmin, x + lo)
                    xmax = max(xmax, x + hi)
                x += token['advance']
            return xmax - xmin if xmin != math.inf else 0.

        def rstrip(tokens):
            end = len(tokens)
            while end and tokens[end - 1]['char'].isspace():
                end -= 1
            return tokens[:end]

        lines = []
        for paragraph in paragraphs:
            line = []
            for token in paragraph:
                if measure(line + [token]) <= usable:
                    line.append(token)
                    continue
                if not line or measure([token]) > usable:
                    raise ValueError('单个字符超过纸张宽度，请减小字号')
                split = next((i for i in range(len(line) - 1, -1, -1) if line[i]['char'] == ' '), -1)
                if split > 0 and token['char'] != ' ':
                    lines.append(rstrip(line[:split]))
                    line = line[split + 1:] + [token]
                else:
                    lines.append(rstrip(line))
                    line = [] if token['char'].isspace() else [token]
                # The retained word is a subset of a previously fitting line;
                # split off its final character only if adding it overflows.
                if measure(line) > usable:
                    lines.append(rstrip(line[:-1]))
                    line = line[-1:]
            lines.append(rstrip(line))
        paths = []
        used = collections.Counter()
        character_variations = []
        for row, line in enumerate(lines):
            x = 0.
            phase = variation.line_phase() if variation else 0.
            row_variations = []
            for token in line:
                if token['font'] is not None:
                    used[token['font']] += 1
                drift = variation.baseline(phase, x + (token['bounds'][1]/2 if token['bounds'] else 0), usable) if variation else 0.
                paths.extend([[(x + px, row * pitch + py + drift) for px, py in stroke] for stroke in token['paths']])
                if variation:
                    row_variations.append(dict(token['variation'], baseline=drift) if 'variation' in token else None)
                x += token['advance']
            if variation: character_variations.append(row_variations)
        total_weight = sum(c['weight'] for c in choices)
        total_chars = sum(used.values())
        summary = [dict(font_id=c['font_id'], font=c['path'].name, weight=c['weight'],
                        probability=c['weight'] / total_weight, used=used[i],
                        actual_probability=used[i] / total_chars if total_chars else 0.,
                        metrics=fonts[i].metrics if i in fonts else None)
                   for i, c in enumerate(choices)]
        metadata = dict(font_mix=summary, seed=seed, fallback_characters=fallback,
                        character_fonts=[[token['font'] for token in line] for line in lines])
        if variation: metadata['character_variations'] = character_variations
        return paths, [''.join(t['char'] for t in line) for line in lines], metadata
