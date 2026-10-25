. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\font-preview-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\font-preview-20261021.tar'
$taskRelease=(Resolve-Path -LiteralPath (Get-ScheduledTask -TaskName Xieziji-Web).Actions[0].WorkingDirectory).Path
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash.ToLower() -ne $taskManifest.package_sha256){throw 'Package mismatch'}
foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash.ToLower() -ne $taskEntry.Value){throw ('Live source changed: '+$taskEntry.Name)}
}
function Listener-Pid([int]$taskPort){
 $taskRows=@(netstat -ano -p tcp | Where-Object {$_ -match (':'+$taskPort+'\s+\S+\s+LISTENING\s+\d+\s*$')})
 if($taskRows.Count -ne 1){throw ('Unexpected listener count: '+$taskPort)}
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
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash.ToLower() -ne $taskEntry.Value){throw 'Installed source mismatch'}
 }
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 foreach($taskId in @('fontSort','fontSearchTitle','fontListTitle','fontPreviewPanel','fontRecommendationTitle','fontSingleChar')){
  if($taskHtml -notmatch ('id="'+$taskId+'"')){throw ('Missing chooser element: '+$taskId)}
 }
 if((Listener-Pid 8080) -ne $taskWebPid -or (Listener-Pid 8090) -ne $taskControllerPid){throw 'Service process changed'}
 $taskStatus=Invoke-RestMethod http://127.0.0.1:8080/api/font-status -TimeoutSec 5
 if($taskStatus.error){throw ('Font analysis error: '+$taskStatus.error)}
 [pscustomobject]@{Result='UPDATED';BackendPid=$taskWebPid;ControllerPid=$taskControllerPid;FontCheck=$taskStatus} | ConvertTo-Json -Depth 5 | Out-String | Write-Output
}catch{
 $taskFailure=$_
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 throw $taskFailure
}
