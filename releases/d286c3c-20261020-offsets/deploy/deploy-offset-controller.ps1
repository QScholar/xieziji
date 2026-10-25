param([Parameter(Mandatory=$true)][string]$Release)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$root='C:\Users\102\plotter-web-20261014'
$Release=[IO.Path]::GetFullPath($Release)
if (-not $Release.StartsWith($root+'\releases\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid release path' }
$previous=Join-Path $root 'releases\d799564-20261020-autostart'
$config=Get-Content -LiteralPath (Join-Path $previous 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$config.Release=$Release
$info=Get-Content -LiteralPath (Join-Path $Release 'deploy\release-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$config.Commit=$info.commit
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Release 'deploy\services.json') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $previous 'deploy\run-campus-service.ps1') -Destination (Join-Path $Release 'deploy\run-campus-service.ps1')
$binary=Join-Path $Release 'controller\bin\plotter-controller.exe'
$unit=Start-Process -FilePath (Join-Path $Release 'controller\bin\controller-tests.exe') -WorkingDirectory $Release -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $Release 'deploy\tests.stdout.log') -RedirectStandardError (Join-Path $Release 'deploy\tests.stderr.log')
if ($unit.ExitCode -ne 0) { throw 'Controller regression tests failed on server' }
function Api([string]$path,$data=$null) {
    if ($null -eq $data) { return Invoke-RestMethod ('http://127.0.0.1:8080'+$path) -TimeoutSec 15 }
    return Invoke-RestMethod ('http://127.0.0.1:8080'+$path) -Method Post -ContentType application/json -Body ($data | ConvertTo-Json -Compress) -TimeoutSec 15
}
$before=Api '/api/state'
if ($before.connected -and ($before.phase -ne 'idle' -or -not $before.pen_top)) { throw 'Device must be idle with pen raised' }
$listener=Get-NetTCPConnection -LocalPort 8090 -State Listen
$oldProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
if ($oldProcess.ExecutablePath -ne (Join-Path $previous 'controller\bin\plotter-controller.exe')) { throw 'Unexpected controller process' }
$backup=Join-Path $root ('deploy\backups\before-offset-fix-'+(Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $backup | Out-Null
$xml=Export-ScheduledTask -TaskName Xieziji-Controller
[IO.File]::WriteAllText((Join-Path $backup 'Xieziji-Controller.xml'),$xml,[Text.Encoding]::Unicode)
$before | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup 'state-before.json') -Encoding UTF8
if ($before.connected) { Api '/api/disconnect' @{} | Out-Null }
try {
    Stop-ScheduledTask -TaskName Xieziji-Controller
    Start-Sleep -Milliseconds 800
    if (Get-Process -Id $oldProcess.ProcessId -ErrorAction SilentlyContinue) {
        $check=Get-CimInstance Win32_Process -Filter ('ProcessId='+$oldProcess.ProcessId)
        if ($check.ExecutablePath -ne $oldProcess.ExecutablePath -or $check.CreationDate -ne $oldProcess.CreationDate) { throw 'Controller process identity changed' }
        Stop-Process -Id $oldProcess.ProcessId -Force
    }
    $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $Release 'deploy\run-campus-service.ps1')+'" -Service controller'
    $action=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $arguments -WorkingDirectory $Release
    Set-ScheduledTask -TaskName Xieziji-Controller -Action $action | Out-Null
    Start-ScheduledTask -TaskName Xieziji-Controller
    $ready=$false
    for ($i=0;$i -lt 25;$i++) {
        Start-Sleep -Seconds 1
        try { $state=Api '/api/state'; if ($state.phase -eq 'disconnected' -and $state.controller -eq 'cpp') { $ready=$true;break } } catch {}
    }
    if (-not $ready) { throw 'New controller did not start' }
    if ($before.connected) {
        $state=Api '/api/connect' @{port=$before.port}
        $state=Api '/api/check' @{}
        for ($i=0;$i -lt 3;$i++) { if ([Math]::Abs($state.position[$i]-$before.position[$i]) -gt .01) { throw 'Physical position changed during deployment' } }
    }
    [pscustomobject]@{Release=$Release;Commit=$info.commit;Backup=$backup;State=$state;Task=[string](Get-ScheduledTask -TaskName Xieziji-Controller).State;Tests=$unit.ExitCode} | ConvertTo-Json -Depth 5
} catch {
    $failure=$_
    Stop-ScheduledTask -TaskName Xieziji-Controller
    Start-Sleep -Milliseconds 700
    $listener=Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
        if ($process.ExecutablePath -eq $binary) { Stop-Process -Id $process.ProcessId -Force }
    }
    Register-ScheduledTask -TaskName Xieziji-Controller -Xml $xml -Force | Out-Null
    Start-ScheduledTask -TaskName Xieziji-Controller
    throw $failure
}
