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
from backend.mixing import PUNCTUATION, is_cjk, missing_message, normalized, scoped, scope_shares, script_of
from backend.wrapping import preferred_space
from backend.device_profiles import control_settings
from backend.glyph_fallback import GlyphFallback, Stroke, moved, fallback_metadata
from backend.font_balance import FontBalance
from backend import repeat_fonts


MAX_PAGES = 100
NO_START = set('，。！？；：、）》〉】」』”’,.;:!?%)]}')
NO_END = set('（《〈【「『“‘([{')
SUPERSCRIPT = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ', '0123456789+-=()n'))
SUBSCRIPT = dict(zip('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₔ', '0123456789+-=()aeoxe'))
HTML_ENTITIES = {'&lt;':'<', '&gt;':'>', '&amp;':'&', '&quot;':'"', '&#39;':"'", '&nbsp;':' '}
ASCII_FALLBACK = {'，':',', '、':',', '。':'.', '！':'!', '？':'?', '；':';', '：':':',
                  '（':'(', '）':')', '【':'[', '】':']', '“':'"', '”':'"', '‘':"'", '’':"'"}
def cjk_character(char):
    """CJK ideographs, kana-width punctuation and fullwidth forms share the Chinese em box."""
    return is_cjk(char)


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


def configuration(data, strict=True):
    """Parse layout parameters; `strict=False` skips derived-layout limits.

    The line-pitch prediction runs before the user can pick a looseness that
    satisfies those limits, so it must still read the requested heights and rows.
    """
    mode = data.get('layout_mode', 'notebook')
    if mode not in ('notebook', 'grid'):
        raise ValueError('排版模式必须是笔记或表格')
    smart_balance = data.get('smart_balance', False)
    if type(smart_balance) is not bool:
        raise ValueError('智能大小均衡开关必须为布尔值')
    letter_scale = number(data, 'letter_scale', 100, 50, 150)
    digit_scale = number(data, 'digit_scale', 100, 50, 150)
    placement=data.get('line_placement','rows') if mode=='notebook' else 'rows'
    if placement not in ('rows','ink'):raise ValueError('笔记行布局方式无效')
    base = dict(data)
    sizing = data.get('line_sizing') if mode == 'notebook' else None
    if sizing is not None and sizing not in ('rows', 'size'):
        raise ValueError('笔记排版须选择固定行数或固定字高')
    looseness = number(data, 'line_looseness', 2, 1, 4) if sizing is not None else None
    # Compact prose may cross nominal row boxes; formulas reserve actual ink space.
    allow_overlap = data.get('allow_overlap', False)
    if type(allow_overlap) is not bool:
        raise ValueError('允许字重叠开关必须为布尔值')
    allow_overlap = allow_overlap and mode == 'notebook' and sizing is not None and placement!='ink'
    if mode == 'grid' or sizing is not None:
        base.pop('rows', None)
    # A legacy fixed-row request has no size ratio to retain; its supplied size is derived away.
    requested_size = 5 if sizing=='rows' and 'latin_size' not in data else number(data, 'size', 5, 2, 20)
    # Grid cells scale every glyph to the cell, so the two heights only differ in notebook mode.
    ratio = (number(data, 'latin_size', requested_size, 2, 20) / requested_size
             if mode == 'notebook' else 1.)
    if sizing == 'rows':
        base.pop('size', None)
    width, height, margins, size, rows, pitch, usable = output_settings(base)
    latin_size = size * ratio
    rows = integer(data, 'rows', 20, 1, 200) if sizing != 'size' else 1
    available = height - margins['top'] - margins['bottom']
    if mode == 'notebook':
        if sizing == 'rows':
            pitch = available / rows
            # The Chinese height fills the automatic line box; English keeps the chosen ratio.
            size = pitch / looseness
            latin_size = size * ratio
            # Overlap mode exists to go past these limits on purpose.
            if strict and not allow_overlap and (not 2 <= size <= 20 or not 2 <= latin_size <= 20):
                raise ValueError('自动字高须使中英文都在 2–20 mm，请调整行数、松紧或上下边距')
            if strict and not allow_overlap and latin_size > pitch + .001:
                raise ValueError('英文字高超过行距，请减小英文字高或放宽行间松紧')
        elif sizing == 'size':
            pitch = max(size, latin_size) * looseness
            rows = math.floor((available + .000001) / pitch)
            if strict and not allow_overlap and not 1 <= rows <= 200:
                raise ValueError('自动行数须为 1–200 行，请调整字高、松紧或上下边距')
            rows = max(1, rows)
        else:
            if strict and not allow_overlap and rows * max(size, latin_size) > available + .001:
                raise ValueError('指定行数与字大小超过可写高度')
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
    return dict(mode=mode, width=width, height=height, margins=margins, size=size, latin_size=latin_size, rows=rows,
                smart_balance=smart_balance, letter_scale=letter_scale, digit_scale=digit_scale,
                pitch=pitch, usable=usable, available=available, grid=grid, pagination=pagination,
                line_sizing=sizing, line_looseness=looseness, allow_overlap=allow_overlap,line_placement=placement,
                punctuation_width=number(data, 'punctuation_width', .8, 0, 2),
                spacing=number(data, 'spacing', size * .18, 0, 10),
                feed=number(data, 'feed', 10000, 100, MAX_FEED),
                device_port=data.get('device_port'), device_control=control_settings(data.get('device_control')),
                move_feed=number(data, 'move_feed', 10000, 100, MAX_FEED), humanization=human_settings(data))


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
                    pitch=config['pitch'], size=max(config['size'], config['latin_size']), row_guides=row_guides)
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
    metadata['device_control'] = c['device_control']
    metadata.update(layout_mode=c['mode'], margins=c['margins'], size=c['size'], latin_size=c['latin_size'], rows=c['rows'],
                    line_sizing=c['line_sizing'], line_looseness=c['line_looseness'],
                    allow_overlap=c['allow_overlap'],line_placement=c['line_placement'],
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
    balance_scale: float = 1.
    serial: int | None = None
    nominal_size: float | None = None
    display: bool = False


class Glyphs:
    def __init__(self, choices, stack, config, seed, fallback_library=None):
        self.choices = [c for c in normalized(choices) if c['weight'] > 0]
        self.fonts = [stack.enter_context(GFont(c['path'])) for c in self.choices]
        self.seed = str(seed)
        self.font_plan = {}
        self.token_sequence, self.box_sequence, self.token_id = 0, 0, None
        self.config = config
        self.balance = FontBalance(self.choices, self.fonts, config)
        self.last_balance_scale = 1.
        self.variation = Variation(config['humanization'], seed) if active(config['humanization']) else None
        self.glyph_variation = self.variation
        self.cache = {}
        self.available={}
        self.used, self.fallback = collections.Counter(), 0
        self.external_occurrence=0
        self.resolver=GlyphFallback(fallback_library,choices,stack,GFont) if fallback_library else None
        self.external_fonts={}
        demo = pathlib.Path(__file__).resolve().parents[1] / 'gcodeFonts' / 'demo.gfont'
        self.latin = stack.enter_context(GFont(demo)) if demo.is_file() else None

    def pool(self, char):
        """Selected fonts allowed to render this character's script."""
        script = script_of(char)
        return [i for i, c in enumerate(self.choices) if scoped(c['scope'], script)]

    def box(self, char, size, varied=False):
        self.last_balance_scale = 1.
        self.box_sequence += 1
        occurrence = f'{self.token_id}:{self.box_sequence}'
        pool = self.pool(char)
        if not pool:
            # No selected font serves this script; never substitute another script's font.
            raise ValueError(missing_message([char], {char: pool}))
        if char not in self.available:
            self.available[char]=[i for i in pool if ord(char) in self.fonts[i].index and any(len(s)>=2 for s in self.fonts[i].glyph(char).strokes())]
        options=self.available[char]
        fallback=len(options)<len(pool)
        replacement=self.resolver.find(char,[self.choices[i]['font_id'] for i in pool]) if not options and self.resolver else None
        info=None
        if options:
            rng = random.Random(f'font:{self.seed}:{occurrence}')
            index = rng.choices(options, weights=[self.choices[i]['weight'] for i in options])[0]
            selected = self.font_plan.get(occurrence)
            index = next((i for i in options if self.choices[i]['font_id'] == selected), index)
            font, font_id = self.fonts[index], self.choices[index]['font_id']
        elif replacement:
            font,info=replacement
            font_id=info['font_id'];self.external_fonts[font_id]=(font,info['font'])
        elif self.latin and char.isascii() and ord(char) in self.latin.index:
            font, font_id, fallback = self.latin, 'builtin-latin', True
        else:
            equivalent = ASCII_FALLBACK.get(char, char)
            if char in SUPERSCRIPT:
                inner = self.box(SUPERSCRIPT[char], size * .62)
                lift = size * .45
                return Box(inner.width, inner.height + lift, inner.baseline + lift, inner.paths)
            if char in SUBSCRIPT:
                inner = self.box(SUBSCRIPT[char], size * .62)
                drop = size * .22
                return Box(inner.width, inner.height + drop, inner.baseline,
                           [moved(stroke,((x,y+drop) for x,y in stroke)) for stroke in inner.paths])
            if char in SYMBOLS:
                box = symbol(char, size)
            elif equivalent in PUNCT_PATHS:
                paths = [[(x * size * .5, y * size) for x, y in stroke] for stroke in PUNCT_PATHS[equivalent]]
                box = Box(size * .5, size * 1.05, size, paths)
            else:
                raise ValueError(missing_message([char], {char: pool}))
            self.used['builtin-symbols'] += 1; self.fallback += 1
            return box
        self.used[font_id] += 1; self.fallback += int(fallback)
        self.last_balance_scale = self.balance.factor(font_id, font, char)
        size *= self.last_balance_scale
        key = (font_id, char, size)
        if key not in self.cache:
            reference=font.cjk_metrics if self.config['mode']=='notebook' and is_cjk(char) else None
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
            value = self.glyph_variation.glyph(dict(paths=paths, bounds=(0., width), advance=width + self.config['spacing']), size, self.config['spacing'])
            paths = value['paths']
            gap = value['variation']['gap']
            points = [p for s in paths for p in s]
            # Keep the original line reference when a glyph moves vertically.
            xs, ys = zip(*points); top, bottom = min(top, min(ys)), max(bottom, max(ys))
            width, baseline = max(xs) - min(xs), size - top
        if info:
            self.external_occurrence+=1
            info=dict(info,occurrence=self.external_occurrence)
        glyph_info = dict(occurrence=occurrence, token=self.token_id, character=char, font_id=font_id, font=font.path.name,
                          options=[self.choices[i]['font_id'] for i in options] if options else [font_id])
        return Box(width, bottom - top, baseline,
                   [Stroke([(x,y-top) for x,y in s],info,glyph_info) for s in paths], gap)

    def token(self, source, formula=False, size=None, serial=None, display=False):
        # Formulas keep the base height; plain text picks the Chinese or Latin height per character.
        size = (self.config['latin_size'] if not (formula or cjk_character(source)) else self.config['size']) if size is None else size
        if serial is None:
            serial = self.token_sequence
            self.token_sequence += 1
        self.token_id, self.box_sequence = serial, 0
        if self.variation:
            self.glyph_variation = Variation(self.config['humanization'], self.seed + ':token:' + str(serial))
        before, fallback = self.used.copy(), self.fallback
        self.last_balance_scale = 1.
        if not formula and source.isspace():
            box = Box(size * .5, size, size, [])
        else:
            box = render(source, size, self.box, display=display) if formula else self.box(source, size, varied=True)
        cell = max(box.width, size * self.config['punctuation_width']) if not formula and source in PUNCTUATION else box.width
        # Center punctuation in its reserved space without stretching its ink.
        if cell > box.width:
            box = Box(cell, box.height, box.baseline, box.placed((cell - box.width) / 2), box.spacing)
        if formula and cell > self.config['usable']:
            # A display formula cannot wrap; shrink it to the writable width.
            fit = self.config['usable'] / cell
            if fit < .5:
                raise ValueError('公式过宽，请拆分公式或减小字高')
            box = Box(box.width * fit, box.height * fit, box.baseline * fit, box.placed(scale=fit), box.spacing)
            cell = box.width
        # Script-only formulas come from HTML <sub>/<sup> in prose: they bind to the preceding
        # glyph, so they must not inherit the formula gap.
        gap = 0. if formula and source.lstrip()[:1] in ('_', '^') else (box.spacing if not formula and box.spacing is not None else self.config['spacing'])
        return Token(('$' + source + '$') if formula else source, box, cell + gap,
                     dict(self.used - before), self.fallback - fallback, formula,
                     self.last_balance_scale if not formula else 1., serial, size, display)

    def summary(self, tokens):
        counts = collections.Counter()
        for token in tokens:
            counts.update(token.fonts)
        total, weight = sum(counts.values()), sum(c['weight'] for c in self.choices)
        shares = scope_shares(self.choices)
        result = [dict(font_id=c['font_id'], font=c['path'].name, weight=c['weight'], scope=c['scope'],
                       probability=c['weight'] / weight, probabilities=shares[c['font_id']],
                       used=counts[c['font_id']],
                       actual_probability=counts[c['font_id']] / total if total else 0., metrics=f.metrics if counts[c['font_id']] else None)
                  for c, f in zip(self.choices, self.fonts)]
        for font_id, name in [('builtin-latin', '内置单线字母与数字'), ('builtin-symbols', '内置单线数学符号')]:
            if counts[font_id]:
                result.append(dict(font_id=font_id, font=name, weight=0, probability=0., used=counts[font_id],
                                   actual_probability=counts[font_id] / total, metrics=None))
        for font_id,(font,name) in self.external_fonts.items():
            if counts[font_id]:result.append(dict(font_id=font_id,font=name,weight=0,probability=0.,used=counts[font_id],
                actual_probability=counts[font_id]/total,metrics=font.metrics,external_fallback=True))
        return result


def markdown_text(text):
    # Convert common prose markup; formula content passes through unchanged.
    # Drop a YAML front matter block, but only when it starts with a `key: value` line.
    text = re.sub(r'\A\ufeff?---[ \t]*\n[A-Za-z_][^\n:]*:[^\n]*\n(?:[^\n]*\n)*?---[ \t]*\n', '', text, count=1)
    result = []
    for kind, source in items(text):
        if kind != 'text':
            result.append(('$$' if kind == 'display' else '$') + source + ('$$' if kind == 'display' else '$'))
            continue
        source = re.sub(r'^ {0,3}#{1,6}\s+', '', source, flags=re.M)
        source = re.sub(r'!?\[([^\]\n]*)\]\([^\)\n]*\)', r'\1', source)
        source = re.sub(r'\*\*([^*\n]+)\*\*|__([^_\n]+)__', lambda m: m[1] or m[2], source)
        for entity, char in HTML_ENTITIES.items():
            source = source.replace(entity, char)
        source = re.sub(r'</?(?:span|small|big|em|i|b|strong|u|mark|code|kbd|var|samp|font)[^>]*>', '', source)
        # Escape literal dollars first, then emit the script delimiters we introduce.
        source = source.replace('$', '\\$')
        source = re.sub(r'<sub>(.*?)</sub>', r'$_{\1}$', source, flags=re.S)
        source = re.sub(r'<sup>(.*?)</sup>', r'$^{\1}$', source, flags=re.S)
        result.append(source)
    return ''.join(result)


def tokenize(text, glyphs, config):
    result = []
    after_display = False
    for kind, source in items(text):
        if kind != 'text':
            token = glyphs.token(source, formula=True, display=kind == 'display')
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


def human_amplitude(human, key):
    """Enabled variation amplitude, mirroring humanization.Variation.amplitude."""
    return human.get(key, 0.) if human.get('enabled') and human.get(key + '_enabled', True) else 0.


def drift_limit(config):
    """Baseline wobble the layout reserves above and below every plain line."""
    return human_amplitude(config.get('humanization', {}), 'baseline')


def plain_line_drift(config):
    return min(drift_limit(config), max(0., (config['pitch'] - max(config['size'], config.get('latin_size',config['size']))) / 2))


def ink_shape(font, char):
    """Raw ink box of one glyph in units of the nominal height.

    Returns (top, bottom, width) measured from the nominal box top, with y
    pointing down. These are the true ink edges, not the ones Glyphs.box clamps
    to the nominal box, because humanization rotates the ink itself.
    """
    if ord(char) not in font.index:
        return None
    glyph = font.glyph(char)
    if not glyph.coordinates:
        return None
    reference = font.cjk_metrics if is_cjk(char) else font.metrics
    scale = 1. / reference['reference_height']
    left, top, right, bottom = glyph.bounds
    return ((top - reference['reference_top']) * scale,
            (bottom - reference['reference_top']) * scale,
            (right - left) * scale, True)


# Glyph parsing dominates a prediction, so shapes are cached per font file.
# A font update rewrites the file and changes its mtime, which invalidates them.
INK_CACHE = {}
INK_CACHE_LIMIT = 100000


def cached_ink_shape(font, char):
    key = getattr(font, '_ink_key', None)
    if key is None:
        try:
            key = (str(font.path), font.path.stat().st_mtime_ns)
        except OSError:
            key = (str(font.path), 0)
        font._ink_key = key
    entry = (key, char)
    if entry not in INK_CACHE:
        if len(INK_CACHE) >= INK_CACHE_LIMIT:
            INK_CACHE.clear()
        INK_CACHE[entry] = ink_shape(font, char)
    return INK_CACHE[entry]


def fallback_ink_shape(char):
    """Ink shape of the built-in stand-ins Glyphs.box substitutes for missing glyphs.

    These are returned before the humanization transform, so `varied` is false.
    """
    if char in SUPERSCRIPT or char in SUBSCRIPT:
        # Glyphs.box nests the base glyph at 0.62× and lifts or drops the result;
        # the outer box is the inner ink shifted, never scaled by the lift itself.
        inner = fallback_ink_shape((SUPERSCRIPT if char in SUPERSCRIPT else SUBSCRIPT)[char])
        if inner is None:
            return None
        lift = .45 if char in SUPERSCRIPT else .22
        return .62 * inner[0] + lift - .45, 1. + .62 * (inner[1] - 1.) + lift, 0., False
    if char in SYMBOLS:
        height = max(1., max(y for stroke in SYMBOLS[char] for _, y in stroke))
        return 0., height, 0., False
    if ASCII_FALLBACK.get(char, char) in PUNCT_PATHS:
        return 0., 1.05, 0., False
    return None


def plain_characters(text):
    """Distinct characters laid out as plain glyphs; formulae keep their own height."""
    try:
        parts = [source for kind, source in items(text) if kind == 'text']
    except ValueError:
        # Unclosed delimiters are reported by generation; predict on the raw text.
        parts = [text]
    return {char for part in parts for char in part if not char.isspace()}


def varied_ink(shape, human):
    """Ink above and below the baseline after scale and rotation are applied.

    Variation rotates and scales each glyph about its ink centre, so a wide glyph
    reaches further up and down. The result is clamped to the nominal box like
    Glyphs.box does. The vertical jitter only moves a glyph, so it is added once
    per line edge by `line_pitch_budget` instead of growing each glyph here.
    """
    top, bottom, width, varied = shape
    scale = 1. + human_amplitude(human, 'scale') / 100
    angle = math.radians(human_amplitude(human, 'rotation'))
    if varied and (scale != 1. or angle):
        # Rotation happens about the ink centre, so both edges can reach further out.
        reach = scale * ((bottom - top) / 2 * abs(math.cos(angle)) + width / 2 * abs(math.sin(angle)))
        centre = (top + bottom) / 2
        top, bottom = min(top, centre - reach), max(bottom, centre + reach)
    # Glyphs.box clamps the ink into the nominal box before measuring, so a glyph
    # that sits inside it still claims the full height above the baseline.
    return 1. - min(0., top), max(0., bottom - 1.)


def ink_extremes(fonts, choices, text, latin=None, human=None):
    """Worst ink above and below the box for the Chinese and Latin scripts.

    Every selected font that may render a character is considered, because the
    mixed-font choice is random per character and must never overflow.
    """
    human = human or {}
    extremes = {'zh': (1., 0., False), 'en': (1., 0., False)}
    for char in plain_characters(text):
        script = script_of(char)
        best = None
        for index, choice in enumerate(choices):
            if choice['weight'] > 0 and scoped(choice['scope'], script):
                value = cached_ink_shape(fonts[index], char)
                if value:
                    best = value if best is None else (min(best[0], value[0]), max(best[1], value[1]),
                                                       max(best[2], value[2]), True)
        if best is None and latin is not None and char.isascii():
            best = cached_ink_shape(latin, char)
        if best is None:
            best = fallback_ink_shape(char)
        if best is None:
            continue  # Generation reports the missing character together with its scope.
        ascent, descent = varied_ink(best, human)
        old_ascent, old_descent, _ = extremes[script]
        extremes[script] = (max(old_ascent, ascent), max(old_descent, descent), True)
    return extremes


def line_pitch_budget(config, extremes):
    """Ink height and reserved drift for a given looseness, in millimetres."""
    ratio_latin = config['latin_size'] / config['size']
    looseness = config['line_looseness']
    if config['line_sizing'] == 'rows':
        pitch = config['available'] / config['rows']
        size = pitch / looseness
    elif config['line_sizing'] == 'size':
        size = config['size']
        pitch = max(size, size * ratio_latin) * looseness
    else:
        size, pitch = config['size'], config['pitch']
    latin_size = size * ratio_latin
    # A line's height is its tallest ascent plus its deepest descent; those can
    # come from different scripts, so combine per component like the renderer.
    ascent = max((height * extremes[script][0] for script, height in (('zh', size), ('en', latin_size))
                  if extremes[script][2]), default=0.)
    descent = max((height * extremes[script][1] for script, height in (('zh', size), ('en', latin_size))
                   if extremes[script][2]), default=0.)
    # Jitter can push one glyph up and another down, so each line edge grows by it.
    lift = human_amplitude(config.get('humanization', {}), 'vertical') if ascent or descent else 0.
    ascent, descent = ascent + lift, descent + lift
    drift = min(drift_limit(config), max(0., (pitch - max(size, latin_size)) / 2))
    return dict(pitch=pitch, size=size, latin_size=latin_size, ink=ascent + descent,
                drift=drift, budget=pitch - 2 * drift)


def minimum_line_pitch(fonts, choices, config, text, latin=None):
    """Smallest looseness whose line box holds every glyph's ink.

    This is the same budget `prepare_notebook_tokens` scales into, so at or above
    the reported ratio the requested Chinese and English heights survive intact.
    The search also respects the 2–20 mm height and 1–200 row limits, and reports
    `feasible=False` when no looseness in 1–4 can satisfy all of them.
    """
    extremes = ink_extremes(fonts, choices, text, latin, config.get('humanization'))
    ratio_latin = config['latin_size'] / config['size']
    sizing = config['line_sizing']

    def evaluate(looseness):
        return line_pitch_budget(dict(config, line_looseness=looseness), extremes)

    def structural(looseness):
        """Whether the derived heights and row count stay inside the documented limits."""
        values = evaluate(looseness)
        if sizing == 'rows':
            return (2 <= values['size'] <= 20 and 2 <= values['latin_size'] <= 20
                    and values['latin_size'] <= values['pitch'] + .001)
        if sizing == 'size':
            return 1 <= math.floor((config['available'] + .000001) / values['pitch']) <= 200
        return True

    def fits(looseness):
        values = evaluate(looseness)
        return values['ink'] <= values['budget'] + 1e-9 and structural(looseness)

    if sizing is None:
        return dict(ratio=1., min_ratio=1., pitch_mm=config['pitch'], ink_mm=None,
                    feasible=True, drift_mm=0.)

    # Feasibility is an interval, not a threshold: in fixed-height mode a loose
    # setting can drop the row count below one. Scan the documented 1–4 range at
    # 1% steps to find the first feasible looseness, then refine it.
    steps = [step / 100 for step in range(100, 401)]
    first = next((value for value in steps if fits(value)), None)
    if first is None:
        # Nothing satisfies every limit, so report the roomiest looseness that at
        # least keeps the heights and rows valid; the hint asks for a fix.
        room = [value for value in steps if structural(value)] or steps
        looseness = max(room, key=lambda value: evaluate(value)['budget'] - evaluate(value)['ink'])
        feasible = False
    elif first == 1.:
        looseness, feasible = 1., True
    else:
        low, high = first - .01, first
        for _ in range(30):
            middle = (low + high) / 2
            if fits(middle):
                high = middle
            else:
                low = middle
        looseness, feasible = high, True
    values = evaluate(looseness)
    return dict(ratio=looseness, min_ratio=math.ceil(looseness * 100 - 1e-6) / 100,
                pitch_mm=values['pitch'], ink_mm=values['ink'], size=values['size'],
                latin_size=values['latin_size'], drift_mm=values['drift'], feasible=feasible)


def predict_line_pitch(data, choices, loader=GFont):
    """Predict the smallest usable line pitch for the requested layout.

    Only notebook mode has a looseness setting; grid mode and the legacy
    no-sizing request report a floor of 1 and keep their own pitch.
    """
    config = configuration(data, strict=False)
    if config['line_placement']=='ink':
        return dict(mode=config['mode'],line_sizing=config['line_sizing'],line_placement='ink',ratio=1.,min_ratio=1.,
                    pitch_mm=config['pitch'],ink_mm=None,feasible=True,drift_mm=0.,allow_overlap=False)
    if config['mode'] != 'notebook' or config['line_sizing'] is None:
        return dict(mode=config['mode'], line_sizing=config['line_sizing'], ratio=1., min_ratio=1.,
                    pitch_mm=config['pitch'], ink_mm=None, feasible=True, drift_mm=0.,
                    allow_overlap=config['allow_overlap'])
    pages = data.get('pages', [dict(text=data.get('text', ''))])
    texts = []
    if isinstance(pages, list):
        for page in pages[:MAX_PAGES]:
            if not isinstance(page, dict) or not isinstance(page.get('text'), str):
                continue
            text = page['text'].replace('\r\n', '\n').replace('\r', '\n')
            for section in re.split(r'\f|\\(?:newpage|pagebreak)\b', text):
                texts.append(markdown_text(section) if data.get('input_format') == 'markdown' else section)
    text = '\n'.join(texts)[:30000]
    entries = normalized([c for c in choices if c['weight'] > 0])
    if not entries:
        raise ValueError('请选择至少一种可用字体')
    with contextlib.ExitStack() as stack:
        fonts = [stack.enter_context(loader(c['path'])) for c in entries]
        demo = pathlib.Path(__file__).resolve().parents[1] / 'gcodeFonts' / 'demo.gfont'
        latin = stack.enter_context(loader(demo)) if demo.is_file() else None
        result = minimum_line_pitch(fonts, entries, config, text, latin)
    result.update(mode=config['mode'], line_sizing=config['line_sizing'], allow_overlap=config['allow_overlap'])
    if config['allow_overlap']:
        # Overlap mode never scales glyphs, so the floor stops being a limit; the
        # prediction is still reported so the hint can name what is being ignored.
        result.update(min_ratio=1., feasible=True, overlap=True)
    return result


def prepare_notebook_tokens(tokens, config):
    """Fit a source-page section once, before measuring any automatic wraps.

    Manual newlines do not trigger another size decision. Every ordinary line
    uses the same scale, while formulas retain their own height and scale.
    Overlap mode skips the fit entirely: the user asked for the requested
    heights even where the ink crosses the line box.
    """
    if config.get('line_sizing') is None or config.get('allow_overlap') or config.get('line_placement')=='ink':
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


def compact_notebook_pages(rows, config, glyphs, protect_text=False):
    """Keep the requested base pitch and reserve actual ink space with sub-row precision."""
    pages=[];paths=[];lines=[];page_tokens=[];guides=[];layout=[]
    pitch=config['pitch'];available=config['available'];m=config['margins']
    gap=max(.25,min(.6,min(config['size'],config['latin_size'])*.08))
    next_y=0.;ink_bottom=None;formula_bottom=None;last_formula=False

    def flush():
        nonlocal paths,lines,page_tokens,guides,layout,next_y,ink_bottom,formula_bottom,last_formula
        pages.append((paths,lines,page_tokens,guides,layout))
        paths=[];lines=[];page_tokens=[];guides=[];layout=[]
        next_y=0.;ink_bottom=None;formula_bottom=None;last_formula=False

    for line in rows:
        formula=any(t.formula for t in line)
        baseline=max((t.box.baseline for t in line),default=max(config['size'],config['latin_size']))
        drift_amplitude=(glyphs.variation.amplitude('baseline') if formula and glyphs.variation else plain_line_drift(config))
        phase=glyphs.variation.line_phase() if glyphs.variation else 0.
        x=m['left'];relative=[]
        for token in line:
            drift=drift_amplitude*math.sin(phase+math.pi*(x-m['left'])/config['usable'])
            relative.extend(token.box.placed(x,baseline-token.box.baseline+drift+drift_amplitude))
            x+=token.advance
        points=[p for stroke in relative for p in stroke]
        top=min((y for _,y in points),default=0.)
        bottom=max((y for _,y in points),default=0.)
        if bottom-top>available+.001:
            raise ValueError('公式或字形高度超过单页可写范围，请减小字号或增加页面高度')
        y=next_y-top if protect_text and ink_bottom is None and points else next_y
        safe_gap=gap if formula or last_formula or not protect_text else max(.15,min(.35,min(config['size'],config['latin_size'])*.04))
        protected=ink_bottom if formula or protect_text else formula_bottom
        if protected is not None:y=max(y,protected+safe_gap-top)
        # Empty source rows still consume one pitch; their nominal baseline must fit.
        edge=bottom if points else min(pitch,max(config['size'],config['latin_size']))
        if lines and y+edge>available+.001:
            flush();y=-top if protect_text and points else 0.
        y=max(y,-top)
        if y+edge>available+.001:
            raise ValueError('公式或字形高度超过单页可写范围，请减小字号或增加页面高度')
        extra=max(0.,y-next_y)
        placed=[moved(s,((px,py+y+m['top']) for px,py in s)) for s in relative]
        paths.extend(placed);lines.append(''.join(t.source for t in line));page_tokens.extend(line)
        guides.append(round(m['top']+y+(bottom if points else baseline),3))
        layout.append(dict(formula=formula,offset_mm=round(y,4),top_mm=round(m['top']+y+top,4),
                           bottom_mm=round(m['top']+y+bottom,4),extra_gap_mm=round(extra,4),safety_gap_mm=safe_gap))
        if points:
            ink_bottom=max(ink_bottom if ink_bottom is not None else -math.inf,y+bottom)
            if formula:formula_bottom=max(formula_bottom if formula_bottom is not None else -math.inf,y+bottom)
        next_y=y+pitch
        last_formula=formula
    if lines or not pages:flush()
    return pages


def notebook_pages(tokens, config, glyphs):
    rows = notebook_rows(tokens, config)
    if config.get('line_placement')=='ink':return compact_notebook_pages(rows,config,glyphs,protect_text=True)
    if config['allow_overlap']:return compact_notebook_pages(rows,config,glyphs)
    pages, paths, page_tokens, lines = [], [], [], []
    def default_guides():
        return {i: round(config['margins']['top'] + i * config['pitch'] + max(config['size'], config['latin_size']), 3) for i in range(config['rows'])}
    guides = default_guides()
    row_cursor = 0
    for line in rows:
        baseline = max((t.box.baseline for t in line), default=max(config['size'], config['latin_size']))
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
        guides[row_cursor] = round(max((py for stroke in row_paths for _, py in stroke), default=y + max(config['size'], config['latin_size'])), 3)
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
            # Fit to the original cell occupancy before balancing. Otherwise the
            # usual fill-to-height step would undo a smaller Latin glyph.
            factor = token.balance_scale
            scale = min(scale * factor, ah / max(token.box.height, .001),
                        aw * min(1., factor) / max(token.box.width, .001))
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


def distributed_pages(tokens, config, glyphs):
    """Reflow real glyphs after spatial selection, checking actual output pages.

    Width/height changes can move a glyph to a new page. Retain the best valid
    measured layout; if page membership oscillates before diversity is satisfied,
    keep those page boundaries and allow further splits, never a silent violation.
    """
    def layout(chunk):
        return notebook_pages(chunk, config, glyphs) if config['mode'] == 'notebook' else character_grid_pages(chunk, config)
    line_state = glyphs.variation.line_rng.getstate() if glyphs.variation else None
    best, best_score, best_state = None, None, None
    barriers = set()
    current = tokens
    weights = {choice['font_id']: choice['weight'] for choice in glyphs.choices}
    for attempt in range(MAX_PAGES + 8):
        if glyphs.variation:
            glyphs.variation.line_rng.setstate(line_state)
        chunks, chunk = [], []
        for token in current:
            if token != '\n' and token.serial in barriers and chunk:
                chunks.append(chunk); chunk = []
            chunk.append(token)
        chunks.append(chunk)
        try:
            pages = [page for chunk in chunks for page in layout(chunk)]
        except ValueError:
            if best is None:
                raise
            if glyphs.variation:
                glyphs.variation.line_rng.setstate(best_state)
            return best
        if len(pages) > MAX_PAGES:
            raise ValueError('排版结果超过 100 页，请拆分文档')
        score = repeat_fonts.quality(pages)
        if score[0] == 0 and (best_score is None or score > best_score):
            best, best_score = pages, score
            best_state = glyphs.variation.line_rng.getstate() if glyphs.variation else None
        plan = repeat_fonts.plan_pages(pages, weights, glyphs.seed)
        placed = {item['occurrence']: item['font_id'] for page in pages for item in repeat_fonts.occurrences(page[0])}
        if all(placed.get(key) == font for key, font in plan.items()) and score[0] == 0:
            if best_score is not None and best_score > score:
                if glyphs.variation:
                    glyphs.variation.line_rng.setstate(best_state)
                return best
            return pages
        if attempt >= 4 and best is not None:
            if glyphs.variation:
                glyphs.variation.line_rng.setstate(best_state)
            return best
        if attempt >= 6:
            barriers.update(page[2][0].serial for page in pages[1:] if page[2])
        glyphs.font_plan = plan
        current = [token if token == '\n' else glyphs.token(token.source[1:-1] if token.formula else token.source,
                   formula=token.formula, size=token.nominal_size, serial=token.serial, display=token.display) for token in tokens]
    raise ValueError('字体变化导致分页无法稳定，请减小字号或增大可写区域后重试')


def emit(config, data, paper_paths, lines, tokens, glyphs, seed, source_page, row_guides=None, line_layout=None):
    coordinates = Coordinates.from_data(data, config['width'], config['height'])
    if config['mode'] == 'grid' and config['grid']['draw_lines']:
        paper_paths = grid_paths(config) + paper_paths
    paper_paths = [moved(stroke,((round(x,3),round(y,3)) for x,y in stroke)) for stroke in paper_paths]
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
    control = config['device_control']
    up, down = control['pen_up'], control['pen_down']
    commands = ['G21', 'G90', 'G94', f'G1 Z{up:g} F3000', 'G92 X0 Y0']
    last, travel, draw = (0., 0.), 0., 0.
    for stroke in paths:
        x, y = stroke[0]; travel += math.dist(last, stroke[0])
        commands.extend([f'G1 X{x:.3f} Y{y:.3f} F{config["move_feed"]:g}', f'G1 Z{down:g} F{config["move_feed"]:g}'])
        commands.extend(f'G1 X{x:.3f} Y{y:.3f} F{config["feed"]:g}' for x,y in stroke[1:])
        commands.append(f'G1 Z{up:g} F{config["move_feed"]:g}'); last = stroke[-1]
        draw += sum(math.dist(a,b) for a,b in zip(stroke, stroke[1:]))
    commands.extend([f'G1 Z{up:g} F3000', 'G1 X0 Y0 F3000'])
    gcode = '\n'.join(commands) + '\n'
    metadata = coordinates.metadata()
    metadata['device_control'] = control
    if config.get('device_port'):
        metadata['device_port'] = config['device_port']
    metadata.update(layout_mode=config['mode'], margins=config['margins'], writable=[config['usable'], config['available']],
                    line_sizing=config['line_sizing'], line_looseness=config['line_looseness'],
                    allow_overlap=config['allow_overlap'],line_placement=config['line_placement'], row_guides=row_guides,line_layout=line_layout,
                    rows=config['rows'], size=config['size'], latin_size=config['latin_size'], pitch=config['pitch'], spacing=config['spacing'], feed=config['feed'], move_feed=config['move_feed'],
                    grid=config['grid'], punctuation_width=config['punctuation_width'], humanization=config['humanization'],
                    seed=seed, source_page=source_page, lines=lines, text='\n'.join(lines), bounds=bounds, machine_bounds=machine_bounds,
                    strokes=len(paths), points=len(points), commands=len(commands), xy_mm=round(travel + draw + math.dist(last,(0,0)), 1),
                    font_mix=glyphs.summary(tokens), fallback_characters=sum(t.fallback for t in tokens),
                    font_balance=glyphs.balance.metadata({key for token in tokens for key in token.fonts}),
                    glyph_occurrences=repeat_fonts.occurrences(paper_paths),
                    repeat_distribution=repeat_fonts.metadata(paper_paths),
                    glyph_fallbacks=fallback_metadata(paper_paths),external_fallback_characters=len(fallback_metadata(paper_paths)),
                    empty=not bool(points), formula_count=sum(t.formula for t in tokens),
                    sha256=hashlib.sha256(gcode.encode('ascii')).hexdigest())
    return dict(metadata=metadata, paths=paths, gcode=gcode, svg=preview_svg(config, coordinates, paper_paths, row_guides))


def generate_document(font_path, data, choices=None, fallback_library=None):
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
        glyphs = Glyphs(choices, stack, config, seed, fallback_library)
        for index, text in enumerate(pages):
            sections = re.split(r'\f|\\(?:newpage|pagebreak)\b', text)
            for section in sections:
                if data.get('input_format') == 'markdown': section = markdown_text(section)
                tokens = tokenize(section.expandtabs(4), glyphs, config)
                generated = distributed_pages(tokens, config, glyphs)
                if config['pagination'] == 'fixed' and len(generated) > 1:
                    raise ValueError(f'预设第 {index + 1} 页内容溢出，需要 {len(generated)} 页；请减少内容或选择自动续页')
                for entry in generated:
                    paths, lines, page_tokens = entry[:3]
                    if len(results) >= MAX_PAGES:
                        raise ValueError('排版结果超过 100 页，请拆分文档')
                    result = emit(config, data, paths, lines, page_tokens, glyphs, seed, index + 1, entry[3] if len(entry) > 3 else None,entry[4] if len(entry)>4 else None)
                    point_count += result['metadata']['points']
                    if point_count > 2000000:
                        raise ValueError('文档路径超过 200 万点，请拆分文档')
                    results.append(result)
    for index, result in enumerate(results):
        result['metadata'].update(page_number=index + 1, page_count=len(results))
    return results
