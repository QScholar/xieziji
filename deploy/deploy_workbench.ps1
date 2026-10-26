. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskDeploy=Join-Path $taskRoot 'deploy'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskDeploy 'workbench-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskDeploy 'workbench-20261021.tar'
$taskRelease=(Resolve-Path -LiteralPath (Get-ScheduledTask -TaskName Xieziji-Web).Actions[0].WorkingDirectory).Path
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package mismatch'}
foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw ('Live file changed: '+$taskEntry.Name)}
}
if(Test-Path -LiteralPath (Join-Path $taskRelease 'frontend/workbench.css')){throw 'Theme already exists; inspect before updating'}
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
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy/services.json')) | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskDeployed=@($taskManifest.expected.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
$taskStage=Join-Path $taskDeploy ('workbench-stage-'+(Get-Date -Format yyyyMMddHHmmss))
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if($LASTEXITCODE -ne 0){throw 'Staging failed'}
foreach($taskEntry in $taskManifest.files.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskStage $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Staged file mismatch'}
}
& $taskConfig.Python -m py_compile (Join-Path $taskStage 'backend/server.py')
if($LASTEXITCODE -ne 0){throw 'Python compile failed'}
try{
 Stop-ScheduledTask -TaskName 'Xieziji-Web'
 Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){Copy-Item -LiteralPath (Join-Path $taskStage $taskEntry.Name) -Destination (Join-Path $taskRelease $taskEntry.Name) -Force}
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 $taskAfter=Wait-Backend
 $taskCss=Invoke-WebRequest http://127.0.0.1:8080/workbench.css -UseBasicParsing -TimeoutSec 5
 if($taskCss.Headers['Content-Type'] -notmatch 'text/css' -or $taskCss.Content -notmatch '--wood: #8b603e'){throw 'Theme serving failed'}
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 foreach($taskMarker in @('/workbench.css','id="workbenchTitle"','id="compose"','id="output"','id="device"')){if(-not $taskHtml.Contains($taskMarker)){throw ('Page marker missing: '+$taskMarker)}}
 if($taskHtml.Contains([string]([char]0x7eb8)+[char]0x95f4)){throw 'Old name remains'}
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Installed file mismatch'}}
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';Release=$taskRelease;BackendPid=(Listener-Pid 8080);ControllerPid=$taskControllerPid;Phase=$taskAfter.phase;JobId=$taskAfter.job_id;Connected=$taskAfter.connected;Acknowledged=$taskAfter.acknowledged;Files=$taskManifest.files}|ConvertTo-Json -Depth 5 | Out-String | Write-Output
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 $taskTheme=Join-Path $taskRelease 'frontend/workbench.css'
 if(Test-Path -LiteralPath $taskTheme){Remove-Item -LiteralPath $taskTheme}
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'WORKBENCH_UPDATE_ROLLED_BACK'
 throw $taskFailure
}
