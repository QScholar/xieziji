param([Parameter(Mandatory=$true)][string]$Release)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$root='C:\Users\102\plotter-web-20261014'
$releaseRoot=Join-Path $root 'releases'
$Release=[IO.Path]::GetFullPath($Release)
if (-not $Release.StartsWith($releaseRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Release path is outside the deployment directory' }
$python=Join-Path $root '.venv\Scripts\python.exe'
$basePython=[string](& $python -c 'import sys; print(sys._base_executable)')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $basePython)) { throw 'Cannot resolve the existing Python runtime' }
$controller=Join-Path $Release 'controller\bin\plotter-controller.exe'
$releaseInfo=Get-Content -LiteralPath (Join-Path $Release 'deploy\release-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$fontDir='C:\tools\奎享雕刻\奎享雕刻\gcodeFonts'
foreach ($path in @($python,$controller,$fontDir,(Join-Path $Release 'backend\server.py'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw ('Missing deployment prerequisite: '+$path) }
}
$oldState=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/state' -TimeoutSec 10
if ($oldState.connected -or $oldState.phase -ne 'disconnected') { throw 'Old service must be disconnected and inactive before deployment' }
$oldTask=Get-ScheduledTask -TaskName 'Xieziji-Web'
if (Get-ScheduledTask -TaskName 'Xieziji-Controller' -ErrorAction SilentlyContinue) { throw 'A controller startup task already exists; inspect it before replacing it' }
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$backup=Join-Path $root ('deploy\backups\before-split-autostart-'+$stamp)
New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
$oldXml=Export-ScheduledTask -TaskName 'Xieziji-Web'
[IO.File]::WriteAllText((Join-Path $backup 'Xieziji-Web.xml'),$oldXml,[Text.Encoding]::Unicode)
Copy-Item -LiteralPath (Join-Path $root 'deploy\start-public.ps1') -Destination (Join-Path $backup 'start-public.ps1')
$oldState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup 'state-before.json') -Encoding UTF8
$config=[ordered]@{Release=$Release;Commit=$releaseInfo.commit;Python=$basePython;FontDir=$fontDir;
    BackendJobs=(Join-Path $root 'backend-jobs');ControllerJobs=(Join-Path $root 'controller-jobs');LogDir=(Join-Path $root 'deploy\services');Backup=$backup}
foreach ($dir in @($config.BackendJobs,$config.ControllerJobs,$config.LogDir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Release 'deploy\services.json') -Encoding UTF8
$shell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$launcher=Join-Path $Release 'deploy\run-campus-service.ps1'
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$trigger=New-ScheduledTaskTrigger -AtStartup
$trigger.Delay='PT15S'
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 100 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Stop-ScheduledTask -TaskName 'Xieziji-Web'
Start-Sleep -Seconds 2
$oldListener=Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($oldListener) {
    # The old venv redirector can leave its base-python child outside the task.
    # Recheck no device is connected, then stop only the verified old module.
    $lastState=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/state' -TimeoutSec 8
    if ($lastState.connected -or $lastState.phase -ne 'disconnected') { throw 'Old service became active; refusing to terminate it' }
    $oldProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+$oldListener.OwningProcess)
    if (@($python,$basePython) -notcontains [string]$oldProcess.ExecutablePath -or $oldProcess.CommandLine -notmatch '(^|\s)-m\s+plotter\.server(\s|$)') { throw 'Port 8080 belongs to an unexpected process' }
    Stop-Process -Id $oldProcess.ProcessId -Force -ErrorAction Stop
}
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8080 is still occupied' }
try {
    foreach ($service in @('controller','backend')) {
        $name=if ($service -eq 'backend') {'Xieziji-Web'} else {'Xieziji-Controller'}
        $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$launcher+'" -Service '+$service
        $action=New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $Release
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description ('xieziji '+$service+'; boot startup; no automatic device connection; release '+$releaseInfo.commit) -Force | Out-Null
    }
    Start-ScheduledTask -TaskName 'Xieziji-Controller'
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    $deadline=(Get-Date).AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $controllerState=Invoke-RestMethod -Uri 'http://127.0.0.1:8090/v1/state' -TimeoutSec 3
            $backendState=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/state' -TimeoutSec 3
            if ($controllerState.controller -eq 'cpp' -and $backendState.controller -eq 'cpp') { break }
        } catch { }
    } while ((Get-Date) -lt $deadline)
    if ($controllerState.controller -ne 'cpp' -or $backendState.controller -ne 'cpp') { throw 'New services did not pass startup health checks' }
    if ($backendState.connected -or $backendState.mode -ne 'serial') { throw 'Unexpected device connection or service mode after startup' }
    [pscustomobject]@{Release=$Release;Backup=$backup;State=$backendState;Tasks=@('Xieziji-Web','Xieziji-Controller') } | ConvertTo-Json -Depth 4
} catch {
    $failure=$_.Exception.Message
    foreach ($name in @('Xieziji-Web','Xieziji-Controller')) { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue }
    foreach ($port in @(8080,8090)) {
        $listener=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            $owned=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
            $isBackend=$port -eq 8080 -and [string]$owned.ExecutablePath -eq $basePython -and $owned.CommandLine -match '(^|\s)-m\s+backend\.server(\s|$)'
            $isController=$port -eq 8090 -and [string]$owned.ExecutablePath -eq $controller
            if ($isBackend -or $isController) { Stop-Process -Id $owned.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    Unregister-ScheduledTask -TaskName 'Xieziji-Controller' -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName 'Xieziji-Web' -Xml $oldXml -Force | Out-Null
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    throw ('Deployment failed; original web startup task restored. '+$failure)
}
