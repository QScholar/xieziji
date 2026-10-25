. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskManifest=[IO.File]::ReadAllText((Join-Path $taskRoot 'deploy\markdown-formula-fix-manifest.json')) | ConvertFrom-Json
$taskPackage=Join-Path $taskRoot 'deploy\markdown-formula-fix-20261022.tar'
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
function Preview-Document([string]$taskText,[string]$taskFont){
 $taskBody=@{
  layout_mode='notebook';line_sizing='rows';line_looseness=2;pagination='auto'
  width=210;height=297;margin_top=30;margin_bottom=30;margin_left=30;margin_right=30
  rows=20;size=5;latin_size=5;punctuation_width=.8;spacing=.9;feed=1800;move_feed=3000
  seed='deploy-check';input_format='markdown';pages=@(@{text=$taskText});font_mix=@(@{font_id=$taskFont;weight=1})
 } | ConvertTo-Json -Depth 6
 # Invoke-RestMethod would otherwise encode the body as ASCII and mangle Chinese text.
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
 $taskFonts=(Invoke-RestMethod http://127.0.0.1:8080/api/fonts -TimeoutSec 30).fonts
 $taskFont=($taskFonts | Where-Object {$_.name -eq 'blood8-2_kvenjoy_blood8'} | Select-Object -First 1).id
 if(-not $taskFont){throw 'Verification font blood8-2_kvenjoy_blood8 is missing'}
 # 1. An inline formula used to raise "ord() expected a character" for every Markdown document.
 $taskInline=Preview-Document ('设 $x^2 + \frac{1}{2} = \sqrt{4}$ 成立') $taskFont
 if($taskInline.page_count -ne 1 -or $taskInline.metadata.formula_count -lt 1){throw 'Inline formula preview did not use the formula engine'}
 # 2. Constructs that only the full document uses: macros, invisible delimiters, HTML scripts, superscripts.
 $taskConstruct=Preview-Document ('$a \equiv b$ 与 $\varphi\left\lbrack x \right\rbrack$、$- \left. f \right.$、r<sub>D</sub>、10⁻⁶、a &lt; b') $taskFont
 if($taskConstruct.page_count -ne 1){throw 'Formula construct preview failed'}
 # 3. The two source documents in tempfile must lay out end to end.
 $taskFullPreview=Preview-Document ([IO.File]::ReadAllText((Join-Path $taskRoot 'tempfile\油井压力不稳定试井翻译.md'))) $taskFont
 if($taskFullPreview.page_count -ne 14){throw ('Full document produced '+$taskFullPreview.page_count+' pages, expected 14')}
 if($taskFullPreview.pages[13].metadata.formula_count -lt 1){throw 'Full document lost its formulae'}
 $taskSimplified=Preview-Document ([IO.File]::ReadAllText((Join-Path $taskRoot 'tempfile\油井压力不稳定试井翻译_简化版.md'))) $taskFont
 if($taskSimplified.page_count -ne 11){throw ('Simplified document produced '+$taskSimplified.page_count+' pages, expected 11')}
 # 4. The device side must be untouched by this deployment.
 if((Listener-Pid 8090) -ne $taskControllerPid){throw 'Controller PID changed'}
 if($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged){throw 'Task continuity check failed'}
 if($taskAfter.error -and $taskAfter.error -ne $taskBefore.error){throw 'New controller error'}
 [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;BackendPid=(Listener-Pid 8080);Phase=$taskAfter.phase;JobId=$taskAfter.job_id;
  FullPages=$taskFullPreview.page_count;SimplifiedPages=$taskSimplified.page_count;Files=$taskManifest.files} | ConvertTo-Json -Depth 4
}catch{
 $taskFailure=$_
 Stop-ScheduledTask -TaskName 'Xieziji-Web' -ErrorAction SilentlyContinue
 try{$taskCurrentWeb=Listener-Pid 8080;Stop-Process -Id $taskCurrentWeb -Force -ErrorAction SilentlyContinue}catch{}
 Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
 Start-ScheduledTask -TaskName 'Xieziji-Web'
 Wait-Backend | Out-Null
 Write-Output 'MARKDOWN_FORMULA_FIX_ROLLED_BACK'
 throw $taskFailure
}
