. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if (-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected release directory.' }
$taskPackage=Join-Path $taskRoot 'deploy\natural-wrapping-20261020.tar'
if ((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne '9C738A5AE5A9CC23E3C73BC03E32B8A13C0A23965091348DEF30FF088F7428AD') { throw 'Package hash mismatch.' }
$taskStamp=Get-Date -Format yyyyMMddHHmmss
$taskStage=Join-Path $taskRoot ('deploy\natural-wrapping-stage-'+$taskStamp)
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if ($LASTEXITCODE -ne 0) { throw 'Staging failed.' }
$taskFiles=@('backend/document.py')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskStage 'manifest.json') -Raw | ConvertFrom-Json
$taskExisting=@()
foreach($taskFile in $taskFiles){
    $taskEntry=$taskManifest.$taskFile
    if ((Get-FileHash -LiteralPath (Join-Path $taskStage $taskFile) -Algorithm SHA256).Hash -ne $taskEntry.after) { throw ('Staged file mismatch: '+$taskFile) }
    if($taskEntry.before){
        if ((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskFile) -Algorithm SHA256).Hash -ne $taskEntry.before) { throw ('Live source changed; rebase update: '+$taskFile) }
        $taskExisting+=,$taskFile
    }elseif(Test-Path -LiteralPath (Join-Path $taskRelease $taskFile)){throw ('New file already exists: '+$taskFile)}
}
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
$taskControllerPid=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
function Stop-Backend {
    $taskListener=Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if($taskListener){
        $taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListener.OwningProcess)"
        if($taskProcess.CommandLine -notmatch 'backend\.server'){throw 'Unexpected listener.'}
    }
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    if($taskListener){Stop-Process -Id $taskListener.OwningProcess -Force -ErrorAction SilentlyContinue}
}
function Wait-Backend {
    $taskDeadline=(Get-Date).AddSeconds(30)
    do {$taskDeployed=@($taskExisting)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{return Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 3}catch{Start-Sleep -Milliseconds 300}} while((Get-Date) -lt $taskDeadline)
    throw 'Backend did not restart.'
}
try{
    Stop-Backend
    foreach($taskFile in $taskFiles){Copy-Item -LiteralPath (Join-Path $taskStage $taskFile) -Destination (Join-Path $taskRelease $taskFile) -Force}
    $taskConfig=Get-Content -LiteralPath (Join-Path $taskRelease 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
    if($LASTEXITCODE -ne 0){throw 'Python compilation failed.'}
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    $taskAfter=Wait-Backend
    $taskPidAfter=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
    if($taskPidAfter -ne $taskControllerPid){throw 'Controller PID changed.'}
    if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged -or $taskAfter.error){throw 'Task continuity check failed.'}
    $taskFonts=Invoke-RestMethod http://127.0.0.1:8080/api/fonts
    $taskFont=$taskFonts.fonts | Where-Object {$_.name -eq 'blood8-2_kvenjoy_blood8'} | Select-Object -First 1
    if(-not $taskFont){throw 'Verification font unavailable.'}
    $taskText=(@('固定行数，自动计算字高。')*20)-join "`n"
    foreach($taskMode in @('rows','size')){
        $taskPayload=@{layout_mode='notebook';font_id=$taskFont.id;width=210;height=297;margin=30;rows=20;size=7.9;line_sizing=$taskMode;line_looseness=1.5;text=$taskText;seed='live-cjk-row-check'}
        $taskBody=[Text.Encoding]::UTF8.GetBytes(($taskPayload|ConvertTo-Json -Depth 5))
        $taskPreview=Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBody -TimeoutSec 30
        if($taskPreview.page_count -ne 1 -or $taskPreview.pages[0].metadata.lines.Count -ne 20 -or $taskPreview.pages[0].metadata.row_guides.Count -ne 20){throw ('Chinese row check failed: '+$taskMode)}
    }
    $taskText='光时域反射仪（英文名称：optical time-domain reflectometer，OTDR）是通过对测量曲线的分析，了解光纤的均匀性、缺陷、断裂、接头耦合等若干性能的仪器。它根据光的后向散射与菲涅耳反向反射制作，利用在光纤中传输时产生的后向散射光来获取衰减的信息。'
    $taskPayload=@{layout_mode='notebook';font_id=$taskFont.id;width=210;height=297;margin=30;rows=40;line_sizing='rows';line_looseness=1.3;text=$taskText;seed='wrap'}
    $taskBody=[Text.Encoding]::UTF8.GetBytes(($taskPayload|ConvertTo-Json -Depth 5))
    $taskPreview=Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBody -TimeoutSec 30
    $taskLines=$taskPreview.pages[0].metadata.lines
    if($taskPreview.page_count -ne 1 -or $taskLines.Count -gt 4 -or $taskLines[0] -notmatch 'reflectometer') {throw 'Natural wrapping check failed.'}
    Write-Output ('VERIFIED optical text: '+$taskLines.Count+' rows; complete English words on first row')
    [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;JobId=$taskAfter.job_id;Phase=$taskAfter.phase;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged}|ConvertTo-Json
}catch{
    $taskFailure=$_
    Stop-Backend
    Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Wait-Backend|Out-Null
    Write-Output 'BACKEND_ROLLED_BACK; controller was not stopped'
    throw $taskFailure
}
