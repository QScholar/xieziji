$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskPackage=Join-Path $taskRoot 'deploy\shared-workspace-20261025.tar'
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'deploy\shared-workspace-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskXml=[xml](schtasks.exe /Query /TN Xieziji-Web /XML | Out-String)
if($LASTEXITCODE -ne 0){throw 'Cannot inspect backend task.'}
$taskRelease=(Resolve-Path -LiteralPath $taskXml.Task.Actions.Exec.WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release directory.'}
if((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch.'}
if((Get-FileHash -LiteralPath (Join-Path $taskRelease 'backend\watchdog.py') -Algorithm SHA256).Hash -ne $taskManifest.watchdog_sha256){throw 'Watchdog changed.'}
$taskOldFiles=@();$taskAddedFiles=@()
foreach($taskEntry in $taskManifest.files.PSObject.Properties){
    $taskPath=Join-Path $taskRelease $taskEntry.Name
    $taskExpected=$taskManifest.expected.PSObject.Properties[$taskEntry.Name]
    if($taskExpected){
        if((Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash -ne $taskExpected.Value){throw ('Online source changed: '+$taskEntry.Name)}
        $taskOldFiles+=$taskEntry.Name
    }else{
        if(Test-Path -LiteralPath $taskPath){throw ('New file unexpectedly exists: '+$taskEntry.Name)}
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
$taskWorkspaceBefore=Invoke-RestMethod http://127.0.0.1:8080/api/workspaces/COM5 -TimeoutSec 10 | ConvertTo-Json -Depth 50 -Compress
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=Get-Content -LiteralPath (Join-Path $taskRelease 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable.'}
$taskBackup=Join-Path $taskRoot ('deploy\backups\before-shared-workspace-'+(Get-Date -Format yyyyMMddHHmmss))
New-Item -ItemType Directory -Path $taskBackup | Out-Null
tar -cf (Join-Path $taskBackup 'before.tar') -C $taskRelease @taskOldFiles
if($LASTEXITCODE -ne 0){throw 'Backup failed.'}
$taskBefore | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-before.json') -Encoding UTF8
try{
    # Update only the business backend. The controller keeps its PID/connection.
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
    if((Listener-Pid 8090) -ne $taskControllerPid -or $taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Controller continuity check failed.'}
    $taskWorkspace=Invoke-RestMethod http://127.0.0.1:8080/api/workspaces/COM5 -TimeoutSec 10
    if(($taskWorkspace | ConvertTo-Json -Depth 50 -Compress) -ne $taskWorkspaceBefore){throw 'Workspace changed during backend restart.'}
    $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing).Content
    if($taskHtml -notlike '*/workspace-sync.js*'){throw 'Frontend module missing.'}
    $taskModule=(Invoke-WebRequest http://127.0.0.1:8080/workspace-sync.js -UseBasicParsing).Content
    if($taskModule -notlike '*refreshInterface*'){throw 'Frontend module not served.'}
    $taskResult=[pscustomobject]@{Result='UPDATED';Release=$taskRelease;ControllerPid=$taskControllerPid;JobId=$taskAfter.job_id;Phase=$taskAfter.phase;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged;WatchdogPreserved=$true;Backup=$taskBackup}
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
    Write-Output 'SHARED_WORKSPACE_UPDATE_ROLLED_BACK; controller was not stopped'
    throw $taskFailure
}
