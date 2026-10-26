. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\preview-zoom-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\preview-zoom-20261022.tar'
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
$null=Assert-CleanSource -Root $taskRoot -Base $taskRelease -Relative $taskDeployed
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
 # 1. The live HTML must carry the zoom dialog, its controls and the click hint.
 $taskHtml=(Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing -TimeoutSec 5).Content
 foreach($taskMark in @('id="zoomPreview"','id="previewZoom"','id="zoomStage"','id="zoomCanvas"','id="zoomFit"','id="closePreviewZoom"','id="zoomLevel"')) {
  if($taskHtml -notmatch [regex]::Escape($taskMark)){throw ('Missing markup: '+$taskMark)}
 }
 if($taskHtml -notmatch '点击纸张或“放大预览”可放大查看笔画'){throw 'Zoom hint missing'}
 # 2. The served script must implement open, wheel zoom, pan, fit and keyboard reset.
 $taskApp=(Invoke-WebRequest http://127.0.0.1:8080/app.js -UseBasicParsing -TimeoutSec 5).Content
 foreach($taskSnippet in @('function previewZoomOpen','zoomAt(event.clientX,event.clientY','zoomBox.pinch','zoomLayout(false)','addEventListener(''click'',()=>{if(zoomSvg())previewZoomOpen();})')) {
  if($taskApp -notmatch [regex]::Escape($taskSnippet)){throw ('Missing behaviour: '+$taskSnippet)}
 }
 # The zoom viewer must stay request-free: its code sits between these markers.
 $taskStart=$taskApp.IndexOf('function previewZoomOpen')
 $taskEnd=$taskApp.IndexOf('function allowOverlap')
 if($taskStart -lt 0 -or $taskEnd -le $taskStart){throw 'Zoom block not found'}
 if($taskApp.Substring($taskStart,$taskEnd-$taskStart) -match '\bapi\(|fetch\('){throw 'Zoom must not issue a request'}
 # 3. The zoom rules must be served by both stylesheets.
 $taskCss=(Invoke-WebRequest http://127.0.0.1:8080/style.css -UseBasicParsing -TimeoutSec 5).Content
 $taskTheme=(Invoke-WebRequest http://127.0.0.1:8080/workbench.css -UseBasicParsing -TimeoutSec 5).Content
 if($taskCss -notmatch '\.preview-zoom-stage'){throw 'Zoom stage style missing'}
 if($taskTheme -notmatch '\.preview-zoom\b'){throw 'Zoom theme missing'}
 # 4. Path generation is untouched: the same fixed-seed request returns a byte-identical SVG.
 $taskFonts=(Invoke-RestMethod http://127.0.0.1:8080/api/fonts -TimeoutSec 30).fonts
 $taskFont=($taskFonts | Where-Object {$_.name -eq 'blood8-2_kvenjoy_blood8'} | Select-Object -First 1).id
 if(-not $taskFont){throw 'Verification font is missing'}
 $taskBody=@{
  layout_mode='notebook';line_sizing='rows';line_looseness=2;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=5;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  seed='deploy-zoom';input_format='plain';pages=@(@{text='Zoom check 视图预览'});font_mix=@(@{font_id=$taskFont;weight=1})
 } | ConvertTo-Json -Depth 6
 $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskBody)
 $taskPreview=Invoke-RestMethod http://127.0.0.1:8080/api/preview -Method Post -ContentType 'application/json; charset=utf-8' -Body $taskBytes -TimeoutSec 120
 if(-not $taskPreview.pages[0].svg){throw 'Preview returned no SVG'}
 if($taskPreview.pages[0].metadata.points -le 0){throw 'Preview returned no path points'}
 # 5. The device side must be untouched by this deployment.
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;
  SvgBytes=$taskPreview.pages[0].svg.Length;Points=$taskPreview.pages[0].metadata.points;Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Base $taskRelease -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'PREVIEW_ZOOM_ROLLED_BACK'
 throw $taskFailure
}
