. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\line-pitch-floor-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\line-pitch-floor-20261022.tar'
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
function Line-Pitch([hashtable]$taskExtra){
 # `+` rejects duplicate keys, so overrides are applied one by one.
 $taskBody=@{
  layout_mode='notebook';line_sizing='rows';line_looseness=2;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=5;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  font_id=$script:taskFont;pages=@(@{text='油井Agjy压力不稳'})
 }
 foreach($taskKey in $taskExtra.Keys){$taskBody[$taskKey]=$taskExtra[$taskKey]}
 $taskBody=$taskBody | ConvertTo-Json -Depth 6
 # Invoke-RestMethod would otherwise encode the body as ASCII and mangle Chinese text.
 $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskBody)
 return Invoke-RestMethod http://127.0.0.1:8080/api/line-pitch -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBytes -TimeoutSec 60
}
function Preview-Document([string]$taskText,[string]$taskFont,[bool]$taskOverlap=$false){
 # `size` sizing with a 1× looseness is exactly the request the overlap toggle makes.
 $taskBody=@{
  layout_mode='notebook';line_sizing='size';line_looseness=1;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=5;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  seed='deploy-check';input_format='plain';pages=@(@{text=$taskText});font_mix=@(@{font_id=$taskFont;weight=1})
 }
 if($taskOverlap){$taskBody['allow_overlap']=$true}
 $taskBody=$taskBody | ConvertTo-Json -Depth 6
 $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskBody)
 return Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBytes -TimeoutSec 180
}
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5
if($taskBefore.phase -ne 'complete' -and $taskBefore.phase -ne 'disconnected'){throw ('Refusing to restart during phase '+$taskBefore.phase)}
$taskControllerPid=Listener-Pid 8090
$taskWebPid=Listener-Pid 8080
$taskConfig=[IO.File]::ReadAllText((Join-Path $taskRelease 'deploy\services.json')) | ConvertFrom-Json
if((Get-Process -Id $taskWebPid).Path -ne $taskConfig.Python){throw 'Unexpected backend executable'}
$taskDeployed=@($taskManifest.files.PSObject.Properties.Name)
$null=Assert-CleanSource -Root $taskRoot -Base $taskRelease -Relative $taskDeployed
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
 $taskFonts=(Invoke-RestMethod http://127.0.0.1:8080/api/fonts -TimeoutSec 30).fonts
 $script:taskFont=($taskFonts | Where-Object {$_.name -eq 'blood8-2_kvenjoy_blood8'} | Select-Object -First 1).id
 if(-not $script:taskFont){throw 'Verification font blood8-2_kvenjoy_blood8 is missing'}
 # 1. The endpoint must answer for a plain layout and report a floor above 1.
 $taskPlain=Line-Pitch @{}
 if($taskPlain.min_ratio -lt 1 -or $taskPlain.min_ratio -gt 4){throw ('Unexpected floor '+$taskPlain.min_ratio)}
 if(-not $taskPlain.feasible){throw 'Plain layout reported infeasible'}
 if($taskPlain.pitch_mm -le 0){throw 'Missing predicted pitch'}
 # 2. Turning on humanization must raise the floor, because rotation and jitter need room.
 $taskHuman=Line-Pitch @{humanization=@{enabled=$true;vertical=2;scale=20;rotation=10;spacing=2;baseline=2}}
 if($taskHuman.min_ratio -le $taskPlain.min_ratio){throw ('Humanization did not raise the floor: '+$taskPlain.min_ratio+' -> '+$taskHuman.min_ratio)}
 # 3. Fewer glyph shapes must not need more room than the full sample.
 $taskChinese=Line-Pitch @{pages=@(@{text='中文测试'})}
 if($taskChinese.min_ratio -gt $taskPlain.min_ratio){throw ('Plain Chinese needed more room than mixed text: '+$taskChinese.min_ratio+' > '+$taskPlain.min_ratio)}
 # 4. Grid mode has no looseness slider, so it must report a floor of 1 and no pitch change.
 $taskGrid=Line-Pitch @{layout_mode='grid';grid=@{columns=20;row_gap=2.9;column_gap=0;size_percent=80;padding=.3;draw_lines=$false}}
 if($taskGrid.min_ratio -ne 1){throw ('Grid floor should stay 1, got '+$taskGrid.min_ratio)}
 # 5. Overlap mode releases the floor but still reports the measured one.
 $taskOverlap=Line-Pitch @{allow_overlap=$true}
 if($taskOverlap.min_ratio -ne 1){throw ('Overlap floor should be 1, got '+$taskOverlap.min_ratio)}
 if(-not $taskOverlap.allow_overlap -or -not $taskOverlap.overlap){throw 'Overlap flag not echoed'}
 if([math]::Abs($taskOverlap.ratio-$taskPlain.ratio) -gt .0001){throw ('Overlap changed the measured floor: '+$taskPlain.ratio+' -> '+$taskOverlap.ratio)}
 # 6. Overlap preview keeps the requested pitch and height, and records the flag.
 $taskOverlapText=[string]::Join([char]10, @('油井Agjy压力不稳','第二行油井Agjy'))
 $taskNormalPreview=Preview-Document $taskOverlapText $script:taskFont $false
 $taskOverlapPreview=Preview-Document $taskOverlapText $script:taskFont $true
 if(-not $taskOverlapPreview.pages[0].metadata.allow_overlap){throw 'Preview metadata lost allow_overlap'}
 if($taskNormalPreview.pages[0].metadata.allow_overlap){throw 'Normal preview reported allow_overlap'}
 if([math]::Abs($taskOverlapPreview.pages[0].metadata.pitch-5) -gt .001){throw ('Overlap changed the pitch to '+$taskOverlapPreview.pages[0].metadata.pitch)}
 if([math]::Abs($taskOverlapPreview.pages[0].metadata.size-5) -gt .001){throw ('Overlap shrank the glyphs to '+$taskOverlapPreview.pages[0].metadata.size)}
 # Both lines keep their 5 mm pitch, so the ink runs deeper than the same page without overlap.
 if($taskOverlapPreview.pages[0].metadata.bounds[3] -le $taskNormalPreview.pages[0].metadata.bounds[3]){throw 'Overlap did not let the lines share ink'}
 # 7. The prediction is read-only: it must not create a job or touch the device.
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 # 8. The slider markup, the overlap toggle and the predictor must all be live.
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 if($taskHtml -notmatch 'id="lineLooseness"'){throw 'Looseness slider missing'}
 if($taskHtml -notmatch 'id="allowOverlap"'){throw 'Overlap toggle missing'}
 $taskApp=(Invoke-WebRequest http://127.0.0.1:8080/app.js -UseBasicParsing -TimeoutSec 5)
 if($taskApp.Content -notmatch '/api/line-pitch'){throw 'Frontend does not request the prediction'}
 if($taskApp.Content -notmatch "lineLooseness'\)\.min"){throw 'Frontend does not apply the predicted floor'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;
  PlainFloor=$taskPlain.min_ratio;HumanFloor=$taskHuman.min_ratio;ChineseFloor=$taskChinese.min_ratio;
  OverlapFloor=$taskOverlap.min_ratio;OverlapPitch=$taskOverlapPreview.pages[0].metadata.pitch;Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Base $taskRelease -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'LINE_PITCH_FLOOR_ROLLED_BACK'
 throw $taskFailure
}
