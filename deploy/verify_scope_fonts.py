"""Assert per-script font scopes: routing, probabilities, errors, unchanged defaults.

Runs inside the deployed release with real fonts; no controller or serial port is touched.
    py -3.10 deploy/verify_scope_fonts.py
Exits non-zero with the first failing assertion.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASE = pathlib.Path(json.loads((ROOT / 'releases' / '89df8a2-20261020-live-speed' / 'deploy' / 'services.json')
                                  .read_text(encoding='utf-8-sig'))['Release'])
sys.path.insert(0, str(RELEASE))

from backend.document import generate_document  # noqa: E402
from backend.mixing import font_choices  # noqa: E402

FONT_DIR = pathlib.Path(r'C:\tools\奎享雕刻\奎享雕刻\gcodeFonts')
ZH = FONT_DIR / '1111122_kvenjoy_1223.gfont'
EN = FONT_DIR / 'blood8-2_kvenjoy_blood8.gfont'
BASE = dict(layout_mode='notebook', line_sizing='rows', line_looseness=2.37, pagination='auto',
            width=210, height=297, margin_top=30, margin_bottom=30, margin_left=30, margin_right=30,
            rows=20, size=5, latin_size=5.5, punctuation_width=.8, spacing=.9, feed=1800, move_feed=3000,
            seed='baseline-check')
TEXT = '中文排版测试。\nEnglish ABC 123, ok? $x^2+1$'


def document(mix, text=TEXT, **extra):
    data = dict(BASE, pages=[dict(text=text)], font_mix=mix, **extra)
    return generate_document(ZH, data, mix)


def test_database_defaults_unchanged():
    # Payloads without `scope` keep 中英通用 semantics and byte-identical output.
    mix = [dict(font_id='zh-font', path=ZH, weight=70), dict(font_id='en-font', path=EN, weight=30)]
    assert document(mix)[0]['metadata']['sha256'] == \
        'defa466f56c9939ccc60d55e057a3dfce3f1cec00a79ba291379ab18cfd913b2', 'legacy notebook output changed'
    grid = dict(layout_mode='grid', grid=dict(columns=20, row_gap=2.9, column_gap=0, size_percent=80,
                                              padding=.3, draw_lines=True))
    assert document(mix, '中文ABC', **grid)[0]['metadata']['sha256'] == \
        '2f0fac83371dd614551a17f06dc12c3b967fb4da925409e083608e4c2946e69e', 'legacy grid output changed'
    assert {c['scope'] for c in document(mix)[0]['metadata']['font_mix']} == {'all'}


def test_scopes_route_each_script():
    split = [dict(font_id='zh-font', path=ZH, weight=1., scope='zh'),
             dict(font_id='en-font', path=EN, weight=1., scope='en')]
    metadata = document(split)[0]['metadata']
    assert [c['scope'] for c in metadata['font_mix']] == ['zh', 'en']
    assert [c['used'] for c in metadata['font_mix']] == [7, 20], metadata['font_mix']
    assert metadata['fallback_characters'] == 0
    assert all(c['probabilities'] == {c['scope']: 1.} for c in metadata['font_mix'])


def test_probabilities_are_per_script():
    zh2 = FONT_DIR / '0_kvenjoy_0.gfont'
    if not zh2.is_file():
        return
    mix = [dict(font_id='a', path=ZH, weight=70, scope='zh'), dict(font_id='b', path=zh2, weight=30, scope='zh'),
           dict(font_id='c', path=EN, weight=5, scope='en')]
    summary = document(mix, '中' * 100 + '\n' + 'A' * 100)[0]['metadata']['font_mix']
    assert [round(c['probabilities']['zh'], 3) for c in summary[:2]] == [.7, .3], summary
    assert summary[2]['probabilities'] == {'en': 1.}, summary[2]


def test_missing_scope_is_reported():
    for scope, char in (('en', '中'), ('zh', 'A')):
        mix = [dict(font_id='only', path=EN if scope == 'en' else ZH, weight=1., scope=scope)]
        try:
            document(mix, char, layout_mode='notebook')
        except ValueError as error:
            label = '中文' if char == '中' else '英文'
            assert f'没有可用的{label}字体' in str(error), str(error)
        else:
            raise AssertionError(f'a {scope}-only mix accepted a {char} character')


def test_invalid_scope_rejected():
    available = {'zh': ZH}
    for scope in ('klingon', '', None, 1):
        try:
            font_choices(dict(font_mix=[dict(font_id='zh', weight=1, scope=scope)]), available)
        except ValueError as error:
            assert '适用范围' in str(error), str(error)
        else:
            raise AssertionError(f'scope {scope!r} was accepted')


def main():
    for test in (test_database_defaults_unchanged, test_scopes_route_each_script,
                 test_probabilities_are_per_script, test_missing_scope_is_reported,
                 test_invalid_scope_rejected):
        test()
    print('SCOPE_FONTS_OK')


if __name__ == '__main__':
    main()
