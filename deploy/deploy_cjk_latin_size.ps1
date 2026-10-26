. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\cjk-latin-size-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\cjk-latin-size-20261022.tar'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if(-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected release'}
if($taskRelease -ne $taskManifest.release){throw 'Release changed'}
if((Get-FileHash -LiteralPath $taskPackage).Hash -ne $taskManifest.package_sha256){throw 'Package hash mismatch'}
foreach($taskEntry in $taskManifest.expected.PSObject.Properties){
 if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw ('Live source changed: '+$taskEntry.Name)}
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
function Preview-Check([double]$taskLatin,[string]$taskText,[string]$taskFont){
 $taskBody=@{
  layout_mode='notebook';line_sizing='rows';line_looseness=2.37;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=$taskLatin;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  seed='deploy-check';font_id=$taskFont;pages=@(@{text=$taskText})
 } | ConvertTo-Json -Depth 5
 # Invoke-RestMethod would otherwise encode the body as ASCII and mangle Chinese text.
 $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskBody)
 return Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBytes -TimeoutSec 60
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy\services.json')) | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskDeployed=@($taskManifest.files.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try{
 # Only the gateway restarts. The C++ worker and device connection are untouched.
 Stop-ScheduledTask -TaskName 'Xieziji-Web'
 Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
 tar -xf $taskPackage -C $taskRelease
 if($LASTEXITCODE -ne 0){throw 'Extraction failed'}
 foreach($taskEntry in $taskManifest.files.PSObject.Properties){
  if((Get-FileHash -LiteralPath (Join-Path $taskRelease $taskEntry.Name)).Hash -ne $taskEntry.Value){throw 'Installed source mismatch'}
 }
 & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
 if($LASTEXITCODE -ne 0){throw 'Compilation failed'}
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 $taskAfter=Wait-Backend
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 if($taskHtml -notmatch 'id="latinSize"'){throw 'English height control missing'}
 $taskHelper=(Invoke-WebRequest http://127.0.0.1:8080/notebook-sizing.js -UseBasicParsing -TimeoutSec 5)
 if($taskHelper.Headers['Content-Type'] -notmatch 'javascript'){throw 'Sizing helper MIME mismatch'}
 if($taskHelper.Content -notmatch 'latinSize'){throw 'Sizing helper not updated'}
 # Same seed and text: only the English height differs, so the Latin line must grow and Chinese must not.
 $taskFonts=(Invoke-RestMethod http://127.0.0.1:8080/api/fonts -TimeoutSec 30).fonts
 $taskFont=($taskFonts | Where-Object {$_.name -eq '1111122_kvenjoy_1223'} | Select-Object -First 1).id
 if(-not $taskFont){throw 'Verification font 1111122_kvenjoy_1223 is missing'}
 $taskChinese=-join ([int[]](0x4E2D,0x6587,0x6D4B,0x8BD5) | ForEach-Object {[char]$_})
 $taskSmall=Preview-Check 5 ("$taskChinese`nABC abc") $taskFont
 $taskLarge=Preview-Check 9 ("$taskChinese`nABC abc") $taskFont
 $taskCjkSmall=Preview-Check 5 $taskChinese $taskFont
 $taskCjkLarge=Preview-Check 9 $taskChinese $taskFont
 function Get-BoundsKey($taskMetadata){
  return [string]::Join(',', @($taskMetadata.machine_bounds | ForEach-Object {([double]$_).ToString('F3',[Globalization.CultureInfo]::InvariantCulture)}))
 }
 $taskCjkKey=Get-BoundsKey $taskCjkSmall.metadata
 if((Get-BoundsKey $taskCjkLarge.metadata) -ne $taskCjkKey){throw ('Chinese glyphs changed with the English height: '+$taskCjkKey+' -> '+(Get-BoundsKey $taskCjkLarge.metadata))}
 if([math]::Abs($taskSmall.metadata.size-5) -gt .001){throw ('Unexpected Chinese height '+$taskSmall.metadata.size)}
 if([math]::Abs($taskSmall.metadata.latin_size-5) -gt .001){throw ('Default English height changed '+$taskSmall.metadata.latin_size)}
 if([math]::Abs($taskLarge.metadata.size-5) -gt .001){throw ('Chinese height moved with English '+$taskLarge.metadata.size)}
 if([math]::Abs($taskLarge.metadata.latin_size-9) -gt .001){throw ('English height not applied '+$taskLarge.metadata.latin_size)}
 if($taskLarge.metadata.machine_bounds[3] -le $taskSmall.metadata.machine_bounds[3]){throw 'Larger English height did not extend the page'}
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;ChineseHeight=$taskSmall.metadata.size;EnglishHeight=$taskLarge.metadata.latin_size;Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'CJK_LATIN_SIZE_UPDATE_ROLLED_BACK'
 throw $taskFailure
}
