$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskDeploy=Join-Path $taskRoot 'deploy'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskDeploy 'blank-lines-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskDeploy 'blank-lines-20261024.tar'
$taskXml=[xml](schtasks.exe /Query /TN Xieziji-Web /XML | Out-String)
$taskRelease=(Resolve-Path -LiteralPath $taskXml.Task.Actions.Exec.WorkingDirectory).Path
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch'}
foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw ('Live file changed: '+$taskEntry.Name)}
}
function Listener-Pid([int]$taskPort){
 $taskRows=@(netstat -ano -p tcp | Where-Object {$_ -match (':'+$taskPort+'\s+\S+\s+LISTENING\s+\d+\s*$')})
 if($taskRows.Count -ne 1){throw ('Unexpected listener count on '+$taskPort)}
 [int](($taskRows[0].Trim() -split '\s+')[-1])
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
$taskWebPid=Listener-Pid 8080
$taskControllerPid=Listener-Pid 8090
$taskBackup=Join-Path $taskDeploy ('backups\before-blank-lines-'+(Get-Date -Format yyyyMMddHHmmss))
New-Item -ItemType Directory -Path $taskBackup | Out-Null
$taskOldFiles=@($taskManifest.expected.PSObject.Properties.Name)
tar -cf (Join-Path $taskBackup 'before.tar') -C $taskRelease @taskOldFiles
if($LASTEXITCODE -ne 0){throw 'Backup failed'}
try{
 tar -xf $taskPackage -C $taskRelease
 if($LASTEXITCODE -ne 0){throw 'Extraction failed'}
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Installed file mismatch'}
 }
 foreach($taskPath in @('/style.css','/workbench.css')){
  $taskResponse=Invoke-WebRequest ('http://127.0.0.1:8080'+$taskPath) -UseBasicParsing -TimeoutSec 5
  if($taskResponse.Headers['Content-Type'] -notmatch 'text/css'){throw 'CSS serving failed'}
 }
 if((Listener-Pid 8080) -ne $taskWebPid -or (Listener-Pid 8090) -ne $taskControllerPid){throw 'Service process changed'}
 $taskAfter=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task state changed unexpectedly'}
 [pscustomobject]@{Phase=$taskAfter.phase;Job=$taskAfter.job_id;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged;Result='UPDATED';Backup=$taskBackup;BackendPid=$taskWebPid;ControllerPid=$taskControllerPid;Files=$taskManifest.files}|ConvertTo-Json -Depth 5 | Tee-Object -FilePath (Join-Path $taskBackup 'result.json')
}catch{
 $taskFailure=$_
 tar -xf (Join-Path $taskBackup 'before.tar') -C $taskRelease
 if($LASTEXITCODE -ne 0){throw 'Rollback failed'}
 throw $taskFailure
}
