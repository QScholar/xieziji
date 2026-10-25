param([string]$DeployRoot = 'C:\Users\102\plotter-web-20261014')
. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$taskRoot = (Resolve-Path -LiteralPath $DeployRoot).Path
$taskPackage = Join-Path $taskRoot 'deploy\output-layout-update-20261016.tar'
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
if ($taskState.phase -notin @('idle','complete','disconnected') -or ($taskState.connected -and $taskState.status -notlike '<Idle*')) {
    throw 'Device is busy; service was not changed.'
}
$taskRestorePort = if ($taskState.connected) { $taskState.port } else { $null }
$taskServiceStopped = $false

function Stop-PlotterService {
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -like '*-m plotter.server --host 0.0.0.0 --port 8080*' -and
        $_.CommandLine -like '*--allow-host 202.206.255.200*'
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Restore-PlotterConnection {
    $taskCurrent = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
    if ($taskRestorePort -and -not $taskCurrent.connected) {
        $taskBody = @{port=$taskRestorePort} | ConvertTo-Json
        Invoke-RestMethod 'http://127.0.0.1:8080/api/connect' -Method Post -ContentType 'application/json' -Body $taskBody | Out-Null
    }
}

$taskDeployed=@('plotter/server.py','plotter/layout.py','plotter/coordinates.py','plotter/machine.py','plotter/static/index.html','plotter/static/app.js','plotter/static/style.css')
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try {
    if ($taskRestorePort) {
        Invoke-RestMethod 'http://127.0.0.1:8080/api/disconnect' -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
    }
    $taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
    if ($taskState.connected) { throw 'Device reconnected during update preparation; aborting.' }
    Stop-PlotterService
    $taskServiceStopped = $true
    & tar.exe -xf $taskPackage -C $taskRoot
    if ($LASTEXITCODE -ne 0) { throw 'Update extraction failed' }
    & $taskPython -m compileall -q (Join-Path $taskRoot 'plotter')
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed' }
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Start-Sleep -Seconds 3
    $taskInvalidRejected = $false
    try {
        Invoke-RestMethod 'http://127.0.0.1:8080/api/jog' -Method Post -ContentType 'application/json' -Body '{"axis":"X","distance":0}' | Out-Null
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 409) { $taskInvalidRejected = $true }
        else { throw }
    }
    if (-not $taskInvalidRejected) { throw 'Invalid jog request was not rejected' }
    Restore-PlotterConnection
    $taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
    [pscustomobject]@{
        Task=(Get-ScheduledTask -TaskName 'Xieziji-Web').State.ToString()
        Connected=$taskState.connected
        Port=$taskState.port
        Phase=$taskState.phase
        Position=$taskState.position
        Pen=$taskState.pen
        InvalidJogRejected=$taskInvalidRejected
        Files=$taskDeployed
    } | ConvertTo-Json -Depth 4
} catch {
    $taskFailure = $_
    if ($taskServiceStopped) {
        Stop-PlotterService
        Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
        Start-ScheduledTask -TaskName 'Xieziji-Web'
        Start-Sleep -Seconds 3
    }
    Restore-PlotterConnection
    throw $taskFailure
}
