. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskController=Get-ScheduledTask -TaskName 'Xieziji-Controller'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
$taskNewRelease=Join-Path $taskRoot 'releases\89df8a2-20261020-limit12000'
$taskPackage=Join-Path $taskRoot 'deploy\limit12000-20261020.tar'
$taskFlag=Join-Path $taskRoot 'backend-jobs\.speed-limit-update-pending'
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release directory.'}
if((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne 'BD0E5B0862B6F1A3867B4684398A104F0A5D321F420B7FC3815969011B2FEE95'){throw 'Package hash mismatch.'}
if(Test-Path -LiteralPath $taskNewRelease){throw 'New controller release already exists; inspect before retrying.'}
$taskFiles=@('backend/layout.py','backend/document.py','backend/server.py','frontend/app.js','frontend/index.html')
if($LASTEXITCODE -ne 0){throw 'Backup failed.'}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
$taskControllerPid=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
$taskWebPid=(Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object -First 1).OwningProcess
$taskWebProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$taskWebPid"
if($taskWebProcess.CommandLine -notmatch 'backend\.server'){throw 'Unexpected backend process.'}
function Wait-Backend {
    $taskDeadline=(Get-Date).AddSeconds(30)
    do {$taskDeployed=@($taskFiles)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{return Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 3}catch{Start-Sleep -Milliseconds 400}}while((Get-Date) -lt $taskDeadline)
    throw 'Backend did not start.'
}
try {
    '12000' | Set-Content -LiteralPath $taskFlag -Encoding ASCII
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
    tar -xf $taskPackage -C $taskRelease
    if($LASTEXITCODE -ne 0){throw 'Extraction failed.'}
    New-Item -ItemType Directory -Path (Join-Path $taskNewRelease 'controller\bin'),(Join-Path $taskNewRelease 'deploy') | Out-Null
    Move-Item -LiteralPath (Join-Path $taskRelease 'plotter-controller-12000.exe') -Destination (Join-Path $taskNewRelease 'controller\bin\plotter-controller.exe')
    $taskOldDeploy=Join-Path $taskController.Actions[0].WorkingDirectory 'deploy'
    Copy-Item -LiteralPath (Join-Path $taskOldDeploy 'run-campus-service.ps1') -Destination (Join-Path $taskNewRelease 'deploy\run-campus-service.ps1')
    $taskConfig=Get-Content -LiteralPath (Join-Path $taskOldDeploy 'services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskConfig.Release=$taskNewRelease
    $taskConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskNewRelease 'deploy\services.json') -Encoding UTF8
    & (Join-Path $taskNewRelease 'controller\bin\plotter-controller.exe') --help | Out-Null
    if($LASTEXITCODE -ne 0){throw 'New controller cannot launch.'}
    & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
    if($LASTEXITCODE -ne 0){throw 'Backend compilation failed.'}
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    $taskAfter=Wait-Backend
    if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged -or $taskAfter.error){throw 'Task continuity check failed.'}
    $taskPidAfter=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
    if($taskPidAfter -ne $taskControllerPid){throw 'Running controller was unexpectedly replaced.'}
    if(-not $taskAfter.speed_limit_update_pending){throw 'Pending update guard is missing.'}
    $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing).Content
    if($taskHtml -notmatch 'max="12000"'){throw 'New speed controls not loaded.'}
    $taskArguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $taskRoot 'deploy\apply_limit12000_when_idle.ps1')+'"'
    $taskAction=New-ScheduledTaskAction -Execute $taskController.Actions[0].Execute -Argument $taskArguments -WorkingDirectory $taskRoot
    $taskTrigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
    $taskSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'Xieziji-Limit12000-Apply' -Action $taskAction -Principal $taskController.Principal -Trigger $taskTrigger -Settings $taskSettings -Description 'One-time controller update after the current operation finishes; never interrupt active motion.' -Force | Out-Null
    Start-ScheduledTask -TaskName 'Xieziji-Limit12000-Apply'
    [pscustomobject]@{Result='QUEUED_AFTER_IDLE';WebLimit=12000;ControllerPidUnchanged=$taskControllerPid;Phase=$taskAfter.phase;JobId=$taskAfter.job_id;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged} | ConvertTo-Json
} catch {
    $taskFailure=$_
    Stop-ScheduledTask -TaskName 'Xieziji-Limit12000-Apply' -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'Xieziji-Limit12000-Apply' -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $taskFlag -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'backend\.server' -and $_.ExecutablePath -eq $taskWebProcess.ExecutablePath} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}
    Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Wait-Backend | Out-Null
    throw $taskFailure
}
