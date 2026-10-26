"""Notebook / character-grid documents, explicit pages and stroke-based formulae."""
import collections
import contextlib
import dataclasses
import hashlib
import math
import pathlib
import random
import re
import secrets

from backend.coordinates import Coordinates, paper_svg
from backend.gfont import GFont
from backend.humanization import settings as human_settings, active, Variation
from backend.layout import MAX_FEED, number, output_settings
from backend.mathtext import Box, items, render, symbol, SYMBOLS
from backend.wrapping import preferred_space


MAX_PAGES = 100
PUNCTUATION = set('，。！？；：、（）【】《》〈〉「」『』“”‘’…—')
NO_START = set('，。！？；：、）》〉】」』”’,.;:!?%)]}')
NO_END = set('（《〈【「『“‘([{')
ASCII_FALLBACK = {'，':',', '、':',', '。':'.', '！':'!', '？':'?', '；':';', '：':':',
                  '（':'(', '）':')', '【':'[', '】':']', '“':'"', '”':'"', '‘':"'", '’':"'"}
PUNCT_PATHS = {
    '(': [[(.65,0),(.35,.2),(.25,.5),(.35,.8),(.65,1)]],
    ')': [[(.35,0),(.65,.2),(.75,.5),(.65,.8),(.35,1)]],
    '[': [[(.7,0),(.3,0),(.3,1),(.7,1)]], ']': [[(.3,0),(.7,0),(.7,1),(.3,1)]],
    '|': [[(.5,0),(.5,1)]], "'": [[(.5,.05),(.45,.25)]],
    '"': [[(.35,.05),(.3,.25)],[(.7,.05),(.65,.25)]],
    '.': [[(.48,.94),(.52,.98)]], ',': [[(.55,.85),(.4,1.05)]],
    ';': [[(.48,.35),(.52,.39)],[(.55,.85),(.4,1.05)]],
    ':': [[(.48,.35),(.52,.39)],[(.48,.85),(.52,.89)]],
    '%': [[(.1,1),(.9,0)],[(.25,0),(.1,.1),(.1,.25),(.25,.35),(.4,.25),(.4,.1),(.25,0)],
          [(.75,.65),(.6,.75),(.6,.9),(.75,1),(.9,.9),(.9,.75),(.75,.65)]],
}


def integer(data, key, default, low, high):
    value = number(data, key, default, low, high)
    if value != int(value):
        raise ValueError(f'{key} 必须为整数')
    return int(value)


def configuration(data):
    mode = data.get('layout_mode', 'notebook')
    if mode not in ('notebook', 'grid'):
        raise ValueError('排版模式必须是笔记或表格')
    base = dict(data)
    sizing = data.get('line_sizing') if mode == 'notebook' else None
    if sizing is not None and sizing not in ('rows', 'size'):
        raise ValueError('笔记排版须选择固定行数或固定字高')
    looseness = number(data, 'line_looseness', 2, 1, 4) if sizing is not None else None
    if mode == 'grid' or sizing is not None:
        base.pop('rows', None)
    if sizing == 'rows':
        base.pop('size', None)
    width, height, margins, size, rows, pitch, usable = output_settings(base)
    rows = integer(data, 'rows', 20, 1, 200) if sizing != 'size' else 1
    available = height - margins['top'] - margins['bottom']
    if mode == 'notebook':
        if sizing == 'rows':
            pitch = available / rows
            size = pitch / looseness
            if not 2 <= size <= 20:
                raise ValueError('自动字高须为 2–20 mm，请调整行数、松紧或上下边距')
        elif sizing == 'size':
            pitch = size * looseness
            rows = math.floor((available + .000001) / pitch)
            if not 1 <= rows <= 200:
                raise ValueError('自动行数须为 1–200 行，请调整字高、松紧或上下边距')
        elif rows * size > available + .001:
            raise ValueError('指定行数与字大小超过可写高度')
        else:
            pitch = available / rows
    raw = data.get('grid', {})
    if not isinstance(raw, dict):
        raise ValueError('表格设置必须为对象')
    grid = dict(columns=integer(raw, 'columns', 20, 1, 100),
                row_gap=number(raw, 'row_gap', 2.9, 0, 100),
                column_gap=number(raw, 'column_gap', 0, 0, 100),
                size_percent=number(raw, 'size_percent', 80, 10, 100),
                padding=number(raw, 'padding', .3, 0, 10), draw_lines=raw.get('draw_lines', False))
    if type(grid['draw_lines']) is not bool:
        raise ValueError('绘制格线开关必须为布尔值')
    grid['cell_width'] = (usable - (grid['columns'] - 1) * grid['column_gap']) / grid['columns']
    grid['cell_height'] = (available - (rows - 1) * grid['row_gap']) / rows
    if mode == 'grid' and min(grid['cell_width'], grid['cell_height']) <= 2 * grid['padding'] + .2:
        raise ValueError('格子尺寸过小，请减少行列数、间隔或内边距')
    pagination = data.get('pagination', 'auto')
    if pagination not in ('auto', 'fixed'):
        raise ValueError('分页方式必须是自动续页或固定每页内容')
    return dict(mode=mode, width=width, height=height, margins=margins, size=size, rows=rows,
                pitch=pitch, usable=usable, available=available, grid=grid, pagination=pagination,
                line_sizing=sizing, line_looseness=looseness,
                punctuation_width=number(data, 'punctuation_width', .8, 0, 2),
                spacing=number(data, 'spacing', size * .18, 0, 10),
                feed=number(data, 'feed', 1800, 100, MAX_FEED),
                move_feed=number(data, 'move_feed', 3000, 100, MAX_FEED), humanization=human_settings(data))


