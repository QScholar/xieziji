. (Join-Path $PSScriptRoot 'deploy-git-rollback.ps1')
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskWeb=Get-ScheduledTask -TaskName 'Xieziji-Web'
$taskRelease=(Resolve-Path -LiteralPath $taskWeb.Actions[0].WorkingDirectory).Path
if (-not $taskRelease.StartsWith($taskRoot+'\releases\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected release path.' }
$taskPackage=Join-Path $taskRoot 'deploy\wrapping-fix-20261020.tar'
if ((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne 'F60E1CC577F3A73BE41CFDA74FAC17818EF317FEFA8CFC8D0738133B3C49C8B9') { throw 'Update package hash mismatch.' }
$taskFiles=@('backend/document.py','backend/layout.py','backend/mixing.py')
$taskControllerPid=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
$taskBefore=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
$taskDeployed=$taskFiles
$null=Assert-CleanSource -Root $taskRoot -Relative $taskDeployed
$taskWebPid=(Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object -First 1).OwningProcess
$taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$taskWebPid"
if ($taskProcess.CommandLine -notmatch 'backend\.server') { throw 'Unexpected web process.' }
function Wait-Backend {
    $taskDeadline=(Get-Date).AddSeconds(30)
    do { try { return Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 3 } catch { Start-Sleep -Milliseconds 300 } } while ((Get-Date) -lt $taskDeadline)
    throw 'Backend did not restart.'
}
try {
    # Only the business backend stops. The independent controller keeps its job.
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Stop-Process -Id $taskWebPid -Force -ErrorAction SilentlyContinue
    tar -xf $taskPackage -C $taskRelease
    if ($LASTEXITCODE -ne 0) { throw 'Update extraction failed.' }
    $taskConfig=Get-Content -LiteralPath (Join-Path $taskRelease 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    & $taskConfig.Python -m compileall -q (Join-Path $taskRelease 'backend')
    if ($LASTEXITCODE -ne 0) { throw 'Updated backend cannot compile.' }
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    $taskAfter=Wait-Backend
    $taskControllerAfter=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
    if ($taskControllerAfter -ne $taskControllerPid) { throw 'Controller PID changed unexpectedly.' }
    if ($taskAfter.job_id -ne $taskBefore.job_id -or $taskAfter.acknowledged -lt $taskBefore.acknowledged -or $taskAfter.error) { throw 'Task continuity check failed.' }
    [pscustomobject]@{Result='UPDATED';ControllerPid=$taskControllerPid;JobId=$taskAfter.job_id;Phase=$taskAfter.phase;AcknowledgedBefore=$taskBefore.acknowledged;AcknowledgedAfter=$taskAfter.acknowledged} | ConvertTo-Json
} catch {
    $taskFailure=$_
    Stop-ScheduledTask -TaskName 'Xieziji-Web'
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'backend\.server' -and $_.ExecutablePath -eq $taskProcess.ExecutablePath } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Restore-CommittedFiles -Root $taskRoot -Relative $taskDeployed
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    Wait-Backend | Out-Null
    Write-Output 'BACKEND_ROLLED_BACK; controller was not stopped'
    throw $taskFailure
}
