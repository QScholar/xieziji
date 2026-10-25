
. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskRelease=(Resolve-Path -LiteralPath (Get-ScheduledTask -TaskName 'Xieziji-Web').Actions[0].WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release'}
$taskPackage=Join-Path $taskRoot 'deploy\fold-ui-font-dialog-20261020.tar'
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne 'AA2D2BC24A4CBE24D0B606575FA4E91EBAECDD0674C0C4E96D0516E2C6EDE902'){throw 'Package mismatch'}
$taskStamp=Get-Date -Format yyyyMMddHHmmss
$taskStage=Join-Path $taskRoot ('deploy\fold-ui-stage-'+$taskStamp)
New-Item -ItemType Directory -Path $taskStage | Out-Null
tar -xf $taskPackage -C $taskStage
if($LASTEXITCODE -ne 0){throw 'Extraction failed'}
$taskManifest=Get-Content -LiteralPath (Join-Path $taskStage 'manifest.json') -Raw | ConvertFrom-Json
$taskFiles=@('frontend/index.html','frontend/style.css','frontend/app.js')
foreach($taskFile in $taskFiles){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskFile)).Hash -ne $taskManifest.$taskFile.before){throw ('Live file changed: '+$taskFile)}
  if((Get-FileHash -LiteralPath (Join-Path $taskStage $taskFile)).Hash -ne $taskManifest.$taskFile.after){throw ('Payload mismatch: '+$taskFile)}
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state
$taskControllerPid=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
$taskBackendPid=(Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object -First 1).OwningProcess
$taskDeployed=@($taskFiles)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{
  foreach($taskFile in $taskFiles){Copy-Item -LiteralPath (Join-Path $taskStage $taskFile) -Destination (Join-Path $taskRelease $taskFile) -Force}
  foreach($taskFile in $taskFiles){
    $taskUrl=if($taskFile -eq 'frontend/index.html'){'/'}else{'/'+(Split-Path -Leaf $taskFile)}
    $taskClient=New-Object System.Net.WebClient
    try{$taskBytes=$taskClient.DownloadData('http://127.0.0.1:8080'+$taskUrl)}finally{$taskClient.Dispose()}
    $taskHash=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($taskBytes)).Replace('-','')
    if($taskHash -ne $taskManifest.$taskFile.after){throw ('HTTP verification failed: '+$taskFile)}
  }
  $taskAfter=Invoke-RestMethod http://127.0.0.1:8080/api/state
  if((Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess -ne $taskControllerPid){throw 'Controller PID changed'}
  if((Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object -First 1).OwningProcess -ne $taskBackendPid){throw 'Backend PID changed'}
  if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity failed'}
  [pscustomobject]@{Result='UPDATED_STATIC_ONLY';BackendPid=$taskBackendPid;ControllerPid=$taskControllerPid;Phase=$taskAfter.phase}|ConvertTo-Json
}catch{
  Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
  Write-Output 'STATIC_FILES_ROLLED_BACK'
  throw
}
