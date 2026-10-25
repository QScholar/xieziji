$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskRelease=Join-Path $taskRoot 'releases\89df8a2-20261020-live-speed'
$taskPackage=Join-Path $taskRoot 'deploy\live-speed-20261020.tar'
$taskBackup=Join-Path $taskRoot ('deploy\backups\before-live-speed-'+(Get-Date -Format yyyyMMddHHmmss))
$taskState=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
if ($taskState.phase -notin @('idle','complete','stopped','disconnected') -or
    ($taskState.connected -and $taskState.status -notlike '<Idle*')) { throw 'Machine is busy; no service changed.' }
$taskRestorePort=if($taskState.connected){$taskState.port}else{$null}
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskController=Get-ScheduledTask -TaskName 'Xieziji-Controller'
if (Test-Path -LiteralPath $taskRelease) { throw 'New release directory already exists; inspect it before retrying.' }
New-Item -ItemType Directory -Path $taskRelease,$taskBackup | Out-Null
Export-ScheduledTask -TaskName $taskWeb.TaskName | Set-Content -LiteralPath (Join-Path $taskBackup 'web.xml') -Encoding UTF8
Export-ScheduledTask -TaskName $taskController.TaskName | Set-Content -LiteralPath (Join-Path $taskBackup 'controller.xml') -Encoding UTF8
$taskState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-before.json') -Encoding UTF8
if ((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne '678EE058DE6803E9C5DF287A2679411C10DAB3721A45C35EC806A79769CD20B7') { throw 'Package hash mismatch.' }
tar -xf $taskPackage -C $taskRelease
if ($LASTEXITCODE -ne 0) { throw 'Package extraction failed.' }
$taskOldDeploy=Join-Path $taskWeb.Actions[0].WorkingDirectory 'deploy'
New-Item -ItemType Directory -Path (Join-Path $taskRelease 'deploy'),(Join-Path $taskRelease 'controller\bin') | Out-Null
Copy-Item -LiteralPath (Join-Path $taskOldDeploy 'run-campus-service.ps1') -Destination (Join-Path $taskRelease 'deploy\run-campus-service.ps1')
$taskConfig=Get-Content -LiteralPath (Join-Path $taskOldDeploy 'services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskConfig.Release=$taskRelease
$taskConfig.Backup=$taskBackup
$taskConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskRelease 'deploy\services.json') -Encoding UTF8
Move-Item -LiteralPath (Join-Path $taskRelease 'plotter-controller-speed.exe') -Destination (Join-Path $taskRelease 'controller\bin\plotter-controller.exe')
& $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
& (Join-Path $taskRelease 'controller\bin\plotter-controller.exe') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'New controller cannot start.' }
function Wait-PlotterApi([string]$Url) {
    $taskDeadline=(Get-Date).AddSeconds(35)
    do {
        try { return Invoke-RestMethod $Url -TimeoutSec 3 } catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $taskDeadline)
    throw ('Service did not start: '+$Url)
}
function Stop-PlotterTasks {
    $taskProcesses=@(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -in @(8080,8090)} | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach($taskProcessId in $taskProcesses) {
        $taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$taskProcessId"
        if ($taskProcess.CommandLine -notmatch 'backend\.server|plotter-controller\.exe') { throw 'Unexpected process owns a service port.' }
    }
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Stop-ScheduledTask -TaskName 'Xieziji-Controller'
    foreach($taskProcessId in $taskProcesses) { Stop-Process -Id $taskProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
}
$taskChanged=$false
try {
    # Recheck immediately before the short outage. Do not interrupt a new task.
    $taskState=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
    if ($taskState.phase -notin @('idle','complete','stopped','disconnected') -or
        ($taskState.connected -and $taskState.status -notlike '<Idle*')) { throw 'A new task started; services remain unchanged.' }
    if ($taskRestorePort) { Invoke-RestMethod http://127.0.0.1:8080/api/disconnect -Method Post -ContentType application/json -Body '{}' | Out-Null }
    $taskChanged=$true
    Stop-PlotterTasks
    foreach($taskItem in @(@('Xieziji-Web','backend'),@('Xieziji-Controller','controller'))) {
        $taskArguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $taskRelease 'deploy\run-campus-service.ps1')+'" -Service '+$taskItem[1]
        $taskAction=New-ScheduledTaskAction -Execute $taskWeb.Actions[0].Execute -Argument $taskArguments -WorkingDirectory $taskRelease
        Set-ScheduledTask -TaskName $taskItem[0] -Action $taskAction | Out-Null
    }
    Start-ScheduledTask -TaskName 'Xieziji-Controller'
    Wait-PlotterApi http://127.0.0.1:8090/v1/state | Out-Null
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Wait-PlotterApi http://127.0.0.1:8080/api/state | Out-Null
    if ($taskRestorePort) {
        $taskBody=@{port=$taskRestorePort}|ConvertTo-Json
        Invoke-RestMethod http://127.0.0.1:8080/api/connect -Method Post -ContentType application/json -Body $taskBody -TimeoutSec 30 | Out-Null
    }
    $taskAfter=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
    if ($taskAfter.phase -notin @('idle','disconnected') -or $taskAfter.error) { throw 'Unexpected service state after restart.' }
    $taskPage=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing).Content
    if ($taskPage -notmatch 'id="liveSpeed"' -or $taskPage -notmatch 'max="10000"') { throw 'New frontend was not loaded.' }
    if ($null -eq $taskAfter.feed_override_requested) { throw 'New controller was not loaded.' }
    $taskAfter | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-after.json') -Encoding UTF8
    [pscustomobject]@{Result='RESTARTED';Release=$taskRelease;Backup=$taskBackup;State=$taskAfter} | ConvertTo-Json -Depth 8
} catch {
    $taskFailure=$_
    if($taskChanged) {
        try {
            Stop-PlotterTasks
            Set-ScheduledTask -TaskName 'Xieziji-Web' -Action $taskWeb.Actions | Out-Null
            Set-ScheduledTask -TaskName 'Xieziji-Controller' -Action $taskController.Actions | Out-Null
            Start-ScheduledTask -TaskName 'Xieziji-Controller'
            Wait-PlotterApi http://127.0.0.1:8090/v1/state | Out-Null
            Start-ScheduledTask -TaskName 'Xieziji-Web'
            Wait-PlotterApi http://127.0.0.1:8080/api/state | Out-Null
            if($taskRestorePort) { Invoke-RestMethod http://127.0.0.1:8080/api/connect -Method Post -ContentType application/json -Body (@{port=$taskRestorePort}|ConvertTo-Json) -TimeoutSec 30 | Out-Null }
            Write-Output 'ROLLED_BACK'
        } catch { Write-Error ('Rollback failed: '+$_) }
    }
    throw $taskFailure
}
