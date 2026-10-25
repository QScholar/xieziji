param([Parameter(Mandatory=$true)][string]$Release)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$root='C:\Users\102\plotter-web-20261014'
$releaseRoot=Join-Path $root 'releases'
$Release=[IO.Path]::GetFullPath($Release)
if (-not $Release.StartsWith($releaseRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Release path is outside the deployment directory' }
$python=Join-Path $root '.venv\Scripts\python.exe'
$controller=Join-Path $Release 'controller\bin\plotter-controller.exe'
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
$config=[ordered]@{Release=$Release;Commit='b41f6f3fc588d77d82d7af6d573aa302d07eed4e';Python=$python;FontDir=$fontDir;
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
$deadline=(Get-Date).AddSeconds(15)
while (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    if ((Get-Date) -gt $deadline) { throw 'Old web process did not stop; no new tasks have been registered' }
    Start-Sleep -Milliseconds 250
}
try {
    foreach ($service in @('controller','backend')) {
        $name=if ($service -eq 'backend') {'Xieziji-Web'} else {'Xieziji-Controller'}
        $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$launcher+'" -Service '+$service
        $action=New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $Release
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description ('xieziji '+$service+'; boot startup; no automatic device connection; release b41f6f3') -Force | Out-Null
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
    Unregister-ScheduledTask -TaskName 'Xieziji-Controller' -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName 'Xieziji-Web' -Xml $oldXml -Force | Out-Null
    Start-ScheduledTask -TaskName 'Xieziji-Web'
    throw ('Deployment failed; original web startup task restored. '+$failure)
}
