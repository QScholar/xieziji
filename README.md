# 写字机 · xieziji

浏览器编辑和预览，Python 后端生成路径，独立 C++ 控制端通过 USB 串口驱动现有 GRBL 写字机。控制端可运行于 Windows 电脑或 Linux / 树莓派。

许可：项目原创代码和演示字体采用 **PolyForm Noncommercial 1.0.0**，见 [LICENSE](LICENSE)。第三方 C++ 依赖的 MIT 许可见 [controller/third_party](controller/third_party/README.md)。第三方商业字体不随仓库分发。

## 源码与个人数据

仓库包含服务源码、通用内置预设和测试样例。自定义排版与字体预设、设备档案、共享编辑内容、预览、任务记录、访问令牌和部署配置均属于实例数据，不随源码分发。新部署的自定义预设为空；请通过环境变量设置令牌，并使用独立的任务目录。商业字体不包含在仓库中。

## 三端职责

```text
浏览器 / frontend
        │ HTTP：文字、参数、预览、任务控制
        ▼
Python / backend :8080
        │ HTTP：任务分块上传、校验、执行控制、状态
        ▼
C++ / controller :8090
        │ 本地 USB 串口：115200 / 8N1
        ▼
现有写字机 GRBL 主板 → 电机
```

| 目录 | 职责 |
| --- | --- |
| `frontend/` | 独立 HTML/CSS/JS，编辑、排版参数、SVG 预览、进度与控制台 |
| `backend/` | 字体解析、混排、拟真、坐标转换、G-code 生成、预览与下载；仅使用 Python 标准库 |
| `controller/` | C++17 服务，原生串口、任务落盘、逐条确认、设备检查、执行状态机及日志 |
| `tests/` | Python 回归及真实 C++ 进程联调；旧 Python 控制实现只作为测试参考 |
| `docs/previous-single-service.md` | 拆分前的完整功能说明和历史部署记录 |

后端不导入串口库，不直接操作设备。控制端不读取字体，不排版，不生成 SVG。前端默认可由后端托管，也可独立托管。

设备名称、控制器类型、XY 映射、起点方向、抬落笔 Z 和速度等按串口保存到服务端，详见 [设备配置档案](docs/device-profiles.md)。当前一个业务后端只连接一个控制实例；Windows／树莓派多控制端选择及并行设备管理尚未实现，后续方案见 [开发日志](docs/development-log.md)。

## 字体直接预览与相似组合

生成路径时，所选字体组合缺少某个字，会按相似度从高到低查找字库中有该字形的字体补入；中文/英文分组仍按各自选定字体作为参照，权重 0 的字体不会被补字启用。组合内已有字形继续优先使用。补入字的实际笔画在页面预览及放大预览中标成橙色，并在字体分布下列出“原字体 → 替代字体”和相似评分。高亮只用于预览，下载及机器书写使用相同补字路径；所有候选都缺字、候选文件已变更或相似度样本不足时仍明确报缺字。字库后台检查会重建完整相似度排序，补字不局限于推荐的几种字体。字段 `external_fallback_characters` 为当前页补字次数，`glyph_fallbacks` 为字形、来源字体、替代字体、评分及出现编号；`font_mix` 中的 `external_fallback` 标记补字字体。

字体弹窗分为搜索字体、字体排序、字体列表、当前字体的预览、字形推荐五块。电脑使用双栏，手机使用单栏并支持折叠当前预览。列表显示真实单字样张、字形数量及速度评分；单字可修改，右侧试写文字可独立修改。搜索结果中的“预览”按钮同步更新右侧字形、字数、速度与推荐，列表标记“预览中”；手机上自动展开预览。预览不改变已选组合，点击“添加”才加入组合。

字数与速度排序保留全部字体并翻页，按当前字体相似度排序仅显示最接近的六项并排除自身；搜索不改变当前参照字体。字形推荐随当前候选更新，原有组合推荐在“相近笔形 · 变化组合”中展开使用。见[字体选择与右侧预览截图](docs/font-chooser.png)。

服务启动时检查一次 `.gfont` 字库，此后每 24 小时检查一次；字体选择弹窗的“刷新字库”可立即检查，并将下次检查安排在本次检查的 24 小时后。按文件名、大小与 SHA-256 检测新增、删除、重命名或内容变化，有变化时自动在后台重新计算速度评分和相似度（相似近邻保持前六名），无变化时跳过分析。无需重启服务，页面自动更新；“刷新预览”仍仅刷新样张。速度评分为模型估计。

