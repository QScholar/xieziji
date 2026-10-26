param([switch]$Simulate, [int]$Port = 8080)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    py -3 -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '需要安装 Python 3.10 或更新版本' }
}
& $taskPython -c "import serial"
if ($LASTEXITCODE -ne 0) {
    $localWheel = Join-Path (Get-Location) 'analysis\tools\pyserial\pyserial-3.5-py2.py3-none-any.whl'
    if (Test-Path -LiteralPath $localWheel) { & $taskPython -m pip install $localWheel }
    else { & $taskPython -m pip install -r plotter\requirements.txt }
    if ($LASTEXITCODE -ne 0) { throw '串口依赖安装失败' }
}
$taskArgs = @('-m', 'plotter.server', '--port', "$Port")
if ($Simulate) { $taskArgs += '--simulate' }
& $taskPython @taskArgs
