param([string]$DeployRoot = 'C:\Users\102\plotter-web-20261014')
. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$taskRoot = (Resolve-Path -LiteralPath $DeployRoot).Path
$taskPackage = Join-Path $taskRoot 'deploy\font-mix-update-20261016.tar'
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
if ($taskState.connected -or $taskState.phase -in @('running','paused','draining')) {
    throw 'Device is connected or busy; service was not changed.'
}

function Stop-PlotterService {
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -like '*-m plotter.server --host 0.0.0.0 --port 8080*' -and
        $_.CommandLine -like '*--allow-host 202.206.255.200*'
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Stop-PlotterService
$taskDeployed=@('plotter/server.py','plotter/layout.py','plotter/static/index.html','plotter/static/app.js','plotter/static/style.css')
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try {
    & tar.exe -xf $taskPackage -C $taskRoot
    if ($LASTEXITCODE -ne 0) { throw 'Update extraction failed' }
    & $taskPython -m compileall -q (Join-Path $taskRoot 'plotter')
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed' }
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Start-Sleep -Seconds 3
    $taskHeaders = @{ Host='202.206.255.200:8080'; Origin='http://202.206.255.200:8080' }
    $taskFonts = Invoke-RestMethod 'http://127.0.0.1:8080/api/fonts' -Headers $taskHeaders
    $taskA = $taskFonts.fonts | Where-Object { $_.name -eq 'blood8-2_kvenjoy_blood8' } | Select-Object -First 1
    $taskB = $taskFonts.fonts | Where-Object { $_.name -eq 'blood8真迹2_kvenjoy_blood8' } | Select-Object -First 1
    if (-not $taskA -or -not $taskB) { throw 'Deployment verification fonts missing' }
    $taskData = @{
        text="你好，世界。让文字落在纸上。`nLearning opens the door to new ideas."
        seed='deployment-check'
        font_mix=@(@{font_id=$taskA.id;weight=70},@{font_id=$taskB.id;weight=30})
    } | ConvertTo-Json -Depth 4
    $taskBody = [Text.Encoding]::UTF8.GetBytes($taskData)
    $taskPreview = Invoke-RestMethod 'http://127.0.0.1:8080/api/preview' -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBody -Headers $taskHeaders
    $taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
    [pscustomobject]@{
        Task=(Get-ScheduledTask -TaskName 'Xieziji-Web').State.ToString()
        FontCount=$taskFonts.fonts.Count
        FontMix=$taskPreview.metadata.font_mix
        Commands=$taskPreview.metadata.commands
        Connected=$taskState.connected
        Files=$taskDeployed
    } | ConvertTo-Json -Depth 5
} catch {
    $taskFailure = $_
    Stop-PlotterService
    Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    throw $taskFailure
}
