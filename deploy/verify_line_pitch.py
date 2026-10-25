"""Assert that the predicted minimum line pitch is really the smallest usable one.

Run from the repository root:
    py -3.10 deploy/verify_line_pitch.py
Exits non-zero with the first failing assertion.
"""
import contextlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'releases' / '89df8a2-20261020-live-speed'
sys.path.insert(0, str(RELEASE))

import backend.document as document  # noqa: E402

FONT_DIR = pathlib.Path(r'C:\tools\奎享雕刻\奎享雕刻\gcodeFonts')
FONT = next(FONT_DIR.glob('blood8-2_kvenjoy_blood8.gfont'))
CHOICES = [dict(font_id='blood8-2', path=FONT, weight=1.)]

BASE = dict(layout_mode='notebook', pagination='auto', width=210, height=297,
            margin_top=30, margin_bottom=30, margin_left=30, margin_right=30,
            size=5, latin_size=5, punctuation_width=.8, spacing=.9, feed=1800, move_feed=3000)

TEXTS = {
    '纯中文': '油井压力不稳定试井解释',
    '中英混排': '油井Agjy压力不稳The quick brown fox 0123456789。',
    '上下伸出的西文': '国Agjyqp（，。',
    '公式与正文': '设 $x^2 + \\frac{1}{2} = \\sqrt{4}$ 成立\n公式不参与行距预算。',
}
HUMANIZATIONS = {
    '关闭': dict(enabled=False),
    '默认': dict(enabled=True, vertical=.3, scale=5., rotation=2., spacing=.2, baseline=.4),
    '全开': dict(enabled=True, vertical=2., scale=20., rotation=10., spacing=2., baseline=2.),
}


def predicted(extra):
    return document.predict_line_pitch(dict(BASE, pages=[dict(text=extra.pop('text'))], **extra), CHOICES)


def render_size(looseness, extra, seed):
    data = dict(BASE, line_looseness=looseness, seed=seed, **extra)
    return document.generate_document(FONT, data, CHOICES)[0]['metadata']


def render_ink(extra, seed):
    """Line ink of one seed at the looseness in `extra`, in millimetres."""
    config = document.configuration(dict(BASE, seed=seed, **extra))
    with contextlib.ExitStack() as stack:
        glyphs = document.Glyphs(document.normalized(CHOICES), stack, config, seed)
        tokens = document.tokenize(extra['text'], glyphs, config)
        plain = [t for t in tokens if t != '\n' and not t.formula]
        return (max((t.box.baseline for t in plain), default=0.)
                + max((t.box.height - t.box.baseline for t in plain), default=0.))


def render_scale(looseness, extra, seed):
    """How much prepare_notebook_tokens scales plain glyphs: 1 when nothing shrinks."""
    config = document.configuration(dict(BASE, line_looseness=looseness, seed=seed, **extra))
    with contextlib.ExitStack() as stack:
        glyphs = document.Glyphs(document.normalized(CHOICES), stack, config, seed)
        tokens = document.tokenize(extra['text'], glyphs, config)
        prepared = document.prepare_notebook_tokens(tokens, config)
    before = [t for t in tokens if t != '\n' and not t.formula]
    after = [t for t in prepared if t != '\n' and not t.formula]
    if not before:
        return 1.
    return min(after.box.height / before.box.height for before, after in zip(before, after) if before.box.height)


