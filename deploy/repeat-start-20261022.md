# 内容可写即可开始写字

- 版本：2026-10-22
- 发布目录：`releases/89df8a2-20261020-live-speed`
- 回滚依据：Git `df060fd` 之前的提交（`Restore-CommittedFiles`）

## 问题

任务开始后浏览器清空 `currentJob`，后端又直接用预览 ID 调控制端，而控制端对
每个任务 ID 只执行一次并拒绝重放，因此已经写过、被终止或失败的页面无法再次开始，
即使页面内容仍可书写。

## 行为

| 场景 | 结果 |
| --- | --- |
| 页面已完整写完 | 重新勾选原点确认即可再次开始，无需重新生成预览 |
| 终止后 | 预览与 `currentJob` 保留，原位确认后可再写 |
| 同一预览连续开始 | 每次新建执行 ID，控制端逐次上传校验，互不覆盖 |
| 别的任务正在执行 | 返回“已有任务正在执行”，不重复提交 |
| 空白页 | 仍不可开始，后端返回“本页没有可书写内容” |
| 后端重启于任务中 | `.executions.json` 保留执行 ID→预览映射，ETA 仍能读取 G-code |

控制端的“任务 ID 只执行一次”约束不变；可重复性来自每次开始分配新执行 ID。

## 接口

`POST /api/start` 请求仍是 `{job_id: <预览ID>, origin_confirmed: true}`；
返回状态中的 `job_id` 为本次执行 ID。`backend-jobs/.executions.json` 记录映射，
最多保留 512 条。

## 部署

```powershell
.\deploy\deploy_repeat_start.ps1
```

脚本只在控制端处于 `complete` / `idle` / `disconnected` 时重启业务后端，
校验在线文件哈希、编译、重启 `Xieziji-Web`，随后运行
`deploy/verify_repeat_start.py`（同一预览连续开始两次、执行 ID 映射、ETA 路径、
运行中互斥、空白页拒绝、重启后映射仍可读），并确认 C++ 控制端 PID 与设备连接未变；
任一步失败用 Git HEAD 恢复这些文件。

## 验证记录

- 模拟控制端（`plotter-controller.exe --simulate`，端口 8091）+ 后端 8092：
  同一预览两次 `/api/start` 的 `job_id` 分别为不同执行 ID，两次均 `complete`。
- 长页面任务期间 `/api/state` 的 ETA `source=path` 且剩余秒数递减，
  证明执行 ID 已映射到预览的 `job.gcode`。
- 浏览器（后端托管页面）：写完一页后 `开始写字` 仍可用；切到第 2 页再切回
  已写页面仍可用；再次点击完成第二次书写；空白页保持禁用。
- 线上（8080 / `https://wm.102.yuna.team`）：页面与 `app.js` 已更新，
  控制器 PID 未变，状态 `complete`、笔已抬起，未发起真实运动。
