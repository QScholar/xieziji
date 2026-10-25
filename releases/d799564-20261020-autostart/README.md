# 写字机 · xieziji

浏览器编辑和预览，Python 后端生成路径，独立 C++ 控制端通过 USB 串口驱动现有 GRBL 写字机。控制端可运行于 Windows 电脑或 Linux / 树莓派。

许可：项目原创代码和演示字体采用 **PolyForm Noncommercial 1.0.0**，见 [LICENSE](LICENSE)。第三方 C++ 依赖的 MIT 许可见 [controller/third_party](controller/third_party/README.md)。第三方商业字体不随仓库分发。

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

## 多页笔记、表格与公式

首页先选择 **笔记模式** 或 **表格模式**。两种模式分别保存排版预设，切换时保留各自的当前设置；字体组合预设可以共用。

| 功能 | 使用方式 |
| --- | --- |
| 多页内容 | 添加预设内容页，分别编辑每页文字；每个预设内容页从新纸开始，无需用空格撑出分页 |
| 自动 / 固定分页 | 自动续页按实际文字与公式尺寸分页；固定每页内容时，溢出会明确报错，不截断文字 |
| 笔记排版 | 四边边距、字高、字间距、每页行数；保留开头空行和空格，中文标点可设置占位宽度 |
| 方格排版 | 行列数、行列间隔、格内边距和字号比例；普通字符每字一格，公式占连续多格 |
| 格线 | 默认只作预览参考，适合已有格子的纸；可选择实际绘制格线 |
| 文字与公式导入 | 追加导入 UTF-8 TXT / Markdown；基础 LaTeX 公式直接转成单线笔画 |
| 保存与下载 | 排版预设保存在浏览器；整份文档可保存 / 载入 JSON，输出页可逐页下载 G-code 或打包 ZIP |

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

### 基础 LaTeX 示例

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

## 任务与执行行为

1. 后端生成并缓存预览、元数据和同一份 G-code；即使控制端离线，也可排版和下载。
2. 开始时将任务以最多 32 KiB 的块上传到控制端磁盘，核对 SHA-256、命令数、页面范围、允许的 G-code 语法及首尾指令。
3. 控制端重新检查设备固件、Idle、Z0、坐标偏移、行程、速度和已验证的配置。
4. 在任何运动前持久化执行标记，然后从文件逐行读取、等待 `ok`。网络不参与每条指令的确认。
5. 最后一条指令确认后继续等待物理 Idle，检查确实返回本次工作原点并抬至 Z0，再报告完成。

控制端每个任务最多 32 MiB / 100 万条命令，上传与串口发送均使用有界缓冲。新版后端文档最多 30000 字、100 页、每页 20 万点及整份 200 万点，最多混选 16 种字体。缓存最多 8 份文档、合计最多 200 万点，按整份文档淘汰。旧单页 API 仍限制 3000 字。每台控制端一次只执行一个单页任务。

后端任务位于 `backend/jobs/<id>/`，包含 `job.gcode`、`preview.svg`、`metadata.json`。控制端任务位于 `controller-jobs/<id>/`，包含执行元数据、G-code、执行标记、设备检查、串口日志与状态。

- 暂停：停止供应后续路径，并发送 feed hold；不会自动抬笔。
- 继续：恢复同一次暂停任务。
- 终止：停止后续发送，发送 feed hold 和软复位，再关闭串口；不自动抬笔或回原点，需在设备端处理并重新确认位置。
- 手动控制：XY 单次微动、抬笔、完整落笔测试并回到 Z0；写字与手动动作互斥，手动取消使用 jog cancel。
- 网络或后端断开：已启动且完整缓存的任务继续在控制端执行；重新连接后查询真实状态。远程断线时无法保证远程按钮可达，设备端应保留停机手段。
- 重复请求：同一任务的重复启动不会重新发送路径；已经使用的任务 ID 不会在控制端重启后重新执行。上传可按偏移重试，但不同内容不能覆盖同一 ID。
- 报警/串口异常：停止发送并尽力请求 hold，进入故障状态；不自动解锁、复位或续写。
- 控制端退出/断电：没有断点续写。重启不自动连接、不自动启动；处理设备状态后生成新任务。

## 已验证设备约束

本版本保留此前的设备约束，不自动修改固件参数：固件 `1.3a.20221114`、115200 / 8N1、Z0 抬笔 / Z10 落笔、`$3=7`、`$100=80`、`$101=80`、`$102=100`、`$20/$21/$22=0`、G54 和刀具偏移为零。允许上次留下的 XY 临时 G92 偏移，每个任务通过 `G92 X0 Y0` 将当前物理位置设为本次原点。

本次是 Windows/Linux 的 C++ 控制程序，不是 ESP32 固件。未来可按同一服务协议替换设备实现，但 ESP32 的存储、网络和串口适配仍需单独移植。

## 测试

```powershell
.\controller\build.ps1
python -m unittest discover -v
```

或先用 CMake 构建，再运行 Python 测试。测试自动查找 `controller/build/plotter-controller[.exe]`；其他位置可设置 `PLOTTER_CONTROLLER_BINARY`。未找到二进制时，跨进程联调会明确跳过，不能视为完整验收。

C++ 测试覆盖上传完整性、范围校验、去重/重启、暂停/终止/互斥、报警停止发送、配置拒绝、物理 Idle、最终坐标及抬落笔。Python 测试保留原有字体、混排、拟真、布局、坐标与 API 回归，并通过真实模拟 C++ 进程核对下载/上传/串口发送字节完全一致，以及认证和独立前端 CORS。

2026-10-17 本地 Windows 验证：**79 项 Python 测试通过**，其中包括真实 C++ 模拟进程联调；浏览器验证了两种模式的独立预设、文档 JSON 保存与载入、Markdown 导入、多页预览及表格尺寸。新增文档测试覆盖空白页、超过 8 页的缓存、公式尺寸、标点留白，以及多页 ZIP / 控制端文件 / 模拟串口发送内容的一致性。

GitHub Actions 在 Windows / Ubuntu、Python 3.10 / 3.12 四个环境运行构建及测试，分别报告结果。测试启用默认编码检查；文本文件显式使用 UTF-8，G-code 使用 ASCII，避免依赖运行机器的区域设置。

这些结果是本地模拟验收；本次新增排版功能尚未进行真机书写验收。路径简化、空移重排与新增字形微变未纳入本次功能。

服务协议见 [docs/controller-protocol.md](docs/controller-protocol.md)。
