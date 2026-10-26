$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$release = 'C:\Users\102\plotter-web-20261014\releases\d799564-20261020-autostart'
$config = Get-Content -LiteralPath (Join-Path $release 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$tasks = foreach ($name in @('Xieziji-Web', 'Xieziji-Controller')) {
    $task = Get-ScheduledTask -TaskName $name
    $info = Get-ScheduledTaskInfo -TaskName $name
    $boot = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' }).Count -gt 0
    if ([string]$task.State -ne 'Running' -or -not $boot -or $task.Principal.UserId -notmatch 'SYSTEM|S-1-5-18') { throw "Task validation failed: $name" }
    [pscustomobject]@{
        Name = $name
        State = [string]$task.State
        User = [string]$task.Principal.UserId
        BootTrigger = $boot
        RestartCount = [int]$task.Settings.RestartCount
        RestartInterval = [string]$task.Settings.RestartInterval
        ExecutionTimeLimit = [string]$task.Settings.ExecutionTimeLimit
        LastTaskResult = [long]$info.LastTaskResult
        Action = [string]$task.Actions[0].Arguments
    }
}
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(8080,8090) } | ForEach-Object {
    [pscustomobject]@{ Address = [string]$_.LocalAddress; Port = [int]$_.LocalPort }
})
$state = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/state' -TimeoutSec 15
if ($state.connected -or $state.controller -ne 'cpp') { throw 'Unexpected controller state' }
$python = 'C:\Users\102\AppData\Local\Programs\Python\Python310\python.exe'
$documentOutput = & $python (Join-Path $release 'deploy\verify-campus-document.py')
if ($LASTEXITCODE -ne 0) { throw 'Document verification failed' }
$report = [pscustomobject]@{
    Release = $release
    Tasks = @($tasks)
    Listeners = $listeners
    Controller = $state
    Document = ($documentOutput | ConvertFrom-Json)
}
$json = $report | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $release 'deploy\startup-acceptance.json'), $json, (New-Object Text.UTF8Encoding($false)))
$json
