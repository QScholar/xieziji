. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskDeploy=Join-Path $taskRoot 'deploy'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskDeploy 'workbench-type-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskDeploy 'workbench-type-20261021.tar'
$taskRelease=(Resolve-Path -LiteralPath (Get-ScheduledTask -TaskName Xieziji-Web).Actions[0].WorkingDirectory).Path
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
$taskWebPid=Listener-Pid 8080
$taskControllerPid=Listener-Pid 8090
$taskDeployed=@($taskManifest.expected.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
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
 [pscustomobject]@{Result='UPDATED';BackendPid=$taskWebPid;ControllerPid=$taskControllerPid;Files=$taskManifest.files}|ConvertTo-Json -Depth 5 | Out-String | Write-Output
}catch{
 $taskFailure=$_
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 throw $taskFailure
}