def grid_paths(config):
    g, m = config['grid'], config['margins']
    paths = []
    # Shared cell edges are emitted once, so adjacent cells do not double-ink.
    if not g['column_gap']:
        for row in range(config['rows']):
            y = m['top'] + row * (g['cell_height'] + g['row_gap'])
            paths.append([(m['left'], y), (m['left'] + config['usable'], y)])
            if g['row_gap'] or row == config['rows'] - 1:
                paths.append([(m['left'], y + g['cell_height']), (m['left'] + config['usable'], y + g['cell_height'])])
            for col in range(g['columns'] + 1):
                x = m['left'] + col * g['cell_width']
                paths.append([(x, y), (x, y + g['cell_height'])])
    else:
        edges = set()
        for row in range(config['rows']):
            for col in range(g['columns']):
                x = m['left'] + col * (g['cell_width'] + g['column_gap'])
                y = m['top'] + row * (g['cell_height'] + g['row_gap'])
                corners = [(x, y), (x + g['cell_width'], y), (x + g['cell_width'], y + g['cell_height']), (x, y + g['cell_height'])]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    edge = tuple(sorted((a, b)))
                    if edge not in edges:
                        edges.add(edge); paths.append([a, b])
    return [[(round(x, 3), round(y, 3)) for x, y in stroke] for stroke in paths]


def preview_svg(config, coordinates, paths=(), row_guides=None):
    svg = paper_svg(coordinates, config['margins'], paths,
                    rows=config['rows'] if config['mode'] == 'notebook' else None,
                    pitch=config['pitch'], size=config['size'], row_guides=row_guides)
    if config['mode'] == 'grid':
        lines = ['<g id="grid-guides" stroke="#a2aaa2" stroke-width=".2" fill="none">']
        for stroke in grid_paths(config):
            lines.append('<polyline points="' + ' '.join(f'{x},{y}' for x, y in stroke) + '"/>')
        lines.append('</g>')
        svg = svg.replace('<g id="writing-paths">', '\n'.join(lines) + '\n<g id="writing-paths">')
    return svg


def coordinate_preview(data):
    c = configuration(data)
    coordinates = Coordinates.from_data(data, c['width'], c['height'])
    metadata = coordinates.metadata()
    metadata.update(layout_mode=c['mode'], margins=c['margins'], size=c['size'], rows=c['rows'],
                    line_sizing=c['line_sizing'], line_looseness=c['line_looseness'],
                    pitch=c['pitch'], grid=c['grid'], humanization=c['humanization'],
                    writable=[c['usable'], c['available']], punctuation_width=c['punctuation_width'])
    return dict(svg=preview_svg(c, coordinates), metadata=metadata)


