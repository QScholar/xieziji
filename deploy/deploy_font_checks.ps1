. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\font-checks-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\font-checks-20261021.tar'
$taskRelease=(Resolve-Path -LiteralPath (Get-ScheduledTask -TaskName Xieziji-Web).Actions[0].WorkingDirectory).Path
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash.ToLower() -ne $taskManifest.package_sha256){throw 'Package mismatch'}
function Assert-Baseline {
 foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
  $taskPath=Join-Path $taskRelease $taskEntry.Name
  if($taskEntry.Value){
   if((Get-FileHash -LiteralPath $taskPath).Hash.ToLower() -ne $taskEntry.Value){throw ('Live source changed: '+$taskEntry.Name)}
  }elseif(Test-Path -LiteralPath $taskPath){throw ('Unexpected added file: '+$taskEntry.Name)}
 }
}
Assert-Baseline
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy\services.json')) | ConvertFrom-Json
$taskStage=Join-Path $taskRoot ('deploy\font-checks-stage-'+(Get-Date -Format yyyyMMddHHmmss))
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if($LASTEXITCODE -ne 0){throw 'Staging extraction failed'}
& $taskConfig.Python -m pip install --no-index --find-links (Join-Path $taskRoot 'deploy') -r (Join-Path $taskStage 'requirements-analysis.txt') --disable-pip-version-check
if($LASTEXITCODE -ne 0){throw 'Analysis dependencies installation failed'}
& $taskConfig.Python -c 'import numpy,PIL; print(numpy.__version__,PIL.__version__)'
if($LASTEXITCODE -ne 0){throw 'Analysis dependencies import failed'}
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
Assert-Baseline
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskDeployed=@($taskManifest.expected.PSObject.Properties | Where-Object {$_.Value} | ForEach-Object {$_.Name})
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{
 Stop-ScheduledTask -TaskName Xieziji-Web
 Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
 tar -xf $taskPackage -C $taskRelease
 if($LASTEXITCODE -ne 0){throw 'Extraction failed'}
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash.ToLower() -ne $taskEntry.Value){throw 'Installed source mismatch'}
 }
 & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend') (Join-Path $taskRelease 'scripts')
 if($LASTEXITCODE -ne 0){throw 'Compilation failed'}
 Start-ScheduledTask -TaskName Xieziji-Web
 $taskAfter=Wait-Backend
 $taskStatus=Invoke-RestMethod http://127.0.0.1:8080/api/font-status -TimeoutSec 5
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;FontCheck=$taskStatus} | ConvertTo-Json -Depth 5 | Out-String | Write-Output
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName Xieziji-Web -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 foreach($taskEntry in $taskManifest.expected.PSObject.Properties | Where-Object {-not $_.Value}){
  $taskRemove=[IO.Path]::GetFullPath((Join-Path $taskRelease $taskEntry.Name))
  if(-not $taskRemove.StartsWith($taskRelease+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe rollback file path'}
  Remove-Item -LiteralPath $taskRemove -Force -ErrorAction SilentlyContinue
 }
 Start-ScheduledTask -TaskName Xieziji-Web
 Wait-Backend | Out-Null
 throw $taskFailure
}
