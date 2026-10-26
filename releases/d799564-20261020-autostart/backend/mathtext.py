"""Small, bounded LaTeX-to-single-stroke layout engine (no TeX execution)."""
import dataclasses
import re


@dataclasses.dataclass
class Box:
    width: float
    height: float
    baseline: float
    paths: list
    spacing: float | None = None

    def placed(self, x=0., y=0., scale=1.):
        return [[(x + px * scale, y + py * scale) for px, py in stroke] for stroke in self.paths]


def join(boxes, gap=0.):
    if not boxes:
        return Box(0., 0., 0., [])
    baseline = max(box.baseline for box in boxes)
    bottom = max(box.height - box.baseline for box in boxes)
    paths, x = [], 0.
    for box in boxes:
        paths.extend(box.placed(x, baseline - box.baseline))
        x += box.width + gap
    return Box(max(0., x - gap), baseline + bottom, baseline, paths)


# Original geometric symbols in an em square; all paths are open pen strokes.
SYMBOLS = {
    '+': [[(.1,.5),(.9,.5)],[(.5,.1),(.5,.9)]],
    '−': [[(.1,.5),(.9,.5)]], '-': [[(.1,.5),(.9,.5)]],
    '=': [[(.1,.35),(.9,.35)],[(.1,.65),(.9,.65)]],
    '×': [[(.15,.15),(.85,.85)],[(.15,.85),(.85,.15)]],
    '÷': [[(.1,.5),(.9,.5)],[(.49,.18),(.51,.18)],[(.49,.82),(.51,.82)]],
    '±': [[(.1,.38),(.9,.38)],[(.5,.05),(.5,.7)],[(.1,.9),(.9,.9)]],
    '·': [[(.49,.5),(.51,.5)]],
    '<': [[(.85,.1),(.15,.5),(.85,.9)]], '>': [[(.15,.1),(.85,.5),(.15,.9)]],
    '≤': [[(.85,.05),(.15,.35),(.85,.65)],[(.1,.9),(.9,.9)]],
    '≥': [[(.15,.05),(.85,.35),(.15,.65)],[(.1,.9),(.9,.9)]],
    '≠': [[(.1,.35),(.9,.35)],[(.1,.65),(.9,.65)],[(.75,.05),(.25,.95)]],
    'π': [[(.05,.25),(.95,.25)],[(.3,.25),(.28,.7),(.18,.9)],[(.7,.25),(.65,.8),(.75,.9),(.9,.8)]],
    'α': [[(.9,.3),(.75,.7),(.55,.9),(.25,.85),(.1,.6),(.25,.3),(.55,.25),(.75,.5),(.85,.9)]],
    'β': [[(.2,1.1),(.2,.2),(.35,.05),(.65,.05),(.85,.2),(.7,.4),(.3,.5),(.65,.5),(.85,.7),(.7,.9),(.2,.9)]],
    'γ': [[(.1,.25),(.3,.3),(.6,.9),(.65,1.1)],[(.9,.25),(.6,.9)]],
    'δ': [[(.75,.1),(.3,.05),(.2,.2),(.7,.5),(.85,.7),(.7,.95),(.3,.95),(.1,.7),(.25,.45),(.6,.4)]],
    'θ': [[(.5,.05),(.2,.2),(.1,.5),(.2,.85),(.5,.95),(.8,.8),(.9,.5),(.8,.15),(.5,.05)],[(.15,.5),(.85,.5)]],
    'λ': [[(.2,.05),(.35,.05),(.8,.95)],[(.5,.4),(.1,.95)]],
    'μ': [[(.2,.25),(.1,1.1)],[(.2,.4),(.25,.85),(.45,.9),(.7,.65),(.8,.25),(.7,.85),(.9,.9)]],
    'σ': [[(.95,.25),(.4,.25),(.15,.4),(.1,.65),(.3,.9),(.6,.9),(.8,.65),(.65,.4),(.4,.25)]],
    'φ': [[(.5,.05),(.5,1.1)],[(.5,.25),(.2,.3),(.1,.6),(.3,.9),(.7,.9),(.9,.6),(.8,.3),(.5,.25)]],
    'ω': [[(.2,.3),(.1,.6),(.2,.9),(.4,.9),(.5,.6),(.6,.9),(.8,.9),(.9,.6),(.8,.3)]],
    'Δ': [[(.5,.05),(.05,.95),(.95,.95),(.5,.05)]],
    'Σ': [[(.9,.05),(.1,.05),(.55,.5),(.1,.95),(.9,.95)]],
    '∑': [[(.9,.05),(.1,.05),(.55,.5),(.1,.95),(.9,.95)]],
    '∫': [[(.85,.05),(.65,0),(.5,.15),(.4,.85),(.25,1),(.1,.95)]],
    '∞': [[(.5,.5),(.25,.25),(.05,.35),(.05,.65),(.25,.75),(.5,.5),(.75,.25),(.95,.35),(.95,.65),(.75,.75),(.5,.5)]],
    '→': [[(.05,.5),(.95,.5)],[(.65,.2),(.95,.5),(.65,.8)]],
    '°': [[(.5,.05),(.3,.1),(.25,.3),(.45,.4),(.65,.3),(.7,.1),(.5,.05)]],
    '∂': [[(.2,.1),(.55,.05),(.8,.3),(.85,.65),(.65,.9),(.3,.9),(.15,.7),(.3,.45),(.65,.45),(.85,.65)]],
}
COMMANDS = dict(alpha='α', beta='β', gamma='γ', delta='δ', theta='θ', lambda_='λ', mu='μ',
                sigma='σ', phi='φ', omega='ω', pi='π', Delta='Δ', Sigma='Σ', sum='∑',
                int='∫', infty='∞', times='×', div='÷', pm='±', cdot='·', le='≤', leq='≤',
                ge='≥', geq='≥', ne='≠', neq='≠', to='→', rightarrow='→', partial='∂', circ='°')