@dataclasses.dataclass
class Token:
    source: str
    box: Box
    advance: float
    fonts: dict = dataclasses.field(default_factory=dict)
    fallback: int = 0
    formula: bool = False


class Glyphs:
    def __init__(self, choices, stack, config, seed):
        self.choices = [dict(c) for c in choices if c['weight'] > 0]
        self.fonts = [stack.enter_context(GFont(c['path'])) for c in self.choices]
        self.rng = random.Random(seed)
        self.config = config
        self.variation = Variation(config['humanization'], seed) if active(config['humanization']) else None
        self.cache = {}
        self.used, self.fallback = collections.Counter(), 0
        demo = pathlib.Path(__file__).resolve().parents[1] / 'gcodeFonts' / 'demo.gfont'
        self.latin = stack.enter_context(GFont(demo)) if demo.is_file() else None

    def box(self, char, size, varied=False):
        options = [i for i, f in enumerate(self.fonts) if ord(char) in f.index]
        fallback = len(options) < len(self.fonts)
        if options:
            index = self.rng.choices(options, weights=[self.choices[i]['weight'] for i in options])[0]
            font, font_id = self.fonts[index], self.choices[index]['font_id']
        elif self.latin and char.isascii() and ord(char) in self.latin.index:
            font, font_id, fallback = self.latin, 'builtin-latin', True
        else:
            equivalent = ASCII_FALLBACK.get(char, char)
            if char in SYMBOLS:
                box = symbol(char, size)
            elif equivalent in PUNCT_PATHS:
                paths = [[(x * size * .5, y * size) for x, y in stroke] for stroke in PUNCT_PATHS[equivalent]]
                box = Box(size * .5, size * 1.05, size, paths)
            else:
                raise ValueError('所选字体缺少字符：' + char)
            self.used['builtin-symbols'] += 1; self.fallback += 1
            return box
        self.used[font_id] += 1; self.fallback += int(fallback)
        key = (font_id, char, size)
        if key not in self.cache:
            code=ord(char)
            chinese=(0x3400 <= code <= 0x9fff or 0xf900 <= code <= 0xfaff or
                     0x20000 <= code <= 0x323af or char in PUNCTUATION)
            reference=font.cjk_metrics if self.config['mode']=='notebook' and chinese else None
            paths = font.text(char, size, spacing=0, reference_metrics=reference)
            points = [p for s in paths for p in s]
            if not points:
                raise ValueError('字符没有可绘制笔画：' + char)
            xs, ys = zip(*points)
            self.cache[key] = (paths, max(xs) - min(xs), min(0., min(ys)), max(size, max(ys)))
        paths, width, top, bottom = self.cache[key]
        baseline = size - top
        gap = self.config['spacing']
        if varied and self.variation:
            value = self.variation.glyph(dict(paths=paths, bounds=(0., width), advance=width + self.config['spacing']), size, self.config['spacing'])
            paths = value['paths']
            gap = value['variation']['gap']
            points = [p for s in paths for p in s]
            # Keep the original line reference when a glyph moves vertically.
            xs, ys = zip(*points); top, bottom = min(top, min(ys)), max(bottom, max(ys))
            width, baseline = max(xs) - min(xs), size - top
        return Box(width, bottom - top, baseline, [[(x, y - top) for x, y in s] for s in paths], gap)

    def token(self, source, formula=False, size=None):
        size = self.config['size'] if size is None else size
        before, fallback = self.used.copy(), self.fallback
        if not formula and source.isspace():
            box = Box(size * .5, size, size, [])
        else:
            box = render(source, size, self.box) if formula else self.box(source, size, varied=True)
        cell = max(box.width, size * self.config['punctuation_width']) if not formula and source in PUNCTUATION else box.width
        # Center punctuation in its reserved space without stretching its ink.
        if cell > box.width:
            box = Box(cell, box.height, box.baseline, box.placed((cell - box.width) / 2), box.spacing)
        return Token(('$' + source + '$') if formula else source, box, cell + (box.spacing if box.spacing is not None and not formula else self.config['spacing']),
                     dict(self.used - before), self.fallback - fallback, formula)

    def summary(self, tokens):
        counts = collections.Counter()
        for token in tokens:
            counts.update(token.fonts)
        total, weight = sum(counts.values()), sum(c['weight'] for c in self.choices)
        result = [dict(font_id=c['font_id'], font=c['path'].name, weight=c['weight'],
                       probability=c['weight'] / weight, used=counts[c['font_id']],
                       actual_probability=counts[c['font_id']] / total if total else 0., metrics=f.metrics)
                  for c, f in zip(self.choices, self.fonts)]
        for font_id, name in [('builtin-latin', '内置单线字母与数字'), ('builtin-symbols', '内置单线数学符号')]:
            if counts[font_id]:
                result.append(dict(font_id=font_id, font=name, weight=0, probability=0., used=counts[font_id],
                                   actual_probability=counts[font_id] / total, metrics=None))
        return result


