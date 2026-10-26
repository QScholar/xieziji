"""Assert that Markdown documents with LaTeX formulae and HTML scripts still lay out.

Run from the deployed release root:
    py -3.10 deploy/verify_markdown_formula.py
Exits non-zero with the first failing assertion.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'releases' / '89df8a2-20261020-live-speed'))

from backend.document import generate_document  # noqa: E402

FONT_DIR = pathlib.Path(r'C:\tools\奎享雕刻\奎享雕刻\gcodeFonts')
FONT = next(FONT_DIR.glob('blood8-2_kvenjoy_blood8.gfont'))
DOCS = ROOT / 'tempfile'

BASE = dict(layout_mode='notebook', line_sizing='rows', line_looseness=2, pagination='auto',
            width=210, height=297, margin_top=30, margin_bottom=30, margin_left=30, margin_right=30,
            rows=20, size=5, latin_size=5, punctuation_width=.8, spacing=.9, feed=1800, move_feed=3000,
            seed='markdown-formula-check')


def generate(text, **extra):
    data = dict(BASE, pages=[dict(text=text)], **extra)
    return generate_document(FONT, data, [dict(font_id='blood8-2', path=FONT, weight=1.)])


def check_documents():
    expected = {'油井压力不稳定试井翻译_简化版.md': 11, '油井压力不稳定试井翻译.md': 14}
    for name, pages in expected.items():
        text = (DOCS / name).read_text(encoding='utf-8')
        result = generate(text, input_format='markdown')
        assert len(result) == pages, f'{name}: {len(result)} 页，预期 {pages}'
        assert sum(page['metadata']['formula_count'] for page in result) > 0, f'{name}: 没有公式'
        assert sum(page['metadata']['points'] for page in result) > 100000, f'{name}: 路径过少'
        for page in result:
            bounds, metadata = page['metadata']['bounds'], page['metadata']
            assert bounds[0] >= 30 - .001 and bounds[1] >= 30 - .001, f'{name}: 超出左边距/上边距'
            assert bounds[2] <= 180 + .001 and bounds[3] <= 267 + .001, f'{name}: 超出右边距/下边距'
            assert page['gcode'].startswith('G21\nG90\nG94\n'), f'{name}: G-code 头部异常'
        print(f'ok  {name}: {len(result)} 页，'
              f'{sum(page["metadata"]["formula_count"] for page in result)} 个公式')


def check_constructs():
    cases = {
        '宏与界定符': '$a \\equiv b$, $\\varphi\\left\\lbrack x \\right\\rbrack$',
        '不可见界定符': '$- \\left. f \\right.$',
        '公式内 HTML 上下标': '$c<sub>T</sub> + r<sub>w</sub>^{2}$',
        'Unicode 上下标': '8 × 10⁻⁶ psia⁻¹ 与 r²、a₀',
        'HTML 实体': 'a &lt; b &gt; c &amp; d',
        '公式内转义尖括号': '$a \\left< b \\right>$',
        '行内公式': '设 $x^2 + \\frac{1}{2} = \\sqrt{4}$ 成立',
        '分式与积分': '$\\int_{x}^{\\infty}{\\frac{e^{-u}}{u}du}$',
    }
    for label, text in cases.items():
        result = generate(text, input_format='markdown')
        assert len(result) == 1 and result[0]['metadata']['points'] > 0, f'{label}: 未生成路径'
        print(f'ok  {label}')


def check_front_matter():
    text = '---\ntitle: "标题"\nlang: "zh-CN"\n---\n\n# 正文'
    result = generate(text, input_format='markdown')
    lines = result[0]['metadata']['lines']
    joined = ''.join(lines)
    assert 'title' not in joined and '---' not in joined, f'front matter 未剥离：{lines}'
    assert '正文' in joined, 'front matter 剥离吃掉正文'
    print('ok  front matter 剥离')


def check_oversize_formula():
    # Wider than the 150 mm writable width; must shrink instead of failing the document.
    wide = '$' + ' + '.join(f'y_{{{i}}} = \\frac{{a_{{{i}}}b_{{{i}}}}}{{c_{{{i}}}d_{{{i}}}}}' for i in range(4)) + '$'
    result = generate(wide, input_format='markdown')
    metadata = result[0]['metadata']
    assert metadata['bounds'][2] <= 180 + .001, f'超宽公式未被收进边距：{metadata["bounds"]}'
    print('ok  超宽公式等比缩小')


def check_modes():
    text = 'AB 中文 CD\ntext'
    for label, extra in (('纯文本', dict(input_format='plain')),
                         ('笔记固定字高', dict(input_format='plain', line_sizing='size')),
                         ('表格', dict(input_format='plain', layout_mode='grid',
                                     grid=dict(columns=10, row_gap=2.9, column_gap=0,
                                               size_percent=80, padding=.3, draw_lines=False)))):
        result = generate(text, **extra)
        assert result and result[0]['metadata']['points'] > 0, f'{label}: 未生成路径'
        print(f'ok  {label}')


if __name__ == '__main__':
    check_documents()
    check_constructs()
    check_front_matter()
    check_oversize_formula()
    check_modes()
    print('MARKDOWN_FORMULA_VERIFY_OK')
