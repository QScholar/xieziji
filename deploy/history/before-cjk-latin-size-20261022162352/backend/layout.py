"""Generate bounded paths; the browser never supplies executable G-code."""
import hashlib
import math
import pathlib
from backend.gfont import GFont
from backend.mixing import layout_mixed
from backend.coordinates import Coordinates, paper_svg
from backend.humanization import settings as humanization_settings, active as humanization_active
from backend.wrapping import preferred_space
from backend.device_profiles import control_settings

MAX_FEED = 12000


def number(data, key, default, low, high):
    value = float(data.get(key, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{key} 必须在 {low}–{high} 之间')
    return value


def output_settings(data):
    width = number(data, 'width', 210, 1, 600)
    height = number(data, 'height', 297, 1, 600)
    margin = number(data, 'margin', 30, 0, 600)
    margins = {side: number(data, 'margin_' + side, margin, 0, 600)
               for side in ('top', 'bottom', 'left', 'right')}
    usable = width - margins['left'] - margins['right']
    available_height = height - margins['top'] - margins['bottom']
    if usable <= 0 or available_height <= 0:
        raise ValueError('边距超过纸张可用范围')
    size = number(data, 'size', 5, 2, 20)
    rows = None
    if 'rows' in data:
        rows = number(data, 'rows', 20, 1, 200)
        if rows != int(rows): raise ValueError('行数必须为整数')
        rows = int(rows)
        if rows * size > available_height + .001:
            raise ValueError('指定行数与字大小超过可写高度，请减少行数、字大小或上下边距')
        pitch = available_height / rows
    else:
        pitch = number(data, 'pitch', size * 1.8, size, 600)
    return width, height, margins, size, rows, pitch, usable


def coordinate_preview(data):
    width, height, margins, size, rows, pitch, usable = output_settings(data)
    coordinates = Coordinates.from_data(data, width, height)
    metadata = coordinates.metadata()
    metadata.update(margins=margins, size=size, rows=rows, pitch=pitch,
                    humanization=humanization_settings(data),
                    writable=[usable, height-margins['top']-margins['bottom']])
    return dict(svg=paper_svg(coordinates, margins, rows=rows, pitch=pitch, size=size), metadata=metadata)


def generate(font_path, data, font_choices=None):
    text = data.get('text', '')
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError('请输入 1–3000 个字符')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('文字包含控制字符')
    width, height, margins, size, rows, pitch, usable = output_settings(data)
    spacing = number(data, 'spacing', size * .18, 0, 10)
    feed = number(data, 'feed', 1800, 100, MAX_FEED)
    move_feed = number(data, 'move_feed', 3000, 100, MAX_FEED)
    coordinates = Coordinates.from_data(data, width, height)
    humanization = humanization_settings(data)
    mix_metadata = {}
    if font_choices is None and humanization_active(humanization):
        font_choices = [dict(font_id='primary', path=pathlib.Path(font_path), weight=100.)]
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
                        split = preferred_space(list(line), char)
                        if split > 0:
                            lines.append(line[:split].rstrip())
                            line = line[split + 1:] + char
                            while measure(line) > usable:
                                lines.append(line[:-1]); line = line[-1]
                        else:
                            lines.append(line.rstrip()); line = char.lstrip()
                lines.append(line.rstrip())
            paths = font.text('\n'.join(lines), size, spacing=spacing, line_pitch=pitch)
            metrics = font.metrics
    if rows is not None and len(lines) > rows:
        raise ValueError(f'文字需要 {len(lines)} 行，超过设定的 {rows} 行，请增加行数、宽度或减小字大小/字间距')
    points = [p for s in paths for p in s]
    if not points:
        raise ValueError('没有可绘制的笔画')
    if len(points) > 200000:
        raise ValueError('路径过大，请减少文字')
    xmin = min(x for x, y in points); ymin = min(y for x, y in points)
    paths = [[(round(x - xmin + margins['left'], 3), round(y - ymin + margins['top'], 3)) for x, y in s] for s in paths]
    points = [p for s in paths for p in s]
    bounds = [min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)]
    if bounds[2] > width - margins['right'] + .001 or bounds[3] > height - margins['bottom'] + .001:
        raise ValueError('文字超出纸张边距，请调整范围、边距、行数或字大小')
    paper_paths = paths
    paths = [[coordinates.transform(x, y) for x, y in stroke] for stroke in paper_paths]
    machine_points = [point for stroke in paths for point in stroke]
    machine_bounds = [min(x for x, y in machine_points), min(y for x, y in machine_points),
                      max(x for x, y in machine_points), max(y for x, y in machine_points)]
    control = control_settings(data.get('device_control'))
    up, down = control['pen_up'], control['pen_down']
    commands = ['G21', 'G90', 'G94', f'G1 Z{up:g} F3000', 'G92 X0 Y0']
    last = (0, 0); travel = draw = 0.
    for stroke in paths:
        x, y = stroke[0]; travel += math.dist(last, stroke[0])
        commands += [f'G1 X{x:.3f} Y{y:.3f} F{move_feed:g}', f'G1 Z{down:g} F{move_feed:g}']
        commands += [f'G1 X{x:.3f} Y{y:.3f} F{feed:g}' for x, y in stroke[1:]]
        draw += sum(math.dist(a, b) for a, b in zip(stroke, stroke[1:]))
        commands.append(f'G1 Z{up:g} F{move_feed:g}'); last = stroke[-1]
    travel += math.dist(last, (0, 0))
    commands += [f'G1 Z{up:g} F3000', 'G1 X0 Y0 F3000']
    gcode = '\n'.join(commands) + '\n'
    metadata = dict(font=pathlib.Path(font_path).name, paper=[width, height], margins=margins, margin=data.get('margin', 30), rows=rows,
                    writable=[usable, height-margins['top']-margins['bottom']],
                    size=size, pitch=pitch, spacing=spacing, feed=feed, move_feed=move_feed, text=text, lines=lines,
                    bounds=bounds, machine_bounds=machine_bounds, strokes=len(paths), points=len(points), commands=len(commands),
                    xy_mm=round(travel + draw, 1), metrics=metrics,
                    sha256=hashlib.sha256(gcode.encode('ascii')).hexdigest())
    metadata.update(mix_metadata)
    metadata['humanization'] = humanization
    metadata['device_control'] = control
    if data.get('device_port'):
        metadata['device_port'] = data['device_port']
    metadata.update(coordinates.metadata())
    return dict(metadata=metadata, gcode=gcode, svg=paper_svg(coordinates, margins, paper_paths, rows=rows, pitch=pitch, size=size), paths=paths)
