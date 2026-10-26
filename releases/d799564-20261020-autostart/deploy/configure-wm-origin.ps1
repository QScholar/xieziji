$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$release='C:\Users\102\plotter-web-20261014\releases\d799564-20261020-autostart'
$launcher=Join-Path $release 'deploy\run-campus-service.ps1'
$original=[IO.File]::ReadAllText($launcher)
$origin='https://wm.102.yuna.team'
if ($original.Contains($origin)) { Write-Output 'Origin already configured'; exit 0 }
$state=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 10
if ($state.connected -or $state.phase -ne 'disconnected') { throw 'Device is active; refusing backend restart' }
$listener=Get-NetTCPConnection -LocalPort 8080 -State Listen
$process=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
if ($process.CommandLine -notmatch '(^|\s)"?-m"?\s+"?backend\.server"?(\s|$)' -or $process.ExecutablePath -ne 'C:\Users\102\AppData\Local\Programs\Python\Python310\python.exe') { throw 'Unexpected backend process' }
$needle="'--allow-unauthenticated','--allow-host'"
if (-not $original.Contains($needle)) { throw 'Unexpected launcher format' }
$updated=$original.Replace($needle,"'--frontend-origin','https://wm.102.yuna.team',"+$needle)
$tokens=$null; $errors=$null
[Management.Automation.Language.Parser]::ParseInput($updated,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) { throw 'Updated launcher is invalid' }
$backup=$launcher+'.before-wm-'+(Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath $launcher -Destination $backup
try {
    [IO.File]::WriteAllText($launcher,$updated,(New-Object Text.UTF8Encoding($true)))
    Stop-ScheduledTask -TaskName Xieziji-Web
    Start-Sleep -Milliseconds 700
    if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
        $check=Get-CimInstance Win32_Process -Filter ('ProcessId='+$process.ProcessId)
        if ($check.CommandLine -ne $process.CommandLine -or $check.CreationDate -ne $process.CreationDate) { throw 'Process identity changed' }
        Stop-Process -Id $process.ProcessId -Force
    }
    Start-ScheduledTask -TaskName Xieziji-Web
    $ready=$false
    for ($i=0;$i -lt 30;$i++) {
        Start-Sleep -Seconds 1
        try {
            $check=Invoke-RestMethod http://127.0.0.1:8080/api/state -Headers @{Origin=$origin} -TimeoutSec 2
            if ($check.controller -eq 'cpp') { $ready=$true; break }
        } catch {}
    }
    if (-not $ready) { throw 'Origin verification failed' }
    [pscustomobject]@{Origin=$origin;Backup=$backup;Phase=$check.phase;Task=[string](Get-ScheduledTask -TaskName Xieziji-Web).State} | ConvertTo-Json
} catch {
    [IO.File]::WriteAllText($launcher,$original,(New-Object Text.UTF8Encoding($true)))
    Start-ScheduledTask -TaskName Xieziji-Web
    throw
}