def markdown_text(text):
    # Convert common prose markup; formula content passes through unchanged.
    result = []
    for kind, source in items(text):
        if kind != 'text':
            result.append(('$$' if kind == 'display' else '$') + source + ('$$' if kind == 'display' else '$'))
            continue
        source = re.sub(r'^ {0,3}#{1,6}\s+', '', source, flags=re.M)
        source = re.sub(r'!?\[([^\]\n]*)\]\([^\)\n]*\)', r'\1', source)
        source = re.sub(r'\*\*([^*\n]+)\*\*|__([^_\n]+)__', lambda m: m[1] or m[2], source)
        result.append(source.replace('$', '\\$'))
    return ''.join(result)


def tokenize(text, glyphs, config):
    result = []
    after_display = False
    for kind, source in items(text):
        if kind != 'text':
            token = glyphs.token(source, formula=True)
            if kind == 'display' and result and result[-1] != '\n':
                result.append('\n')
            result.append(token)
            if kind == 'display': result.append('\n')
            after_display = kind == 'display'
        else:
            if after_display and source.startswith('\n'):
                source = source[1:]
            after_display = False
            for char in source:
                result.append('\n' if char == '\n' else glyphs.token(char))
    return result


def text_width(tokens):
    return sum(t.advance for t in tokens[:-1]) + (tokens[-1].box.width if tokens else 0.)


def plain_line_drift(config):
    human=config.get('humanization',{})
    drift=human.get('baseline',0.) if human.get('enabled') and human.get('baseline_enabled',True) else 0.
    return min(drift,max(0.,(config['pitch']-config['size'])/2))


def prepare_notebook_tokens(tokens, config):
    """Fit a source-page section once, before measuring any automatic wraps.

    Manual newlines do not trigger another size decision. Every ordinary line
    uses the same scale, while formulas retain their own height and scale.
    """
    if config.get('line_sizing') is None:
        return tokens
    budget=config['pitch']-2*plain_line_drift(config)
    plain=[t for t in tokens if t!='\n' and not t.formula]
    height=max((t.box.baseline for t in plain),default=0.)+max((t.box.height-t.box.baseline for t in plain),default=0.)
    scale=min(1.,budget/height) if height else 1.
    result=[]
    for token in tokens:
        if token=='\n' or token.formula or scale==1.:
            result.append(token)
        else:
            box=token.box
            result.append(dataclasses.replace(token,box=Box(box.width*scale,box.height*scale,
                box.baseline*scale,box.placed(scale=scale),box.spacing),advance=token.advance*scale))
    return result


def protected_word_ends(tokens):
    """Protect ordinary Latin words/compounds and measured numbers with units."""
    text=''.join(t if isinstance(t,str) else t.source if len(t.source)==1 else '\ufffc' for t in tokens)
    units=r'(?:GHz|MHz|kHz|Hz|km|cm|mm|nm|μm|um|ms|mV|kV|mA|dB|°C|℃|m|s|V|A|W|%|毫米|厘米|千米|纳米|微米|毫秒|分钟|小时|赫兹|度|米|秒|伏|瓦|安)'
    pattern=r"[A-Za-z]+(?:[-'’][A-Za-z]+)*|[+−-]?[0-9]+(?:[.,][0-9]+)*(?:[ \u00a0]?"+units+r')?'
    ends={}
    for match in re.finditer(pattern,text):
        end=match.end()
        while end<len(text) and text[end] in NO_START:
            end+=1
        ends[match.start()]=end
    return ends