部署时额外运行 `python -m pip install -r requirements-analysis.txt`，并保留 `scripts/analyze_font_similarity.py` 和 `scripts/analyze_font_speed.py`。分析通过独立子进程运行，不访问控制端或创建书写任务；成功结果及检查指纹保存在任务目录的 `.font-analysis/`，重启可复用，无变化时不重新分析。损坏文件会跳过并在界面提示；失败保留上一份结果，修改字体的旧分数不会沿用，可点刷新重试。分析期间再次刷新不会重复启动任务。

字体列表分页显示单字真实笔画样张；当前候选、已选字体和推荐组合使用统一试写文字（1–24 字），无需悬停。缺字明确显示方框，不借用其他字体。浏览样张不会生成写字任务或调用机器控制端。

“相近笔形 · 变化组合”提供 7 套分析推荐，应用后采用 60/25/15 权重，可继续调整并另存字体预设；只影响重新生成的路径。当前报告比较线上 304 个字体文件的 128 个共同常用汉字，以 blood8 真迹系列校准，结合归一化笔画墨迹、路径顺序与抬笔分段，并通过全部共同字核对重复候选。相似评分用于风格比较，不代表同一作者的概率；抽样不能保证每个字都相似。详细组合见 [分析报告](docs/font-similarity-report.md)，[实际笔画对照](docs/font-comparison.png)。

业务 API 使用 Python 标准库；自动分析和离线报告脚本额外需要 NumPy、Pillow。使用启动服务的同一个 Python 安装依赖，然后可以离线更新报告：

```powershell
python -m pip install -r requirements-analysis.txt
python scripts/analyze_font_similarity.py --font-dir ../gcodeFonts --output backend/font_similarity.json --report docs/font-similarity-report.md
python scripts/analyze_font_speed.py --font-dir ../gcodeFonts
python scripts/render_font_comparison.py --font-dir ../gcodeFonts
```

接口：`POST /api/font-previews` 接收 `{ "font_ids": ["字体编号"], "text": "春风十里" }`（每次最多 12 种），返回 SVG 与缺字列表；`GET /api/font-recommendations` 返回与当前字体文件匹配的分析推荐。`POST /api/font-refresh` 接收 `{}` 并启动后台检查；`GET /api/font-status` 返回检查/分析阶段、是否运行、上次检查与分析时间、下次检查时间、无效文件及错误。已有分析运行时再次刷新会复用同一任务。自动分析结果通过文件内容哈希核对，已删除或已修改字体的旧评分自动隐藏，其他可用字体仍可预览。

每种字体同时显示**速度评分**，支持按评分从快到慢排序。100 代表字库中位水平，分数越高越快（可超过 100）；200 约为参考模型中的两倍速度。统一比较 128 个汉字、5 mm 字高、绘图/移动 3000 mm/min、Z 行程 10 mm，计算落笔路径、笔间及字间空移、抬落笔和简化加减速。每字先与全库同一个字的耗时中位数比较，避免缺字造成虚高；少于 96 个样本字显示“样本较少”。评分是字体相对效率模型，不调整实际速度，也不替代任务 ETA；未模拟拐角减速和串口吞吐。完整排名见 [字体速度评分](docs/font-speed-report.md)。用 `python scripts/analyze_font_speed.py --font-dir ../gcodeFonts` 可离线更新；`--inventory` 与 `--overrides` 用于按线上清单和不同版本的字体副本计算。评分数据独立保存在 `backend/font_speed.json`，接口在每种字体的 `speed` 中返回分数、样本数量、路径长度和平均笔画段数。

## 多页笔记、表格与公式

排版区分别设置“绘图速度”（落笔走线）和“移动速度”（笔画间空移及抬落笔），范围均为 100–12000 mm/min，默认均为 10000；新预设、新设备档案及省略速度的 API 请求使用该默认值，已保存的自定义速度保留，设备档案中的旧默认组合 1800/3000 在更新时迁移为 10000/10000。修改后重新生成预览，预览指标显示两项速度；起止回位保持 3000。相邻笔画之间不再重复提交已经完成的抬笔命令。未额外添加抬落笔停顿。