def check_floor_keeps_heights():
    """At the reported floor the renderer must not shrink the requested heights."""
    for label, text in TEXTS.items():
        for human_label, human in HUMANIZATIONS.items():
            for sizing in ('size', 'rows'):
                extra = dict(text=text, line_sizing=sizing, humanization=human)
                if sizing == 'rows':
                    extra['rows'] = 20
                result = predicted(dict(extra))
                if not result['feasible']:
                    # No looseness in 1–4 can hold the worst-case ink, so the floor is
                    # advisory: it must still be self-consistent and must not block
                    # generation for seeds that happen to fit.
                    assert result['ink_mm'] > result['pitch_mm'] - 2 * result['drift_mm'] + 1e-9, (
                        f'{label}/{human_label}/{sizing}: 报告不可行但预算仍够用：{result}')
                    render_size(result['min_ratio'], dict(extra, text=text), 'floor-a')
                    print(f'ok  {label}/{human_label}/{sizing}: 不可行（最坏情况仍超高），生成不受阻')
                    continue
                for seed in ('floor-a', 'floor-b', 'floor-c'):
                    scale = render_scale(result['min_ratio'], dict(extra, text=text), seed)
                    assert scale == 1, (
                        f'{label}/{human_label}/{sizing}: 在预测下限 {result["min_ratio"]} 处仍收缩到 {scale:.4f}')
                    metadata = render_size(result['min_ratio'], dict(extra, text=text), seed)
                    if sizing == 'size':
                        assert abs(metadata['latin_size'] - 5) < .001, f'{label}: 英文字高被压缩'
                    else:
                        assert abs(metadata['pitch'] - 11.85) < .001, f'{label}/{sizing}: 行距被改动'
                        assert metadata['size'] > 2, f'{label}/{sizing}: 自动字高低于 2 mm'
                print(f'ok  {label}/{human_label}/{sizing}: 下限 {result["min_ratio"]} 倍，字高未被压缩')


def check_tighter_than_floor():
    """Just below the floor the renderer must actually have to shrink."""
    shrunk = 0
    for label, text in TEXTS.items():
        extra = dict(text=text, line_sizing='size', humanization=dict(enabled=False))
        result = predicted(dict(extra))
        if result['min_ratio'] <= 1.02:
            continue
        scale = render_scale(result['min_ratio'] - .05, dict(extra, text=text), 'tighter')
        assert scale < 1 - 1e-4, f'{label}: 低于下限 {result["min_ratio"]} 仍未触发收缩，预测偏保守'
        shrunk += 1
        print(f'ok  {label}: 低于下限时收缩到 {scale:.4f} 倍')
    assert shrunk, '没有任何用例低于下限，断言未生效'


def check_budget_matches_renderer():
    """The prediction must bound every seed's ink without being wildly pessimistic."""
    for label, text in TEXTS.items():
        for human_label, human in HUMANIZATIONS.items():
            extra = dict(text=text, line_sizing='size', humanization=human)
            config = document.configuration(dict(BASE, line_looseness=2, **extra))
            extremes = document.ink_extremes([document.GFont(FONT)], document.normalized(CHOICES), text, None,
                                             config['humanization'])
            budget = document.line_pitch_budget(config, extremes)
            predicted_ink = budget['ink']
            worst = max(render_ink(dict(extra, text=text), seed) for seed in (f'ink-{i}' for i in range(24)))
            assert predicted_ink + 1e-6 >= worst, (
                f'{label}/{human_label}: 预测墨高 {predicted_ink:.4f} 小于实测最坏 {worst:.4f}，会溢出')
            assert predicted_ink <= worst * 1.35 + .05, (
                f'{label}/{human_label}: 预测墨高 {predicted_ink:.4f} 远超实测最坏 {worst:.4f}，过于保守')
            print(f'ok  {label}/{human_label}: 预测 {predicted_ink:.3f} mm，实测最坏 {worst:.3f} mm')


def check_modes():
    """Grid mode and the legacy request keep their own pitch and report no floor."""
    grid = document.predict_line_pitch(dict(BASE, layout_mode='grid', pages=[dict(text='国A')],
                                            grid=dict(columns=20, row_gap=2.9, column_gap=0,
                                                      size_percent=80, padding=.3, draw_lines=False)), CHOICES)
    assert grid['min_ratio'] == 1 and grid['feasible'], f'表格模式预测异常：{grid}'
    legacy = document.predict_line_pitch(dict(BASE, pages=[dict(text='国A')]), CHOICES)
    assert legacy['min_ratio'] == 1, f'旧请求预测异常：{legacy}'
    empty = document.predict_line_pitch(dict(BASE, line_sizing='size', pages=[dict(text='  \n ')]), CHOICES)
    assert empty['min_ratio'] == 1, f'空白文档预测异常：{empty}'
    print('ok  表格 / 旧请求 / 空白文档')