def notebook_rows(tokens, config):
    tokens=prepare_notebook_tokens(tokens,config)
    word_ends=protected_word_ends(tokens)
    rows, line = [], []
    def flush():
        nonlocal line
        rows.append(line); line = []
    for index,token in enumerate(tokens):
        if token == '\n':
            flush(); continue
        if token.box.width > config['usable'] + .0001:
            raise ValueError('单个字符或公式超过可写宽度，请减小字高或拆分公式')
        word=tokens[index:word_ends[index]] if index in word_ends else [token]
        trial=word if text_width(word)<=config['usable'] else [token]
        if line and text_width(line + trial) > config['usable']:
            # Move a complete opening/closing punctuation pair when possible.
            original = line[:]
            carry = []
            if token.source in NO_START:
                carry.insert(0, line.pop())
                while line and line[-1].source in NO_END:
                    carry.insert(0, line.pop())
            elif line[-1].source in NO_END:
                carry.insert(0, line.pop())
                while line and line[-1].source in NO_END:
                    carry.insert(0, line.pop())
            else:
                space = preferred_space([t.source for t in line], token.source)
                if space > 0:
                    carry = line[space + 1:]; line = line[:space]
                    if text_width(carry + [token]) > config['usable']:
                        line, carry = original, []
            if carry and text_width(carry + [token]) > config['usable']:
                raise ValueError('标点与相邻字符无法在同一行排下，请增加宽度或减小字高')
            if line: flush()
            line = carry
        line.append(token)
    if line or not rows:
        flush()
    return rows


def notebook_pages(tokens, config, glyphs):
    rows = notebook_rows(tokens, config)
    pages, paths, page_tokens, lines = [], [], [], []
    def default_guides():
        return {i: round(config['margins']['top'] + i * config['pitch'] + config['size'], 3) for i in range(config['rows'])}
    guides = default_guides()
    row_cursor = 0
    for line in rows:
        baseline = max((t.box.baseline for t in line), default=config['size'])
        descent = max((t.box.height - t.box.baseline for t in line), default=0.)
        height = baseline + descent
        drift_amplitude = glyphs.variation.amplitude('baseline') if glyphs.variation else 0.
        if config['line_sizing'] is not None and not any(t.formula for t in line):
            drift_amplitude=plain_line_drift(config)
        height += 2 * drift_amplitude
        slots = max(1, math.ceil((height - .0001) / config['pitch']))
        if slots > config['rows'] or height > config['available'] + .001:
            raise ValueError('公式或字形高度超过单页可写范围，请减小字号或增加页面高度')
        if row_cursor + slots > config['rows']:
            pages.append((paths, lines, page_tokens, list(guides.values()))); paths, lines, page_tokens, row_cursor = [], [], [], 0
            guides = default_guides()
        x, y = config['margins']['left'], config['margins']['top'] + row_cursor * config['pitch']
        phase = glyphs.variation.line_phase() if glyphs.variation else 0.
        # Smooth line drift gets reserved space instead of crossing page margins.
        row_paths = []
        for token in line:
            drift = drift_amplitude * math.sin(phase + math.pi * (x - config['margins']['left']) / config['usable'])
            row_paths.extend(token.box.placed(x, y + baseline - token.box.baseline + drift + drift_amplitude))
            x += token.advance
        paths.extend(row_paths)
        guides[row_cursor] = round(max((py for stroke in row_paths for _, py in stroke), default=y + config['size']), 3)
        for occupied in range(row_cursor + 1, row_cursor + slots):
            guides.pop(occupied, None)
        page_tokens.extend(line); lines.append(''.join(t.source for t in line)); row_cursor += slots
    pages.append((paths, lines, page_tokens, list(guides.values())))
    return pages