任务区提供“实时写字速度”滑块：运行、暂停或等待完成时可调整为两项基础速度的 10%–200%，无需重新生成预览或中断任务。实时倍率上限按当前任务最高指令速度自动缩小，确保不超过 12000 mm/min。界面分别显示目标倍率与控制器已回报的倍率；机器状态显示 `FS` 回报的瞬时进给，它会随加减速、笔画长度及轴速限制变化。每个新任务自动恢复 100%。固件未回报 `Ov` 时禁用实时调速。下载文件保持生成时的路径与速度。

接口：`POST /api/speed`，请求为 `{ "job_id": "当前任务编号", "percent": 80 }`；转发至控制端 `POST /v1/control/speed`。只接受当前书写任务的整数百分比，拒绝过期任务、空闲、终止和回位阶段。使用 [GRBL 实时倍率命令](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Commands)，等待 `Ov` 回报后再逐步调整，不重新提交路径，也不自动继续暂停任务。

任务进度区显示 ETA（预计剩余时间、预计完成时刻）：开始时按各段绘图、空移和抬落笔的路径/速度加权估算，运行约 8 秒后按实际确认进度持续校准；实时调速以控制器已回报的倍率更新。暂停时冻结剩余估算、隐藏完成时刻，继续后排除暂停时间；终止、故障和失联时不显示过期完成时间。最后保留控制器缓存尾段的估算，全部指令确认后显示“机器收尾中”，仅收到机器实际完成确认才显示完成。ETA 为估算，不将指令回执等同于笔尖实际执行完成。

`GET /api/state` 增加 `eta: { remaining_seconds, estimated_finish_at, source, status }`，完成时刻是 Unix 秒时间戳；未知值为 `null`。估算在业务后端运行，同一后端的多个网页共用校准结果，刷新页面不会清空；后端重启可从已有任务文件接入当前任务并重新校准，文件不可用时退回实际指令进度估算，不操作串口。

控制区提供“每页完成提醒”：默认显示页内完成提示，点击“开启系统提醒”并允许浏览器通知后，每页实际完成时再发送一次系统通知，支持测试和关闭。仅控制端 `complete` 状态触发，发送完指令、暂停、终止及故障均不触发。通知记录按业务服务、端口和任务编号区分，保存最近 200 个任务，避免常规轮询及刷新重复提醒；浏览器支持 Web Locks 时也会串行处理同源多标签页的通知。同一任务在不同浏览器上的提醒独立。系统通知需要 HTTPS 或本机安全上下文及浏览器支持，使用无缓存 Service Worker 显示通知；本功能不包含 Web Push，请保持页面打开，后台休眠可能延迟提醒，关闭页面期间不会实时接收完成通知。系统通知失败或权限被拒绝时，页内提示仍可用。

首页先选择 **笔记模式** 或 **表格模式**。两种模式分别保存排版预设，切换时保留各自的当前设置；字体组合预设可以共用。

三类预设均在服务器持久保存，页面打开或刷新后可跨浏览器、设备读取；旧浏览器预设会自动迁移并保留本地备份。保存失败和其他页面的修改冲突会明确提示，详见[服务器预设](docs/server-presets.md)。

当前工作界面也按设备端口保存在服务端：编辑文字、分页、字体组合、排版参数和当前预览页自动同步到访问同一设备的手机、电脑。浏览器刷新或重新打开只恢复存档，不清空内容、不重新生成随机笔迹；SVG 和 G-code 由服务端生成并复用同一份文件。点击顶部“刷新界面”才会重置当前设备的文字和预览，其他设备存档、预设与已执行任务不受影响。并发修改通过版本校验避免旧页面覆盖新内容；冲突时本页修改保留，可先保存文档备份再载入共享内容。存档位于任务目录 `.workspaces/`，重启后可继续恢复。

| 功能 | 使用方式 |
| --- | --- |
| 多页内容 | 添加预设内容页，分别编辑每页文字；每个预设内容页从新纸开始，无需用空格撑出分页 |
| 自动 / 固定分页 | 自动续页按实际文字与公式尺寸分页；固定每页内容时，溢出会明确报错，不截断文字 |
| 笔记排版 | 四边边距、字高、字间距、每页行数；保留开头空行和空格，中文标点可设置占位宽度 |
| 方格排版 | 行列数、行列间隔、格内边距和字号比例；普通字符每字一格，公式占连续多格 |
| 格线 | 默认只作预览参考，适合已有格子的纸；可选择实际绘制格线 |
| 文字与公式导入 | 导入 UTF-8 TXT / Markdown 替换当前预设页；LaTeX 分式、矩阵、分段与多行公式直接转成单线笔画 |
| 保存与下载 | 字体、笔记排版、表格排版预设保存在服务器，多设备共用；整份文档可保存 / 载入 JSON，输出页可逐页下载 G-code 或打包 ZIP |

