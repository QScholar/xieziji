# Markdown 公式导入修复

- 版本：2026-10-22
- 发布目录：`releases/89df8a2-20261020-live-speed`
- 改动：`backend/document.py`、`backend/mathtext.py`
- 回滚：`deploy-git-rollback.ps1`（HEAD 即部署前状态）

## 问题

`tempfile/` 的两份 Markdown（`油井压力不稳定试井翻译.md` 为完整版，`_简化版.md` 为节选）在 8080 上**都无法生成**：

| 文件 | 部署前 `/api/preview` |
| --- | --- |
| 简化版 | `409 ord() expected a character, but string of length 119 found` |
| 完整版 | `409 ord() expected a character, but string of length 166 found` |

简化版“曾经可用”是 16:23 分中英文字高部署（`3c8cd01`）之前的状态。

### 根因 1：公式字号回归

`Glyphs.token` 为分设中英文字高改成按字符取高度：

```python
size = (self.config['latin_size'] if not cjk_character(source) else self.config['size']) if size is None else size
```

公式 token 的 `source` 是整段公式字符串，`cjk_character` 里的 `ord()` 必然抛 `TypeError`。任何含公式的文档都会失败，与字体、纸张参数无关。

### 根因 2：完整版独有的解析缺口

即便去掉回归，完整版仍会失败：

| 构造 | 位置 | 原行为 |
| --- | --- | --- |
| `\equiv`、`\varphi`、`\lbrack`、`\rbrack` | 式 12.2 / 12.3 / 12.5 / 12.8 | 暂不支持 LaTeX 命令 |
| `\left.`、`\right.` | 式 12.6 | 只接受 `()[]\|` |
| 公式内 `<sub>` | `φ(μc<sub>T</sub>)<sub>i</sub>·r<sub>w</sub>²` | 按字面字符排版 |
| `⁻¹ ⁻⁶ ²` | 正文 psia⁻¹、10⁻⁶、r<sub>D</sub>² | 字体无字形，报缺字 |
| `&lt;` `&gt;` | 正文 | 字面输出 |
| 超宽独立公式 | 式 12.8 宽 213.7 mm，可写 150 mm | 报“单个字符或公式超过可写宽度” |
| YAML front matter | 完整版开头 | 当正文排版 |

## 改法

| 缺陷 | 处理 |
| --- | --- |
| 公式字号回归 | `formula` 一律使用 `size`（中文字高），恢复 `3c8cd01` 之前的语义 |
| 缺命令 / 界定符 | 补 `equiv varphi lbrack rbrack`；`\left`/`\right` 支持 `.`（不可见）、`<>/`、`\{ \}` |
| 上下标无主体 | `sequence` 允许 `^`/`_` 直接起头，`<sub>` 转 `$_{…}$` 后可排 |
| 公式内 HTML | `Parser.__init__` 先规整 `<sub>`→`_`、`<sup>`→`^` |
| Unicode 上下标 | `Glyphs.box` 用 `SUPERSCRIPT`/`SUBSCRIPT` 映射成 0.62 倍真上下标，字体缺字形也不报错 |
| 正文标记 | `markdown_text` 剥 front matter、解 HTML 实体、`<sub>/<sup>` 转行内公式、丢弃无意义内联标签 |
| 超宽公式 | 独立公式按可写宽等比缩小；缩小超过一半才报错 |
| 行内上下标间距 | `$_{…}$` 类 token 不吃整字间距，紧贴前字 |

公式仍是自建单线排版，不执行 TeX，不引入新依赖。

## 兼容性

未涉及新语法与参数，`/api/preview` 与 `/api/coordinate-preview` 请求体不变。用相同种子对比部署前后：

| 用例 | gcode sha256 |
| --- | --- |
| 纯文本笔记 | 完全一致 |
| Markdown（无公式） | 完全一致 |
| 表格模式 + 格线 | 完全一致 |
| 固定行数自动字高 | 完全一致 |
| 含公式（部署前无法生成） | 现可生成 |

## 验证

服务端（`deploy/verify_markdown_formula.py`，部署后对线上源码运行）：

- 简化版 11 页 / 9 公式；完整版 14 页 / 47 公式；全部页边界在 30–180 mm × 30–267 mm 内。
- 构造回归：宏与界定符、不可见界定符、公式内 HTML 上下标、Unicode 上下标、HTML 实体、`\left<`、行内公式、分式与积分、front matter 剥离、超宽公式缩小、纯文本 / 固定字高 / 表格模式。

浏览器（127.0.0.1:8080，真实字体 `blood8-2_kvenjoy_blood8`）：

- 用“导入”按钮上传完整版 → `inputFormat` 自动切到 markdown、7500 字入框 → 生成成功，13 张纸来自 2 个预设内容页。
- 第 2 页显示式 12.1 与 12.2（含 `\left( \frac{1}{r_D} \right)`、`\equiv`），第 12 页显示式 12.6 与例题（含 `\left.`、`⁻¹`、`10⁻⁶`）。
- 简化版同样可生成。

## 部署

```powershell
.\deploy\deploy_markdown_formula_fix.ps1
```

脚本校验在线文件哈希、打包哈希、源码树干净，解包后 `compileall`，重启 `Xieziji-Web`，断言：行内公式、完整版 14 页、简化版 11 页、控制器 PID 与任务号未变；失败由 `git checkout HEAD` 回滚并重启。

实际执行记录：`Result=UPDATED`，控制器 PID 10168 未变，任务号 `c5afb6e7d74f427bb7b53861c918cd41` 与确认数 3620 未变，COM5 仍连接、Idle、抬笔。