def character_grid_pages(tokens, config):
    g, m = config['grid'], config['margins']
    pages, paths, page_tokens, lines, line = [], [], [], [], []
    row = col = 0
    pending_row = False
    def next_row():
        nonlocal row, col, line, pending_row
        lines.append(''.join(line)); line = []; row += 1; col = 0; pending_row = False
    def next_page():
        nonlocal paths, page_tokens, lines, row
        pages.append((paths, lines, page_tokens)); paths, page_tokens, lines, row = [], [], [], 0
    for token in tokens:
        if token == '\n':
            # A hard break after a completely full row should not add a blank row.
            next_row()
            continue
        if pending_row:
            next_row()
        if row >= config['rows']:
            next_page()
        aw, ah = g['cell_width'] - 2 * g['padding'], g['cell_height'] - 2 * g['padding']
        scale = ah * g['size_percent'] / 100 / max(token.box.height, .001)
        span = 1
        if token.formula:
            span = max(1, math.ceil((token.box.width * scale + 2 * g['padding'] + g['column_gap']) / (g['cell_width'] + g['column_gap'])))
            if span > g['columns']:
                raise ValueError('公式超过一整行格子宽度，请拆分公式或增加列数')
        else:
            scale = min(scale, aw / max(token.box.width, .001))
        if col + span > g['columns']:
            next_row()
            if row >= config['rows']: next_page()
        width = span * g['cell_width'] + (span - 1) * g['column_gap']
        x = m['left'] + col * (g['cell_width'] + g['column_gap']) + (width - token.box.width * scale) / 2
        y = m['top'] + row * (g['cell_height'] + g['row_gap']) + (g['cell_height'] - token.box.height * scale) / 2
        paths.extend(token.box.placed(x, y, scale)); page_tokens.append(token); line.append(token.source)
        col += span
        pending_row = col == g['columns']
    if line: lines.append(''.join(line))
    if paths or lines or not pages: pages.append((paths, lines, page_tokens))
    return pages


def emit(config, data, paper_paths, lines, tokens, glyphs, seed, source_page, row_guides=None):
    coordinates = Coordinates.from_data(data, config['width'], config['height'])
    if config['mode'] == 'grid' and config['grid']['draw_lines']:
        paper_paths = grid_paths(config) + paper_paths
    paper_paths = [[(round(x, 3), round(y, 3)) for x, y in stroke] for stroke in paper_paths]
    points = [p for s in paper_paths for p in s]
    if len(points) > 200000:
        raise ValueError('单页路径超过 20 万点，请减少单页内容')
    if points:
        bounds = [min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)]
        m = config['margins']
        if (bounds[0] < m['left'] - .001 or bounds[1] < m['top'] - .001 or
                bounds[2] > config['width'] - m['right'] + .001 or bounds[3] > config['height'] - m['bottom'] + .001):
            raise ValueError('本页字形超出边距，请减小字号或仿真幅度')
    else:
        bounds = [config['margins']['left'], config['margins']['top']] * 2
    paths = [[coordinates.transform(x, y) for x, y in stroke] for stroke in paper_paths]
    machine_points = [p for s in paths for p in s]
    machine_bounds = ([min(x for x,y in machine_points), min(y for x,y in machine_points),
                       max(x for x,y in machine_points), max(y for x,y in machine_points)] if machine_points else [0., 0., 0., 0.])
    commands = ['G21', 'G90', 'G94', 'G1 Z0 F3000', 'G92 X0 Y0']
    last, travel, draw = (0., 0.), 0., 0.
    for stroke in paths:
        x, y = stroke[0]; travel += math.dist(last, stroke[0])
        commands.extend([f'G1 X{x:.3f} Y{y:.3f} F{config["move_feed"]:g}', f'G1 Z10 F{config["move_feed"]:g}'])
        commands.extend(f'G1 X{x:.3f} Y{y:.3f} F{config["feed"]:g}' for x,y in stroke[1:])
        commands.append(f'G1 Z0 F{config["move_feed"]:g}'); last = stroke[-1]
        draw += sum(math.dist(a,b) for a,b in zip(stroke, stroke[1:]))
    commands.extend(['G1 Z0 F3000', 'G1 X0 Y0 F3000'])
    gcode = '\n'.join(commands) + '\n'
    metadata = coordinates.metadata()
    metadata.update(layout_mode=config['mode'], margins=config['margins'], writable=[config['usable'], config['available']],
                    line_sizing=config['line_sizing'], line_looseness=config['line_looseness'], row_guides=row_guides,
                    rows=config['rows'], size=config['size'], pitch=config['pitch'], spacing=config['spacing'], feed=config['feed'], move_feed=config['move_feed'],
                    grid=config['grid'], punctuation_width=config['punctuation_width'], humanization=config['humanization'],
                    seed=seed, source_page=source_page, lines=lines, text='\n'.join(lines), bounds=bounds, machine_bounds=machine_bounds,
                    strokes=len(paths), points=len(points), commands=len(commands), xy_mm=round(travel + draw + math.dist(last,(0,0)), 1),
                    font_mix=glyphs.summary(tokens), fallback_characters=sum(t.fallback for t in tokens),
                    empty=not bool(points), formula_count=sum(t.formula for t in tokens),
                    sha256=hashlib.sha256(gcode.encode('ascii')).hexdigest())
    return dict(metadata=metadata, paths=paths, gcode=gcode, svg=preview_svg(config, coordinates, paper_paths, row_guides))