表格内置预设采用 **157 × 215 mm 可写范围、20 行 × 20 列、2.9 mm 行间隔**，对应格宽 7.85 mm、格高 7.995 mm，适合逐格书写。

<details>
<summary>查看表格模式预览</summary>

![表格模式：逐字方格与纸张路径预览](docs/grid-preview.jpg)

</details>

### 一份多页文档的使用流程

1. 选择模式、字体组合与排版预设，设置纸张范围及边距。
2. 编辑当前预设内容页，需要指定下一张纸的内容时点击“加页”；选择自动续页或固定每页内容。
3. 点击“生成全部页面预览”，在右侧切换输出页，检查文字、公式与格子位置。
4. 下载全部页面，或连接设备、确认纸张和原点后开始当前页。
5. 当前页完成后换纸，选择下一输出页并重新确认原点，再开始书写。空白页可以保留、下载，不发送书写任务。

每页是独立任务，没有自动送纸。预览、下载与执行使用同一份缓存路径；点击重新随机才重新生成。

### LaTeX 示例

```text
行内公式：$S = \pi r^2$

独立公式：
$$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$$

\newpage

$$\int_0^1 x^2 dx = \frac{1}{3}$$
```

支持分数、上下标、平方根、常用运算符和部分希腊字母；公式高度会参与分页。`\newpage` / `\pagebreak` 可以显式分页。尚不支持完整 TeX 环境、矩阵与自定义宏，不支持的语法会明确提示。

完整参数、文件格式与 API 见 [多页文档与排版说明](docs/document-layout.md)，可导入的示例见 [document-sample.md](tests/fixtures/document-sample.md)。

## Windows 快速开始

需要 Python 3.10+ 和支持 C++17 的编译器。下面使用已加入 PATH 的 MinGW g++；也可按下一节使用 CMake 构建。

```powershell
git clone https://github.com/QScholar/xieziji.git
cd xieziji
python -m pip install -r requirements-analysis.txt
.\controller\build.ps1
.\controller\build\plotter-controller.exe --simulate --jobs-dir .\controller-jobs
```

