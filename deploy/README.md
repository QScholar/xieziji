# 部署与运行实例规程

本机正式服务不从仓库工作区运行。改动仓库后必须按本规程同步到运行实例并验证，否则用户看到的页面不会有任何变化。2026-10-25 的两次返工（改仓库未同步、就地编辑丢失 server.py）都源于跳过本规程，详见 [docs/development-log.md](../docs/development-log.md) 2026-10-25 条目。

## 运行拓扑

| 组件 | 计划任务 | 端口 | 运行位置 |
| --- | --- | --- | --- |
| 业务后端 | `Xieziji-Web` | 8080（0.0.0.0） | `releases/<commit>-<名称>/` |
| C++ 控制端 | `Xieziji-Controller` | 8090 | `releases/<commit>-<名称>/controller/bin/` |

- 计划任务执行 `releases/<release>/deploy/run-campus-service.ps1 -Service backend|controller`，工作目录由脚本从该 release 的 `deploy/services.json` 读取（`Release` 字段），后端以 `-m backend.server` 在该目录下启动，`STATIC = ROOT / 'frontend'` 因此指向 release 自带的 `frontend/`。
- 仓库工作区的 `frontend/`、`backend/` 只是开发整合副本；**改仓库不会改变运行中的服务**。
- 对外经隧道暴露（`--frontend-origin https://wm.102.yuna.team`，SakuraFrp 开机自启）。

## 判断“我在看哪个实例”

1. `netstat -ano | findstr :8080` 取 PID，再 `Get-CimInstance Win32_Process` 查命令行与父进程（父进程是 `run-campus-service.ps1` 的 powershell 即正式服务）。
2. 文件指纹：`curl -s http://127.0.0.1:8080/<文件> | md5sum`，分别对比仓库工作区与 release 副本中同名文件的 md5。
3. 前端改动的最终验证必须打在用户实际访问的服务（本机 8080）上，临时另起端口的实例只能用于开发调试。

## 改动如何生效

- **frontend**：后端每次请求实时读盘并响应 `Cache-Control: no-store`。把文件复制进 release 的 `frontend/` 后，浏览器普通刷新即生效，无需重启。但 index.html 引用的**新增**静态文件还要在 `server.py` 的 `files` 映射里登记，此项需重启。
- **backend Python**：模块在进程启动时加载，替换文件后必须重启任务才生效。
- 重启后端（仅在后端空闲时；`/api/state` 显示 running/paused/draining 时禁止）：

  ```powershell
  schtasks /End /TN Xieziji-Web
  schtasks /Run /TN Xieziji-Web
  ```

  控制端任务不要动，串口连接会断。

## 热更新脚本模式（改 backend 时必须遵循）

`deploy/deploy_*.ps1`（参考 `deploy_persistent_preview.ps1`、`deploy_shared_workspace.ps1`）+ `deploy/*-manifest.json` + tar 包，流程：

1. 校验 release 中待改文件与 manifest `expected` 哈希一致（防线上漂移）。
2. 备份到 `deploy/backups/before-<功能>-<时间戳>/`（before.tar + state-before.json）。
3. `schtasks /End` + 停后端进程 → 解包 → 安装后逐文件哈希校验 + `compileall` → `/Run` 重启。
4. 健康检查：控制端 PID 不变、任务 job_id 与 acknowledged 进度不回退、工作区内容不变、页面与新模块可访问。
5. 任一步失败自动回滚（解包 before.tar、删除新增文件）并重启，输出 `..._ROLLED_BACK`。

每次热更新的结果记录在备份目录的 `result.json`。改 backend 前先确认写字任务不在进行中（脚本自身也会核对此前的任务状态）。

## 文件操作纪律（2026-10-25 事故教训）

- release 目录受 git 跟踪。**每次**对 release 的文件操作前后运行 `git status --short`，出现预期外的 `D`/`M` 立即停下核对，不要带病继续。
- 禁止对部署文件使用先删后改型就地编辑（`sed -i`、重命名覆盖）。改法：用编辑工具确认后的内容整文件写入，或生成补丁后应用，随后 `git diff` 核对；多一行 `git status` 比丢一次文件便宜。
- 补救路径：内容在 git 历史里都有，`git checkout -- <release 路径>` 回滚，或从仓库版本恢复后用 `python -c "import backend.server"`（把 release 路径插入 sys.path）验证可导入。
- 2026-10-25 实例：`sed -i` 改 release 的 `backend/server.py` 时编辑失败，目标文件随之丢失；运行中进程不受影响（模块已加载进内存），从仓库版本补回并验证。当时若操作后立即 `git status`，丢失会被当场发现。
