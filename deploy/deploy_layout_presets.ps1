param([string]$DeployRoot = 'C:\Users\102\plotter-web-20261014')
. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$taskRoot = (Resolve-Path -LiteralPath $DeployRoot).Path
$taskPackage = Join-Path $taskRoot 'deploy\layout-presets-update-20261016.tar'
$taskDeployed=@('plotter/static/index.html','plotter/static/app.js','plotter/static/style.css')
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
try {
    & tar.exe -xf $taskPackage -C $taskRoot
    if ($LASTEXITCODE -ne 0) { throw 'Extraction failed' }
    $taskPage = (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8080/').Content
    $taskScript = (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8080/app.js').Content
    if ($taskPage -notlike '*id="layoutPreset"*' -or $taskScript -notlike '*plotter.layout-presets.v1*') { throw 'Updated content was not served' }
    $taskState = Invoke-RestMethod 'http://127.0.0.1:8080/api/state'
    [pscustomobject]@{Files=$taskDeployed;Phase=$taskState.phase;Connected=$taskState.connected;Port=$taskState.port;StaticUpdate=$true} | ConvertTo-Json
} catch {
    Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
    throw
}
