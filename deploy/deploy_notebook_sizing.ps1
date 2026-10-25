. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if (-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected release directory.' }
$taskPackage=Join-Path $taskRoot 'deploy\notebook-sizing-20261020.tar'
if ((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne 'B9D965983CD514E1040B7014EEA40C9AA3456CDB4E88E3C5017FF1B3DE4D535A') { throw 'Package hash mismatch.' }
$taskStamp=Get-Date -Format yyyyMMddHHmmss
$taskStage=Join-Path $taskRoot ('deploy\notebook-sizing-stage-'+$taskStamp)
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if ($LASTEXITCODE -ne 0) { throw 'Staging failed.' }
$taskFiles=@('backend/document.py','backend/coordinates.py','backend/server.py','frontend/app.js','frontend/index.html','frontend/style.css','frontend/notebook-sizing.js')
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
    $taskModule=Invoke-WebRequest http://127.0.0.1:8080/notebook-sizing.js -UseBasicParsing
    if($taskModule.Content -notmatch 'NotebookSizing'){throw 'Sizing module did not load.'}
    foreach($taskMode in @('rows','size')){
        $taskBody=@{layout_mode='notebook';width=100;height=100;margin=10;rows=4;size=5;line_sizing=$taskMode;line_looseness=2}|ConvertTo-Json
        $taskGuide=Invoke-RestMethod http://127.0.0.1:8080/api/coordinate-preview -Method Post -ContentType application/json -Body $taskBody
        if(($taskMode -eq 'rows' -and $taskGuide.metadata.size -ne 10) -or ($taskMode -eq 'size' -and $taskGuide.metadata.rows -ne 8)){throw 'Sizing API check failed.'}
    }
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
