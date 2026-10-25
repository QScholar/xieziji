. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskDeploy=Join-Path $taskRoot 'deploy'
$taskPackage=Join-Path $taskDeploy 'device-profiles-20261021.tar'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskDeploy 'device-profiles-manifest.json')) | ConvertFrom-Json
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskController=Get-ScheduledTask -TaskName 'Xieziji-Controller'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
$taskControllerRelease=(Resolve-Path -LiteralPath $taskController.Actions[0].WorkingDirectory).Path
foreach($taskPath in @($taskRelease,$taskControllerRelease)){
 if(-not $taskPath.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release directory'}
}
$taskBinary=Join-Path $taskControllerRelease 'controller\bin\plotter-controller.exe'
if((Get-FileHash -LiteralPath $taskBinary).Hash -ne $taskManifest.controller_before){throw 'Controller binary changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch'}
foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw ('Live file changed: '+$taskEntry.Name)}
}
$taskStamp=Get-Date -Format yyyyMMddHHmmss
$taskStage=Join-Path $taskDeploy ('device-profiles-stage-'+$taskStamp)
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if($LASTEXITCODE -ne 0){throw 'Package extraction failed'}
foreach($taskEntry in $taskManifest.files.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskStage $taskEntry.Name)).Hash -ne $taskEntry.Value){throw ('Staged file mismatch: '+$taskEntry.Name)}
}
if((Get-FileHash -LiteralPath (Join-Path $taskStage 'controller-update.exe')).Hash -ne $taskManifest.controller_after){throw 'Staged binary mismatch'}
$taskDeployed=@($taskManifest.files.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
# Source files roll back through git. The controller binary is a build product
# with no tracked sources in this release, so keep exactly that one file aside.
$taskBinaryBefore=$taskManifest.controller_before
$taskBackup=Join-Path $taskDeploy ('backups\before-device-profiles-'+$taskStamp)
New-Item -ItemType Directory -Path $taskBackup | Out-Null
Copy-Item -LiteralPath $taskBinary -Destination (Join-Path $taskBackup 'controller-before.exe')
if((Get-FileHash -LiteralPath (Join-Path $taskBackup 'controller-before.exe')).Hash -ne $taskBinaryBefore){throw 'Controller backup mismatch'}
function Listener-Pid([int]$taskPort){
 $taskRows=@(netstat -ano -p tcp | Where-Object {$_ -match (':'+$taskPort+'\s+\S+\s+LISTENING\s+\d+\s*$')})
 if($taskRows.Count -ne 1){throw ('Unexpected listener count on '+$taskPort)}
 [int](($taskRows[0].Trim() -split '\s+')[-1])
}
function Wait-Service([string]$taskUrl){
 $taskDeadline=(Get-Date).AddSeconds(30)
 do{try{return Invoke-RestMethod $taskUrl -TimeoutSec 2}catch{Start-Sleep -Milliseconds 300}}while((Get-Date)-lt $taskDeadline)
 throw ('Service did not start: '+$taskUrl)
}
function Assert-Idle($taskState){
 if($taskState.phase -notin @('idle','complete','stopped','disconnected')){throw ('Device has an active operation: '+$taskState.phase)}
 if($taskState.connected -and $taskState.status -notmatch '^<Idle'){throw 'Physical machine is not Idle'}
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
Assert-Idle $taskBefore
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
if((Get-Process -Id $taskControllerPid).Path -ne $taskBinary){throw 'Unexpected controller executable'}
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy\services.json')) | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskFlag=Join-Path $taskConfig.BackendJobs '.speed-limit-update-pending'
if(Test-Path -LiteralPath $taskFlag){throw 'Another controller update is pending'}
$taskControllerStopped=$false
$taskFilesChanged=$false
try{
 'device-profiles' | Set-Content -LiteralPath $taskFlag -Encoding ASCII
 Assert-Idle (Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5)
 Stop-ScheduledTask -TaskName 'Xieziji-Web'
 Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
 # With the gateway stopped, a new browser operation cannot race this check.
 Assert-Idle (Invoke-RestMethod http://127.0.0.1:8090/v1/state -TimeoutSec 5)
 Stop-ScheduledTask -TaskName 'Xieziji-Controller'
 Stop-Process -Id $taskControllerPid -Force -ErrorAction SilentlyContinue
 $taskControllerStopped=$true
 $taskFilesChanged=$true
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  Copy-Item -LiteralPath (Join-Path $taskStage $taskEntry.Name) -Destination (Join-Path $taskRelease $taskEntry.Name) -Force
 }
 Copy-Item -LiteralPath (Join-Path $taskStage 'controller-update.exe') -Destination $taskBinary -Force
 & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
 if($LASTEXITCODE -ne 0){throw 'Python compilation failed'}
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Installed file mismatch'}
 }
 Start-ScheduledTask -TaskName 'Xieziji-Controller'
 $taskControllerAfter=Wait-Service 'http://127.0.0.1:8090/v1/state'
 if($taskControllerAfter.connected -or $taskControllerAfter.phase -ne 'disconnected'){throw 'Unexpected automatic connection or motion'}
 Remove-Item -LiteralPath $taskFlag -Force
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 $taskAfter=Wait-Service 'http://127.0.0.1:8080/api/state'
 $taskProfiles=Invoke-RestMethod http://127.0.0.1:8080/api/device-profiles -TimeoutSec 5
 if($null -eq $taskProfiles.profiles){throw 'Profiles endpoint missing'}
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 if($taskHtml -notmatch 'id="deviceName"' -or $taskHtml -notmatch 'id="penUpZ"' -or $taskHtml -notmatch 'id="controllerType"'){throw 'Updated device controls missing'}
 if((Get-FileHash -LiteralPath $taskBinary).Hash -ne $taskManifest.controller_after){throw 'Installed binary mismatch'}
 [pscustomobject]@{Result='UPDATED';Phase=$taskAfter.phase;Connected=$taskAfter.connected;PreviousPort=$taskBefore.port;ControllerPid=(Listener-Pid 8090);BackendPid=(Listener-Pid 8080)} | ConvertTo-Json | Out-String | Write-Output
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 if($taskControllerStopped){
  Stop-ScheduledTask -TaskName 'Xieziji-Controller' -ErrorAction SilentlyContinue
  try{$taskCurrentController=Listener-Pid 8090;Stop-Process -Id $taskCurrentController -Force -ErrorAction SilentlyContinue}catch{}
 }
 if($taskFilesChanged){
  Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
  Copy-Item -LiteralPath (Join-Path $taskBackup 'controller-before.exe') -Destination $taskBinary -Force
  if((Get-FileHash -LiteralPath $taskBinary).Hash -ne $taskBinaryBefore){throw 'Rolled-back controller binary mismatch'}
 }
 if($taskControllerStopped){Start-ScheduledTask -TaskName 'Xieziji-Controller'}
 Remove-Item -LiteralPath $taskFlag -Force -ErrorAction SilentlyContinue
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Write-Output 'DEVICE_PROFILES_UPDATE_ROLLED_BACK'
 throw $taskFailure
}