保持控制端终端运行，在第二个终端进入同一目录执行 `python -m backend.server`，然后打开 [本地写字台](http://127.0.0.1:8080)。选择排版模式、生成预览，再连接 `SIMULATOR` 即可验证书写流程。模拟模式运行真实 C++ 服务，串口设备由模拟器替代，不连接硬件。

仓库自带原创英文演示字体，可直接体验英文与基础公式。中文排版需要通过 `--font-dir` 或 `-FontDir` 指定用户自行提供、拥有使用权限的 `.gfont` 字体目录；第三方字体不随仓库分发。

使用真实写字机时，先退出模拟控制端，再启动不带 `--simulate` 的控制端；第二终端启动业务后端：

```powershell
# 终端一
.\controller\build\plotter-controller.exe --jobs-dir .\controller-jobs
# 终端二
.\start.ps1 -FontDir 'C:\path\to\gcodeFonts'
```

程序启动不自动连接或移动机器。先关闭原上位机的串口连接，在页面选择设备并连接，生成预览，放好纸张、确认方向和 Z0，再开始写字。停止后端不会终止独立控制端的任务。`start.ps1` 仅启动业务后端；模拟模式由控制端的 `--simulate` 参数决定。

也可以手动分别启动（两个终端）：

```powershell
.\controller\build\plotter-controller.exe --jobs-dir .\controller-jobs
python -m backend.server --controller-url http://127.0.0.1:8090 --font-dir 'C:\path\to\gcodeFonts'
```

## CMake 构建 / Linux 与树莓派

依赖头文件已固定版本并随仓库提供，构建无需下载依赖。需要 CMake 3.16+、C++17 编译器和线程库：

```bash
cmake -S controller -B controller/build -DCMAKE_BUILD_TYPE=Release
cmake --build controller/build --parallel 2
ctest --test-dir controller/build --output-on-failure
./controller/build/plotter-controller --jobs-dir ./controller-jobs
```

在另一终端或后端所在电脑运行：

```bash
python -m backend.server --controller-url http://127.0.0.1:8090
```

Linux 真实设备通常是 `/dev/ttyUSB0` 或 `/dev/ttyACM0`；运行用户需拥有串口访问权限（常见发行版为 `dialout` 组，具体以系统配置为准）。设备列表自动枚举 USB 串口。需要固定设备名时，使用 `--serial-port /dev/serial/by-id/你的设备` 将其加入允许列表；此参数不会自动连接。

Windows 二进制不能复制到树莓派执行，应在对应系统/架构重新编译。当前本地验证环境为 Windows；Linux 构建与测试已加入 CI，树莓派和真实串口尚待硬件验收。

## 后端与控制端分机部署

例如控制端 IP 为 `192.168.1.50`，仅在可信局域网或受保护隧道内使用。控制端：

```bash
export CONTROLLER_TOKEN='replace-with-a-long-random-token'
./controller/build/plotter-controller --host 0.0.0.0 --allow-host 192.168.1.50 --jobs-dir ./controller-jobs
```

后端（环境变量写法按操作系统调整）：

```bash
export CONTROLLER_TOKEN='replace-with-a-long-random-token'
python -m backend.server --controller-url http://192.168.1.50:8090
```

`CONTROLLER_TOKEN` 只供后端访问控制端。浏览器访问后端的令牌另用 `PLOTTER_TOKEN`：

```bash
export PLOTTER_TOKEN='replace-with-another-long-random-token'
python -m backend.server --host 0.0.0.0 --allow-host 192.168.1.20 --controller-url http://192.168.1.50:8090
```

非本机监听要求至少 16 位令牌。控制端拒绝携带 Origin 的浏览器请求并检查 Host；前端只访问后端。控制服务本身提供 HTTP，跨公网使用受控 HTTPS 代理或隧道，不直接暴露串口接口。没有自动修改防火墙。

独立部署前端的方法见 [frontend/README.md](frontend/README.md)。后端支持 `--api-only` 和显式 `--frontend-origin` 白名单。

本机正式服务由计划任务 `Xieziji-Web` / `Xieziji-Controller` 从 `releases/<commit>-<名称>/` 运行，与仓库工作区相互独立；改动生效规则、实例判别与热更新规程见 [deploy/README.md](deploy/README.md)。

## 任务与执行行为

1. 后端生成并缓存预览、元数据和同一份 G-code；即使控制端离线，也可排版和下载。
2. 开始时将任务以最多 32 KiB 的块上传到控制端磁盘，核对 SHA-256、命令数、页面范围、允许的 G-code 语法及首尾指令。
3. 控制端重新检查设备固件、Idle、Z0、坐标偏移、行程、速度和已验证的配置。
4. 在任何运动前持久化执行标记，然后从文件逐行读取、等待 `ok`。网络不参与每条指令的确认。
5. 最后一条指令确认后继续等待物理 Idle，检查确实返回本次工作原点并抬至 Z0，再报告完成。

控制端每个任务最多 32 MiB / 100 万条命令，上传与串口发送均使用有界缓冲。新版后端文档最多 30000 字、100 页、每页 20 万点及整份 200 万点，最多混选 16 种字体。内存缓存最多 8 份文档、合计最多 200 万点，按整份文档移出内存；预览、G-code、元数据和页序索引保存在磁盘，移出内存或后端重启后可原样恢复，不因缓存限制过期，也不重新随机排版。旧单页 API 仍限制 3000 字。每台控制端一次只执行一个单页任务。

后端任务位于 `backend/jobs/<id>/`，包含 `job.gcode`、`preview.svg`、`metadata.json`。控制端任务位于 `controller-jobs/<id>/`，包含执行元数据、G-code、执行标记、设备检查、串口日志与状态。

- 暂停：停止供应后续路径，并发送 feed hold；不会自动抬笔。
- 继续：恢复同一次暂停任务。
- 终止：立即停止后续路径发送并请求 feed hold，等待明确的 `Hold:0` 停稳确认后，软复位清空剩余队列。收到新的启动报文、重新检查配置并确认机器坐标未变化后，先用机器坐标抬笔至 Z0，再返回本次书写开始的 XY 位置；实际到位才报告终止完成，并保留连接。原点在任务开始时记录，不受复位清除 G92 的影响。回位期间点击“停止回原点”会取消回位并断开连接；报警、停稳超时、复位未确认或坐标校验失败时不继续自动回位，不自动解锁报警。终止后的旧任务不能续写，需重新生成并确认原点。GRBL 的停稳复位行为见 [mc_reset 实现](https://github.com/gnea/grbl/blob/master/grbl/motion_control.c)。
- 手动控制：XY 单次微动、抬笔、完整落笔测试并回到 Z0；写字与手动动作互斥，手动取消使用 jog cancel。
- 网络或后端断开：已启动且完整缓存的任务继续在控制端执行；重新连接后查询真实状态。远程断线时无法保证远程按钮可达，设备端应保留停机手段。
- 重复请求：同一任务的重复启动不会重新发送路径；已经使用的任务 ID 不会在控制端重启后重新执行。上传可按偏移重试，但不同内容不能覆盖同一 ID。
- 报警/串口异常：停止发送并尽力请求 hold，进入故障状态；不自动解锁、复位或续写。
- 控制端退出/断电：没有断点续写。重启不自动连接、不自动启动；处理设备状态后生成新任务。

## 已验证设备约束

本版本保留此前的设备约束，不自动修改固件参数：固件 `1.3a.20221114`、115200 / 8N1、Z0 抬笔 / Z10 落笔、`$3=7`、`$100=80`、`$101=80`、`$102=100`、`$20/$21/$22=0`。G54 Z、G92 Z 和刀具偏移必须为零。允许已有的 G54 XY 工作偏移和上次留下的 XY 临时 G92 偏移，每个任务通过 `G92 X0 Y0` 将当前物理位置设为本次原点；保留已保存的 G54 设置。结束时按 G54 与 G92 的 XY 偏移之和核对机器是否回到本次原点，计算依据为 [GRBL 坐标转换实现](https://github.com/gnea/grbl/blob/master/grbl/gcode.c)。

本次是 Windows/Linux 的 C++ 控制程序，不是 ESP32 固件。未来可按同一服务协议替换设备实现，但 ESP32 的存储、网络和串口适配仍需单独移植。

## 测试

```powershell
.\controller\build.ps1
python -m pip install -r requirements-analysis.txt
python -m unittest discover -v
```

或先用 CMake 构建，再运行 Python 测试。测试自动查找 `controller/build/plotter-controller[.exe]`；其他位置可设置 `PLOTTER_CONTROLLER_BINARY`。未找到二进制时，跨进程联调会明确跳过，不能视为完整验收。

C++ 测试覆盖上传完整性、范围校验、去重/重启、暂停/终止/互斥、报警停止发送、配置拒绝、物理 Idle、最终坐标及抬落笔。Python 测试保留原有字体、混排、拟真、布局、坐标与 API 回归，并通过真实模拟 C++ 进程核对下载/上传/串口发送字节完全一致，以及认证和独立前端 CORS。

2026-10-21 字体更新验证：131 项 Python 测试通过，新增字库检查测试覆盖新增/删除、同大小文件替换、损坏字体、失败重试、重启复用、空目录及检查并发。真实浏览器验证三种排序、相似度前六名、搜索结果预览与右侧联动、预览不添加字体、手机折叠及现有组合功能；线上 304 个字体完成分析并验证无变化跳过。相关浏览器脚本为 `tests/test_font_library_ui.cjs` 和 `tests/test_font_updates_ui.cjs`，需要 Node.js、Playwright 及 Microsoft Edge；前者使用用户自备的完整字库，后者使用仓库测试字体，不发送机器控制命令。

2026-10-17 本地 Windows 验证：**79 项 Python 测试通过**，其中包括真实 C++ 模拟进程联调；浏览器验证了两种模式的独立预设、文档 JSON 保存与载入、Markdown 导入、多页预览及表格尺寸。新增文档测试覆盖空白页、超过 8 页的缓存、公式尺寸、标点留白，以及多页 ZIP / 控制端文件 / 模拟串口发送内容的一致性。

GitHub Actions 在 Windows / Ubuntu、Python 3.10 / 3.12 四个环境运行构建及测试，分别报告结果。测试启用默认编码检查；文本文件显式使用 UTF-8，G-code 使用 ASCII，避免依赖运行机器的区域设置。

这些结果是本地模拟验收；本次新增排版功能尚未进行真机书写验收。路径简化、空移重排与新增字形微变未纳入本次功能。

服务协议见 [docs/controller-protocol.md](docs/controller-protocol.md)。