COMMANDS['lambda'] = COMMANDS.pop('lambda_')


def symbol(char, size):
    paths = [[(x * size, y * size) for x, y in stroke] for stroke in SYMBOLS[char]]
    height = max(1., max(y for stroke in SYMBOLS[char] for x, y in stroke)) * size
    return Box(size, height, size, paths)


class Parser:
    def __init__(self, source, glyph):
        if not source.strip() or len(source) > 1000:
            raise ValueError('公式需为 1–1000 个字符')
        self.source, self.glyph, self.pos = source, glyph, 0

    def spaces(self):
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def group(self, size, depth):
        self.spaces()
        if self.pos >= len(self.source):
            raise ValueError('公式缺少参数或上下标内容')
        if self.source[self.pos] == '{':
            self.pos += 1
            return self.sequence(size, depth + 1, closing=True)
        return self.atom(size, depth + 1)

    def sequence(self, size, depth=0, closing=False):
        if depth > 12:
            raise ValueError('公式嵌套超过 12 层')
        boxes = []
        while True:
            self.spaces()
            if self.pos >= len(self.source):
                if closing:
                    raise ValueError('公式缺少右花括号 }')
                break
            if self.source[self.pos] == '}':
                if not closing:
                    raise ValueError('公式存在多余的右花括号 }')
                self.pos += 1
                break
            base = self.group(size, depth) if self.source[self.pos] == '{' else self.atom(size, depth)
            scripts = {}
            self.spaces()
            while self.pos < len(self.source) and self.source[self.pos] in '^_':
                kind = self.source[self.pos]
                if kind in scripts:
                    raise ValueError('公式的上下标重复，请用花括号分组')
                self.pos += 1
                scripts[kind] = self.group(size * .62, depth + 1)
                self.spaces()
            if scripts:
                top = -scripts['^'].height * .65 if '^' in scripts else 0.
                paths = base.placed(0, -top)
                bottom = base.height
                for kind, box in scripts.items():
                    y = top if kind == '^' else base.baseline - box.baseline * .15
                    paths.extend(box.placed(base.width + size * .06, y - top))
                    bottom = max(bottom, y + box.height)
                base = Box(base.width + size * .06 + max(b.width for b in scripts.values()),
                           bottom - top, base.baseline - top, paths)
            boxes.append(base)
        return join(boxes, size * .1)

    def atom(self, size, depth):
        if depth > 12:
            raise ValueError('公式嵌套超过 12 层')
        char = self.source[self.pos]
        self.pos += 1
        if char in '^_}':
            raise ValueError('公式上下标前缺少主体或括号不匹配')
        if char == '\\':
            match = re.match(r'[A-Za-z]+|.', self.source[self.pos:])
            if not match:
                raise ValueError('公式末尾的反斜杠无效')
            command = match[0]
            self.pos += len(command)
            if command in ('frac', 'dfrac', 'tfrac'):
                numerator = self.group(size * .72, depth + 1)
                denominator = self.group(size * .72, depth + 1)
                width = max(numerator.width, denominator.width) + size * .3
                line_y = numerator.height + size * .15
                paths = numerator.placed((width - numerator.width) / 2)
                paths.append([(0., line_y), (width, line_y)])
                paths.extend(denominator.placed((width - denominator.width) / 2, line_y + size * .15))
                return Box(width, line_y + size * .15 + denominator.height, line_y + size * .35, paths)
            if command == 'sqrt':
                self.spaces()
                if self.pos < len(self.source) and self.source[self.pos] == '[':
                    raise ValueError('根号暂不支持可选次数，请使用普通平方根或分数指数')
                body = self.group(size, depth + 1)
                pad = size * .15
                paths = body.placed(size * .65, pad)
                paths.append([(0., body.height * .55), (size * .18, body.height * .45),
                              (size * .35, body.height + pad), (size * .6, 0.),
                              (body.width + size * .7, 0.)])
                return Box(body.width + size * .75, body.height + pad, body.baseline + pad, paths)
            if command == 'text':
                self.spaces()
                if self.pos >= len(self.source) or self.source[self.pos] != '{':
                    raise ValueError('\\text 的文字请放在花括号中')
                self.pos += 1
                letters, level = [], 1
                while self.pos < len(self.source):
                    letter = self.source[self.pos]; self.pos += 1
                    if letter == '\\':
                        if self.pos >= len(self.source) or self.source[self.pos] not in '{}%$#_& ':
                            raise ValueError('\\text 中暂只支持纯文本及转义标点')
                        letter = self.source[self.pos]; self.pos += 1
                    elif letter == '{':
                        level += 1
                        if level > 12: raise ValueError('公式嵌套超过 12 层')
                        continue
                    elif letter == '}':
                        level -= 1
                        if not level: break
                        continue
                    letters.append(Box(size * .35, size, size, []) if letter.isspace() else self.glyph(letter, size))
                if level: raise ValueError('\\text 缺少右花括号 }')
                return join(letters, size * .05)
            if command in ('overline', 'bar'):
                body = self.group(size, depth + 1)
                paths = body.placed(0., size * .15)
                paths.append([(0., 0.), (body.width, 0.)])
                return Box(body.width, body.height + size * .15, body.baseline + size * .15, paths)
            if command in ('mathrm', 'mathbf', 'operatorname'):
                return self.group(size, depth + 1)
            if command in ('sin', 'cos', 'tan', 'log', 'ln', 'exp', 'lim'):
                return join([self.glyph(c, size) for c in command], size * .05)
            if command in (',', ';', ' ', 'quad', 'qquad', '!'):
                return Box(size * ({'!':0., ',':.16, ';':.3, 'qquad':2.}.get(command, 1.)), size, size, [])
            if command in ('left', 'right'):
                self.spaces()
                if self.pos >= len(self.source) or self.source[self.pos] not in '()[]|':
                    raise ValueError('\\left / \\right 后暂只支持圆括号、方括号和竖线')
                return self.atom(size, depth + 1)
            if command in COMMANDS:
                char = COMMANDS[command]
            elif command in '{}%$#_':
                char = command
            else:
                raise ValueError('暂不支持 LaTeX 命令：\\' + command)
        return symbol(char, size) if char in SYMBOLS else self.glyph(char, size)


def render(source, size, glyph):
    return Parser(source, glyph).sequence(size)


def items(text):
    """Yield ('text'|'formula'|'display', source), rejecting unclosed delimiters."""
    pending, pos = [], 0
    while pos < len(text):
        if text.startswith('\\$', pos):
            pending.append('$'); pos += 2; continue
        opener = next((value for value in ('$$', '$', '\\(', '\\[') if text.startswith(value, pos)), None)
        if not opener:
            pending.append(text[pos]); pos += 1; continue
        if pending:
            yield 'text', ''.join(pending); pending = []
        closer = {'\\(':'\\)', '\\[':'\\]'}.get(opener, opener)
        start = pos + len(opener)
        end = text.find(closer, start)
        if end < 0:
            raise ValueError('公式分隔符未闭合：' + opener)
        yield ('display' if opener in ('$$', '\\[') else 'formula'), text[start:end]
        pos = end + len(closer)
    if pending:
        yield 'text', ''.join(pending)
