"""Generate bounded paths; the browser never supplies executable G-code."""
import hashlib
import math
import pathlib
from analysis.gfont import GFont
from plotter.mixing import layout_mixed
from plotter.coordinates import Coordinates, paper_svg


def number(data, key, default, low, high):
    value = float(data.get(key, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{key} 必须在 {low}–{high} 之间')
    return value


def coordinate_preview(data):
    width = number(data, 'width', 210, 80, 297)
    height = number(data, 'height', 297, 80, 297)
    margin = number(data, 'margin', 30, 5, 60)
    if width <= 2 * margin or height <= 2 * margin:
        raise ValueError('边距超过纸张可用范围')
    coordinates = Coordinates.from_data(data, width, height)
    return dict(svg=paper_svg(coordinates, margin), metadata=coordinates.metadata())


def generate(font_path, data, font_choices=None):
    text = data.get('text', '')
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError('请输入 1–3000 个字符')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('文字包含控制字符')
    width = number(data, 'width', 210, 80, 297)
    height = number(data, 'height', 297, 80, 297)
    margin = number(data, 'margin', 30, 5, 60)
    size = number(data, 'size', 5, 2, 20)
    pitch = number(data, 'pitch', size * 1.8, size, 50)
    spacing = number(data, 'spacing', size * .18, 0, 10)
    feed = number(data, 'feed', 1800, 100, 3000)
    coordinates = Coordinates.from_data(data, width, height)
    usable = width - 2 * margin
    if usable <= 0 or height <= 2 * margin:
        raise ValueError('边距超过纸张可用范围')
    mix_metadata = {}
    if font_choices is not None:
        paths, lines, mix_metadata = layout_mixed(font_choices, data, text, size, spacing, pitch, usable, GFont)
        metrics = next(c['metrics'] for c in mix_metadata['font_mix'] if c['metrics'] is not None)
    else:
        lines = []
        with GFont(font_path) as font:
            missing = font.missing(text)
            if missing:
                raise ValueError('字体缺少字符：' + ''.join(missing))
            def measure(line):
                strokes = font.text(line, size, spacing=spacing)
                xs = [x for s in strokes for x, y in s]
                return max(xs, default=0) - min(xs, default=0)
            # Character wrapping also handles Chinese and overlong English words.
            # Prefer the most recent space when an English line fills up.
            for paragraph in text.replace('\r\n', '\n').replace('\r', '\n').expandtabs(4).split('\n'):
                line = ''
                for char in paragraph:
                    trial = line + char
                    if measure(trial) <= usable:
                        line = trial
                    else:
                        if not line or measure(char) > usable:
                            raise ValueError('单个字符超过纸张宽度，请减小字号')
                        split = line.rfind(' ')
                        if split > 0 and char != ' ':
                            lines.append(line[:split].rstrip())
                            line = line[split + 1:] + char
                            while measure(line) > usable:
                                lines.append(line[:-1]); line = line[-1]
                        else:
                            lines.append(line.rstrip()); line = char.lstrip()
                lines.append(line.rstrip())
            paths = font.text('\n'.join(lines), size, spacing=spacing, line_pitch=pitch)
            metrics = font.metrics
    points = [p for s in paths for p in s]
    if not points:
        raise ValueError('没有可绘制的笔画')
    if len(points) > 200000:
        raise ValueError('路径过大，请减少文字')
    xmin = min(x for x, y in points); ymin = min(y for x, y in points)
    paths = [[(round(x - xmin + margin, 3), round(y - ymin + margin, 3)) for x, y in s] for s in paths]
    points = [p for s in paths for p in s]
    bounds = [min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)]
    if bounds[2] > width - margin + .001 or bounds[3] > height - margin + .001:
        raise ValueError('文字超出纸张边距，请减少文字、字号或行距')
    paper_paths = paths
    paths = [[coordinates.transform(x, y) for x, y in stroke] for stroke in paper_paths]
    machine_points = [point for stroke in paths for point in stroke]
    machine_bounds = [min(x for x, y in machine_points), min(y for x, y in machine_points),
                      max(x for x, y in machine_points), max(y for x, y in machine_points)]
    commands = ['G21', 'G90', 'G94', 'G1 Z0 F3000', 'G92 X0 Y0']
    last = (0, 0); travel = draw = 0.
    for stroke in paths:
        x, y = stroke[0]; travel += math.dist(last, stroke[0])
        commands += ['G1 Z0 F3000', f'G1 X{x:.3f} Y{y:.3f} F3000', 'G1 Z10 F3000']
        commands += [f'G1 X{x:.3f} Y{y:.3f} F{feed:g}' for x, y in stroke[1:]]
        draw += sum(math.dist(a, b) for a, b in zip(stroke, stroke[1:]))
        commands.append('G1 Z0 F3000'); last = stroke[-1]
    travel += math.dist(last, (0, 0))
    commands += ['G1 Z0 F3000', 'G1 X0 Y0 F3000']
    gcode = '\n'.join(commands) + '\n'
    metadata = dict(font=pathlib.Path(font_path).name, paper=[width, height], margin=margin,
                    size=size, pitch=pitch, spacing=spacing, feed=feed, text=text, lines=lines,
                    bounds=bounds, machine_bounds=machine_bounds, strokes=len(paths), points=len(points), commands=len(commands),
                    xy_mm=round(travel + draw, 1), metrics=metrics,
                    sha256=hashlib.sha256(gcode.encode('ascii')).hexdigest())
    metadata.update(mix_metadata)
    metadata.update(coordinates.metadata())
    return dict(metadata=metadata, gcode=gcode, svg=paper_svg(coordinates, margin, paper_paths), paths=paths)
