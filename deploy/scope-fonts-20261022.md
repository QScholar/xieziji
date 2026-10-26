# 中英文分别设定字体

- 版本：2026-10-22
- 发布目录：`releases/89df8a2-20261020-live-speed`
- 改动：`backend/mixing.py`、`backend/document.py`、`frontend/{index.html,app.js,style.css,font-library.js}`、`README.md`
- 旧版单服务同步：`plotter/mixing.py`、`plotter/static/{index.html,app.js,style.css}`、`plotter/test_service.py`
- 回滚：部署前状态为 `ca74792`；`deploy-git-rollback.ps1` 恢复 Git HEAD，若已提交本功能，
  需要先 `git checkout ca74792 -- releases/89df8a2-20261020-live-speed` 再重启 `Xieziji-Web`

## 行为

“字体与组合”中每个已选字体新增 **适用范围**：`中英通用 / 仅中文 / 仅英文`；添加字体时也可先选范围。
中文字高、英文字高此前已分别设定，本次把“用哪个字体”也按同一个中文/英文分类拆开。

| 场景 | 结果 |
| --- | --- |
| 仅中文 + 仅英文 | 中文只用中文字体、英文只用英文字体，互不参与随机 |
| 中英通用 | 与旧行为一致，参与两种文字的随机选择 |
| 概率显示 | 按范围分别归一：中文字体显示“中 x%”，英文字体显示“英 x%”，通用字体两者都显示 |
| 缺字 | 在**同范围**的其余字体间重分配，并计入 `fallback_characters` |
| 某范围无字体 | 明确报错“没有可用的中文字体/英文字体，请为…添加字体：…” |
| 字体预设 / 文档 JSON | 保存并恢复 `scope`；旧预设无该字段时按“中英通用”补齐 |
| 表格模式 | 照常按格缩放，范围只决定选字来源 |

字符分类沿用排版既有规则：CJK 汉字、假名、全角形式与中文标点算中文，其余（ASCII、希腊字母、半角标点）算英文。公式仍按中文字高排。

## 接口

`font_mix` 的每一项可带 `scope`（`"all"` / `"zh"` / `"en"`，缺省 `"all"`）。非法值 409：
`字体适用范围必须是中英通用、中文或英文`。

元数据 `font_mix` 每项新增 `scope` 与 `probabilities`（`{"zh": 0.7}` 形式，只含该字体服务的文字）。
`probability`、`used`、`actual_probability` 保留原语义，旧前端不受影响。

## 兼容性

不带 `scope` 的请求仍按“中英通用”处理：相同种子下，笔记模式与表格模式的 gcode 与改动前逐字节一致
（`defa466f…` / `2f0fac83…`，由 `deploy/verify_scope_fonts.py` 断言）。

## 部署

```powershell
.\deploy\deploy_scope_fonts.ps1
```

脚本校验在线文件哈希、打包哈希、源码树干净，解包后 `compileall`，重启 `Xieziji-Web`，断言：
`verify_scope_fonts.py`（默认输出不变、分范围路由、分范围概率、缺范围报错、非法 scope 拒绝）、
线上 HTML/JS 含范围控件、线上 `/api/preview` 返回 `scope=zh,en`、C++ 控制端 PID 与任务号未变；
失败由 `git checkout HEAD` 回滚并重启。

## 验证记录

- 服务端：`中` 走中文字体 7 字、`English…` 走英文字体 20 字，`fallback_characters=0`；
  两中文字体 70:30 时 `probabilities.zh` 为 0.7/0.3；仅英文字体排中文报“没有可用的中文字体”。
- 浏览器（暂存后端 8091，真实字体）：禁用字体对话框前先选笔记模式；两行分别设为“仅中文/仅英文”后
  生成预览，实际分布显示 `（仅中文）7 字`、`（仅英文）17 字`；改为“中英通用”后概率变为 `中 50% · 英 100%`。
- 字体预设保存 `scope`，刷新后恢复；整份文档 JSON 载入后范围保持为 zh/en，并可生成预览。
- 旧版 `plotter/` 页面在模拟服务（8093）同样完成只中文/只英文预览。
- 线上部署（`Result=UPDATED`）：后端 PID 24480、控制端 PID 10168 未变，任务号
  `fb5c24e7…` 与 10180 条确认数未变；线上页面把 `blood8-2` 设为仅英文、`1111122` 设为仅中文后
  生成预览，实际分布为“仅英文 17 字 / 仅中文 7 字”，未发起真实运动。
- 旧测试套件 `python -m unittest plotter.test_service`：9 项中 7 项通过；`HTTPTests` 与
  `test_nonzero_origin_rejected_without_motion` 在本次改动前后同样失败（既有问题，与本功能无关）。
