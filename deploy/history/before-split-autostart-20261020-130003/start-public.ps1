$ErrorActionPreference='Stop'
Set-Location -LiteralPath 'C:\Users\102\plotter-web-20261014'
Remove-Item Env:PLOTTER_TOKEN -ErrorAction SilentlyContinue
$taskProcess=Start-Process -FilePath 'C:\Users\102\plotter-web-20261014\.venv\Scripts\python.exe' -ArgumentList '-u -m plotter.server --host 0.0.0.0 --port 8080 --allow-unauthenticated --allow-host 202.206.255.200 --allow-origin-port 18081 --font-dir "C:\tools\奎享雕刻\奎享雕刻\gcodeFonts"' -WorkingDirectory 'C:\Users\102\plotter-web-20261014' -WindowStyle Hidden -RedirectStandardOutput 'C:\Users\102\plotter-web-20261014\deploy\server.stdout.log' -RedirectStandardError 'C:\Users\102\plotter-web-20261014\deploy\server.stderr.log' -Wait -PassThru
exit $taskProcess.ExitCode