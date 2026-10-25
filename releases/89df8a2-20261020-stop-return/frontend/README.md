# 前端

纯 HTML/CSS/JavaScript，无 Node 构建步骤。默认由业务后端托管，访问其 8080 端口。

独立部署时：

1. 修改 `config.js`：`window.PLOTTER_API_BASE = 'http://127.0.0.1:8080';`。
2. 在仓库根目录运行 `python -m http.server 8081 --bind 127.0.0.1 --directory frontend`。
3. 后端添加 `--api-only --frontend-origin http://127.0.0.1:8081`。
4. 打开 `http://127.0.0.1:8081`。后端配置访问令牌时，在页面输入同一令牌。

前端只访问业务 API，不保存控制端令牌，也不直接连接设备服务。切换站点地址后，浏览器的字体/排版预设仍按各自站点隔离。
