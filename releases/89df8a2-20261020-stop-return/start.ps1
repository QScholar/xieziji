param([int]$Port = 8080, [string]$ControllerUrl = 'http://127.0.0.1:8090', [string]$FontDir = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskBackendArgs = @('-m', 'backend.server', '--port', "$Port", '--controller-url', $ControllerUrl)
if ($FontDir) { $taskBackendArgs += @('--font-dir', $FontDir) }
python @taskBackendArgs
