$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskPackage=Join-Path $taskRoot 'deploy\ink-spacing-20261024.tar'
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'deploy\ink-spacing-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskXml=[xml](schtasks.exe /Query /TN Xieziji-Web /XML | Out-String)
if($LASTEXITCODE -ne 0){throw 'Cannot inspect backend task.'}
$taskRelease=(Resolve-Path -LiteralPath $taskXml.Task.Actions.Exec.WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release directory.'}
if((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch.'}
$taskOldFiles=@();$taskAddedFiles=@()
foreach($taskEntry in $taskManifest.files.PSObject.Properties){
    $taskPath=Join-Path $taskRelease $taskEntry.Name
    $taskExpected=$taskManifest.expected.PSObject.Properties[$taskEntry.Name]
    if($taskExpected){
        if((Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash -ne $taskExpected.Value){throw ('Online source changed: '+$taskEntry.Name)}
        $taskOldFiles+=$taskEntry.Name
    }else{
        if(Test-Path -LiteralPath $taskPath){throw ('New file unexpectedly already exists: '+$taskEntry.Name)}
        $taskAddedFiles+=$taskEntry.Name
    }
}
function Listener-Pid([int]$taskPort){
    $taskRows=@(netstat -ano -p tcp | Where-Object {$_ -match (':'+$taskPort+'\s+\S+\s+LISTENING\s+\d+\s*$')})
    if($taskRows.Count -ne 1){throw ('Unexpected listener count on '+$taskPort)}
    return [int](($taskRows[0].Trim() -split '\s+')[-1])
}
function Wait-Backend {
    $taskDeadline=(Get-Date).AddSeconds(30)
    do{try{return Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 3}catch{Start-Sleep -Milliseconds 300}}while((Get-Date)-lt $taskDeadline)
    throw 'Backend did not start.'
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=Get-Content -LiteralPath (Join-Path $taskRelease 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable.'}
$taskBackup=Join-Path $taskRoot ('deploy\backups\before-ink-spacing-'+(Get-Date -Format yyyyMMddHHmmss))
New-Item -ItemType Directory -Path $taskBackup | Out-Null
tar -cf (Join-Path $taskBackup 'before.tar') -C $taskRelease @taskOldFiles
if($LASTEXITCODE -ne 0){throw 'Backup failed.'}
$taskBefore | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-before.json') -Encoding UTF8
try{
    # The controller process and its device connection are never stopped.
    schtasks.exe /End /TN Xieziji-Web | Out-Null
    Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
    tar -xf $taskPackage -C $taskRelease
    if($LASTEXITCODE -ne 0){throw 'Extraction failed.'}
    foreach($taskEntry in $taskManifest.files.PSObject.Properties){
        if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name) -Algorithm SHA256).Hash -ne $taskEntry.Value){throw ('Installed hash mismatch: '+$taskEntry.Name)}
    }
    & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
    if($LASTEXITCODE -ne 0){throw 'Compilation failed.'}
    schtasks.exe /Run /TN Xieziji-Web | Out-Null
    $taskAfter=Wait-Backend
    if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed.'}
    if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged -or $taskAfter.error){throw 'Controller continuity check failed.'}
    $taskResponse=Invoke-RestMethod http://127.0.0.1:8080/api/font-status -TimeoutSec 10
    $taskResult=[pscustomobject]@{Result='UPDATED';Release=$taskRelease;ControllerPid=$taskControllerPid;JobId=$taskAfter.job_id;Phase=$taskAfter.phase;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged;FontAnalysis=$taskResponse.phase;Backup=$taskBackup}
    $taskResult | ConvertTo-Json | Tee-Object -FilePath (Join-Path $taskBackup 'result.json')
}catch{
    $taskFailure=$_
    schtasks.exe /End /TN Xieziji-Web | Out-Null
    try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
    tar -xf (Join-Path $taskBackup 'before.tar') -C $taskRelease
    foreach($taskFile in $taskAddedFiles){
        $taskRemove=[IO.Path]::GetFullPath((Join-Path $taskRelease $taskFile))
        if(-not $taskRemove.StartsWith($taskRelease+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe rollback file path.'}
        Remove-Item -LiteralPath $taskRemove -Force -ErrorAction SilentlyContinue
    }
    schtasks.exe /Run /TN Xieziji-Web | Out-Null
    Wait-Backend | Out-Null
    Write-Output 'FONT_SPEED_UPDATE_ROLLED_BACK; controller was not stopped'
    throw $taskFailure
}
