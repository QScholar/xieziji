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
    glue: bool = False
    limits: bool = False

    def placed(self, x=0., y=0., scale=1.):
        from backend.glyph_fallback import moved
        return [moved(stroke,((x+px*scale,y+py*scale) for px,py in stroke)) for stroke in self.paths]


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
    '≡': [[(.1,.2),(.9,.2)],[(.1,.5),(.9,.5)],[(.1,.8),(.9,.8)]],
}
COMMANDS = dict(alpha='α', beta='β', gamma='γ', delta='δ', theta='θ', lambda_='λ', mu='μ',
                sigma='σ', phi='φ', omega='ω', pi='π', Delta='Δ', Sigma='Σ', sum='∑',
                int='∫', infty='∞', times='×', div='÷', pm='±', cdot='·', le='≤', leq='≤',
                ge='≥', geq='≥', ne='≠', neq='≠', to='→', rightarrow='→', partial='∂', circ='°',
                equiv='≡', varphi='φ', lbrack='[', rbrack=']')
COMMANDS['lambda'] = COMMANDS.pop('lambda_')
from backend.math_symbols import EXTRA_COMMANDS, EXTRA_SYMBOLS
SYMBOLS.update(EXTRA_SYMBOLS)
COMMANDS.update(EXTRA_COMMANDS)


def symbol(char, size):
    paths = [[(x * size, y * size) for x, y in stroke] for stroke in SYMBOLS[char]]
    height = max(1., max(y for stroke in SYMBOLS[char] for x, y in stroke)) * size
    return Box(size, height, size, paths)


def render(source, size, glyph, display=False):
    from backend.latex import Parser
    return Parser(source, glyph, display=display).sequence(size)


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
        end = start
        while end < len(text):
            if text[end] == '%':
                newline = text.find('\n', end)
                end = len(text) if newline < 0 else newline + 1
                continue
            if text.startswith(closer, end): break
            # Skip an escaped character (including \$), not a closing delimiter.
            end += 2 if text[end] == '\\' else 1
        if end >= len(text): end = -1
        if end < 0:
            raise ValueError('公式分隔符未闭合：' + opener)
        yield ('display' if opener in ('$$', '\\[') else 'formula'), text[start:end]
        pos = end + len(closer)
    if pending:
        yield 'text', ''.join(pending)
