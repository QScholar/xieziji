$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$release='C:\Users\102\plotter-web-20261014\releases\89df8a2-20261020-stop-return'
$listener=Get-NetTCPConnection -LocalPort 8090 -State Listen
$process=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
if ($process.ExecutablePath -ne (Join-Path $release 'controller\bin\plotter-controller.exe')) { throw 'Wrong controller executable' }
$tasks=foreach ($name in @('Xieziji-Web','Xieziji-Controller')) {
    $task=Get-ScheduledTask -TaskName $name
    $boot=@($task.Triggers | Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger'}).Count -gt 0
    if ([string]$task.State -ne 'Running' -or -not $boot) { throw 'Startup task validation failed' }
    [pscustomobject]@{Name=$name;State=[string]$task.State;User=[string]$task.Principal.UserId;Boot=$boot;RestartCount=[int]$task.Settings.RestartCount}
}
$webRoot=[string](Get-ScheduledTask -TaskName Xieziji-Web).Actions[0].WorkingDirectory
foreach ($name in @('app.js','index.html')) {
    if ((Get-FileHash (Join-Path $webRoot ('frontend\'+$name))).Hash -ne (Get-FileHash (Join-Path $release ('frontend\'+$name))).Hash) { throw 'Frontend does not match release' }
}
[pscustomobject]@{Binary=[string]$process.ExecutablePath;FrontendMatches=$true;Tasks=@($tasks)} | ConvertTo-Json -Depth 4