def generate_document(font_path, data, choices=None):
    config = configuration(data)
    raw_pages = data.get('pages', [dict(text=data.get('text', ''))])
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= MAX_PAGES:
        raise ValueError('文档需要 1–100 个预设内容页')
    pages = []
    for page in raw_pages:
        if not isinstance(page, dict) or not isinstance(page.get('text'), str):
            raise ValueError('每页内容必须是文本')
        text = page['text'].replace('\r\n', '\n').replace('\r', '\n')
        if any(ord(char) < 32 and char not in '\n\t\f' for char in text):
            raise ValueError('内容包含控制字符')
        pages.append(text)
    if sum(map(len, pages)) > 30000 or not any(p.strip() for p in pages):
        raise ValueError('请输入合计 1–30000 个字符')
    if data.get('input_format', 'plain') not in ('plain', 'markdown'):
        raise ValueError('导入格式必须为文本或 Markdown')
    seed = data.get('seed', secrets.token_hex(8))
    if isinstance(seed, bool) or not isinstance(seed, (str, int)) or not 1 <= len(str(seed)) <= 64:
        raise ValueError('随机种子必须是 1–64 位文本或整数')
    seed = str(seed)
    choices = choices or [dict(font_id='primary', path=pathlib.Path(font_path), weight=100.)]
    results, point_count = [], 0
    with contextlib.ExitStack() as stack:
        glyphs = Glyphs(choices, stack, config, seed)
        for index, text in enumerate(pages):
            sections = re.split(r'\f|\\(?:newpage|pagebreak)\b', text)
            for section in sections:
                if data.get('input_format') == 'markdown': section = markdown_text(section)
                tokens = tokenize(section.expandtabs(4), glyphs, config)
                generated = (notebook_pages(tokens, config, glyphs) if config['mode'] == 'notebook'
                             else character_grid_pages(tokens, config))
                if config['pagination'] == 'fixed' and len(generated) > 1:
                    raise ValueError(f'预设第 {index + 1} 页内容溢出，需要 {len(generated)} 页；请减少内容或选择自动续页')
                for entry in generated:
                    paths, lines, page_tokens = entry[:3]
                    if len(results) >= MAX_PAGES:
                        raise ValueError('排版结果超过 100 页，请拆分文档')
                    result = emit(config, data, paths, lines, page_tokens, glyphs, seed, index + 1, entry[3] if len(entry) > 3 else None)
                    point_count += result['metadata']['points']
                    if point_count > 2000000:
                        raise ValueError('文档路径超过 200 万点，请拆分文档')
                    results.append(result)
    for index, result in enumerate(results):
        result['metadata'].update(page_number=index + 1, page_count=len(results))
    return results