def check_overlap():
    """Overlap mode must keep the requested pitch and heights below the floor."""
    text = '油井Agjy压力不稳\n第二行油井Agjy'
    for sizing in ('size', 'rows'):
        extra = dict(text=text, line_sizing=sizing, allow_overlap=True, line_looseness=1.)
        if sizing == 'rows':
            extra['rows'] = 60
        result = document.predict_line_pitch(dict(BASE, pages=[dict(text=text)], **{k: v for k, v in extra.items() if k != 'text'}), CHOICES)
        assert result['min_ratio'] == 1, f'{sizing}: 允许重叠时下限应为 1，实际 {result["min_ratio"]}'
        assert result['allow_overlap'] and result['overlap'], f'{sizing}: 未回显允许重叠'
        # The measured floor is still reported so the hint can name what is ignored.
        reference = document.predict_line_pitch(dict(BASE, pages=[dict(text=text)], **{k: v for k, v in extra.items() if k not in ('text', 'allow_overlap')}), CHOICES)
        assert result['ratio'] == reference['ratio'], f'{sizing}: 参考下限被改动'
        sub_floor = {k: v for k, v in extra.items() if k != 'line_looseness'}
        scale = render_scale(1., sub_floor, 'overlap')
        assert scale == 1, f'{sizing}: 允许重叠时仍然压缩到 {scale:.4f}'
        metadata = render_size(1., sub_floor, 'overlap')
        assert metadata['allow_overlap'], f'{sizing}: 元数据缺少 allow_overlap'
        expected = 5. if sizing == 'size' else (297 - 60) / 60
        assert abs(metadata['pitch'] - expected) < .001, f'{sizing}: 行距被改动 {metadata["pitch"]}'
        if sizing == 'size':
            assert abs(metadata['size'] - 5) < .001, f'{sizing}: 字高被压缩 {metadata["size"]}'
        print(f'ok  {sizing}: 允许重叠时行距 {metadata["pitch"]:.2f} mm，未压缩字形')
    # Guards that must survive: one line taller than the page, and fixed pagination.
    try:
        document.generate_document(FONT, dict(BASE, line_sizing='size', size=20, allow_overlap=True, height=60,
                                              margin_top=25, margin_bottom=25, pages=[dict(text='国')]), CHOICES)
        raise AssertionError('允许重叠时单行超高未报错')
    except ValueError as error:
        assert '超过单页可写范围' in str(error), error
    try:
        document.generate_document(FONT, dict(BASE, pagination='fixed', line_sizing='size', line_looseness=1.,
                                              allow_overlap=True, pages=[dict(text='\n'.join(['油井Agjy'] * 60))]), CHOICES)
        raise AssertionError('允许重叠时固定分页未报错')
    except ValueError as error:
        assert '溢出' in str(error), error
    # Without the flag the same requests keep their old limits.
    try:
        document.generate_document(FONT, dict(BASE, line_sizing='rows', rows=200, line_looseness=1.,
                                              pages=[dict(text='中文测试')]), CHOICES)
        raise AssertionError('未允许重叠时 200 行小字未被拦截')
    except ValueError as error:
        assert '2–20 mm' in str(error), error
    print('ok  允许重叠时仍保留单行超高、固定分页与字高范围拦截')


def check_infeasible():
    """An impossible layout reports feasible=False and still yields a usable number."""
    result = document.predict_line_pitch(dict(BASE, height=60, margin_top=25, margin_bottom=25, size=20,
                                              line_sizing='size', pages=[dict(text='国A')]), CHOICES)
    assert not result['feasible'], f'不可行布局未被标记：{result}'
    assert 1 <= result['min_ratio'] <= 4, f'不可行布局的倍率越界：{result}'
    print(f'ok  不可行布局：倍率 {result["min_ratio"]}，feasible=False')


if __name__ == '__main__':
    check_floor_keeps_heights()
    check_tighter_than_floor()
    check_budget_matches_renderer()
    check_modes()
    check_overlap()
    check_infeasible()
    print('LINE_PITCH_VERIFY_OK')
