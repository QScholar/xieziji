# 部署前快照（历史归档）

本目录保存 `deploy/backups/` 曾用 tar 归档的部署前文件，已转为普通文件交由 Git 版本管理。
每个子目录对应一次部署前的状态；`MANIFEST.json` 记录每个文件的 SHA-256 与来源归档，
`result.json` 是该次部署的结果记录（若当时生成）。

`.exe` 构建产物不入库，`MANIFEST.json` 的 `rebuildable` 字段保留其哈希与重建命令。

| 快照 | 文件数 | 可重建 | 有结果记录 |
| --- | --- | --- | --- |
| `before-cjk-latin-size-20261022162352` | 5 | 0 | 是 |
| `before-cjk-rows-20261020203543` | 2 | 0 | 否 |
| `before-device-profiles-20261021022959` | 8 | 1 | 是 |
| `before-eta-calibration-20261020193032` | 1 | 0 | 否 |
| `before-fold-ui-20261020211713` | 3 | 0 | 否 |
| `before-font-checks-20261021031507` | 5 | 0 | 是 |
| `before-font-chooser-20261021032108` | 4 | 0 | 是 |
| `before-font-preview-20261021110844` | 2 | 0 | 是 |
| `before-font-previews-20261020202726` | 4 | 0 | 是 |
| `before-font-speed-20261020230442` | 3 | 0 | 是 |
| `before-limit12000-controller` | 3 | 0 | 否 |
| `before-limit12000-web-20261020195517` | 5 | 0 | 否 |
| `before-live-speed-20261020190425` | 4 | 0 | 否 |
| `before-natural-wrapping-20261020210535` | 1 | 0 | 否 |
| `before-notebook-sizing-20261020201522` | 6 | 0 | 否 |
| `before-offset-fix-20261020-133507` | 2 | 0 | 否 |
| `before-page-notifications-20261021024828` | 4 | 0 | 是 |
| `before-speed-controls-20261020191909` | 6 | 0 | 否 |
| `before-split-autostart-20261020-130003` | 3 | 0 | 否 |
| `before-stop-return-20261020-135640` | 4 | 0 | 否 |
| `before-task-eta-20261020192719` | 4 | 0 | 否 |
| `before-workbench-20261021162959` | 3 | 0 | 是 |
| `before-workbench-type-20261021164115` | 2 | 0 | 是 |
| `before-wrapping-fix-20261020191229` | 3 | 0 | 否 |
