. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\scope-fonts-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\scope-fonts-20261022.tar'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release'}
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch'}
# This work tree is also the deployed release, so the new bytes may already be on
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
 # 1. Backend routing, per-script probabilities, scope errors, byte-identical defaults.
 $taskVerify=& $taskConfig.Python (Join-Path $taskRoot 'deploy\verify_scope_fonts.py')
 if($LASTEXITCODE -ne 0){throw 'Scope font verification failed'}
 if(-not @($taskVerify | Where-Object {$_ -like 'SCOPE_FONTS_OK*'})){throw 'Scope verification produced no result'}
 # 2. The live HTML exposes the per-row scope control and scoped hint text.
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 if($taskHtml -notmatch 'id="fontScope"'){throw 'Add-font scope control missing'}
 if($taskHtml -notmatch '适用范围 · 权重'){throw 'Scoped mix heading missing'}
 $taskApp=(Invoke-WebRequest http://127.0.0.1:8080/app.js -UseBasicParsing -TimeoutSec 5).Content
 if($taskApp -notmatch 'font-scope'){throw 'Per-row scope control missing from app.js'}
 if($taskApp -match 'all''\s*,\s*''zh''\s*,\s*''en'){}else{throw 'Scope list missing from app.js'}
 # 3. A live preview with split scopes returns one font per script.
 $taskFonts=(Invoke-RestMethod http://127.0.0.1:8080/api/fonts -TimeoutSec 30).fonts
 $taskZh=($taskFonts | Where-Object {$_.name -eq '1111122_kvenjoy_1223'} | Select-Object -First 1).id
 $taskEn=($taskFonts | Where-Object {$_.name -eq 'blood8-2_kvenjoy_blood8'} | Select-Object -First 1).id
 if(-not $taskZh -or -not $taskEn){throw 'Verification fonts are missing'}
 $taskBody=@{
  layout_mode='notebook';line_sizing='rows';line_looseness=2;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=5;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  seed='deploy-scope';pages=@(@{text='中文 A'});font_mix=@(
   @{font_id=$taskZh;weight=1;scope='zh'},@{font_id=$taskEn;weight=1;scope='en'})
 } | ConvertTo-Json -Depth 6
 $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskBody)
 $taskPreview=Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBytes -TimeoutSec 120
 $taskScopes=@($taskPreview.metadata.font_mix | ForEach-Object {$_.scope})
 if(($taskScopes -join ',') -ne 'zh,en'){throw ('Live preview did not keep scopes: '+($taskScopes -join ','))}
 # 4. The device side must be untouched by this deployment.
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;
  Scopes=($taskScopes -join ',');Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'SCOPE_FONTS_ROLLED_BACK'
 throw $taskFailure
}
