. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\repeat-start-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\repeat-start-20261022.tar'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release'}
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch'}
# This work tree is also the deployed release, so the new bytes are already on
# disk. Accept only the recorded pre-deploy or post-deploy content, never a mix.
foreach($taskEntry in $taskManifest.files.PSObject.Properties){
 $taskHash=(Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash
 $taskOld=$taskManifest.expected.PSObject.Properties[$taskEntry.Name].Value
 if($taskHash -ne $taskEntry.Value -and $taskHash -ne $taskOld){throw ('Unexpected live source: '+$taskEntry.Name)}
}
function Listener-Pid([int]$taskPort){
 $taskRows=@(netstat -ano -p tcp | Where-Object {$_ -match (':'+$taskPort+'\s+\S+\s+LISTENING\s+\d+\s*$')})
 if($taskRows.Count -ne 1){throw ('Unexpected listener count on '+$taskPort)}
 [int](($taskRows[0].Trim() -split '\s+')[-1])
}
function Wait-Backend {
 $taskDeadline=(Get-Date).AddSeconds(30)
 do{try{return Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 2}catch{Start-Sleep -Milliseconds 300}}while((Get-Date)-lt $taskDeadline)
 throw 'Backend did not start'
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
if($taskBefore.phase -ne 'complete' -and $taskBefore.phase -ne 'disconnected' -and $taskBefore.phase -ne 'idle'){throw ('Refusing to restart during phase '+$taskBefore.phase)}
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy\services.json')) | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskDeployed=@($taskManifest.files.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{
 # Only the gateway restarts. The C++ worker and the device connection are untouched.
 Stop-ScheduledTask -TaskName 'Xieziji-Web'
 Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
 if($taskManifest.files.PSObject.Properties | Where-Object {(Get-FileHash -LiteralPath (Join-Path $taskRelease $_.Name)).Hash -ne $_.Value}){
  tar -xf $taskPackage -C $taskRelease
  if($LASTEXITCODE -ne 0){throw 'Extraction failed'}
 }
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Installed source mismatch'}
 }
 & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
 if($LASTEXITCODE -ne 0){throw 'Compilation failed'}
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 $taskAfter=Wait-Backend
 # 1. The business layer mints a new execution id per start and maps it back to the preview.
 $taskVerify=& $taskConfig.Python (Join-Path $taskRoot 'deploy\verify_repeat_start.py')
 if($LASTEXITCODE -ne 0){throw 'Repeat-start verification failed'}
 $taskVerifyLine=@($taskVerify | Where-Object {$_ -like 'REPEAT_START_OK*'})[0]
 if(-not $taskVerifyLine){throw 'Repeat-start verification produced no result'}
 # 2. A writable page keeps a job the browser can start, and the hint no longer forbids rewriting.
 $taskStart=Invoke-WebRequest http://127.0.0.1:8080/app.js -UseBasicParsing -TimeoutSec 5
 if($taskStart.Content -notmatch "currentJob=m\.empty\?null:page\.job_id"){throw 'Start gate was not lifted'}
 if($taskStart.Content -match 'startedJobs'){throw 'Stale started-task gate remains'}
 $taskHint=Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5
 if($taskHint.Content -match '旧任务不能续写'){throw 'Stale hint text remains'}
 if($taskHint.Content -notmatch '可重复写入|随时可重新确认原点'){throw 'Rewrite hint missing'}
 # 3. The device side must be untouched by this deployment.
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;
  Verify=$taskVerifyLine;Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'REPEAT_START_ROLLED_BACK'
 throw $taskFailure
}
