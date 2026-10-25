$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$taskRoot='C:\Users\102\plotter-web-20261014'
$taskNewRelease=Join-Path $taskRoot 'releases\89df8a2-20261020-limit12000'
$taskFlag=Join-Path $taskRoot 'backend-jobs\.speed-limit-update-pending'
$taskStatus=Join-Path $taskRoot 'deploy\services\limit12000-status.json'
$taskBackup=Join-Path $taskRoot 'deploy\backups\before-limit12000-controller'
function Publish-Update([string]$Phase,[string]$Message) {
    @{phase=$Phase;message=$Message;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $taskStatus -Encoding UTF8
}
function Wait-Controller {
    $taskDeadline=(Get-Date).AddSeconds(35)
    do { try { return Invoke-RestMethod http://127.0.0.1:8090/v1/state -TimeoutSec 3 } catch { Start-Sleep -Milliseconds 400 } } while((Get-Date) -lt $taskDeadline)
    throw 'Controller did not start.'
}
function Connect-Native([string]$Port) {
    Invoke-RestMethod http://127.0.0.1:8090/v1/control/connect -Method Post -ContentType application/json -Body (@{port=$Port}|ConvertTo-Json) -TimeoutSec 30 | Out-Null
}
if(-not(Test-Path -LiteralPath $taskFlag)){exit 0}
Publish-Update 'waiting' 'Waiting for the current operation to finish; no task interruption.'
while(Test-Path -LiteralPath $taskFlag) {
    try { $taskState=Invoke-RestMethod http://127.0.0.1:8080/api/state -TimeoutSec 5 } catch { Start-Sleep -Seconds 3; continue }
    if($taskState.phase -notin @('idle','complete','stopped','disconnected') -or
       ($taskState.connected -and $taskState.status -notlike '<Idle*') -or $taskState.error) { Start-Sleep -Seconds 3; continue }
    $taskRestorePort=if($taskState.connected){$taskState.port}else{$null}
    # This is the controller's atomic idle guard: it rejects a newly started
    # operation. The backend pending flag blocks new starts/reconnections.
    if($taskRestorePort) {
        try { Invoke-RestMethod http://127.0.0.1:8080/api/disconnect -Method Post -ContentType application/json -Body '{}' -TimeoutSec 10 | Out-Null }
        catch { Start-Sleep -Seconds 3; continue }
    }
    $taskOld=Get-ScheduledTask -TaskName 'Xieziji-Controller'
    New-Item -ItemType Directory -Force -Path $taskBackup | Out-Null
    Export-ScheduledTask -TaskName 'Xieziji-Controller' | Set-Content -LiteralPath (Join-Path $taskBackup 'controller.xml') -Encoding UTF8
    $taskState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-before.json') -Encoding UTF8
    try {
        Publish-Update 'applying' 'Current task finished; switching controller release.'
        $taskOwner=(Get-NetTCPConnection -LocalPort 8090 -State Listen | Select-Object -First 1).OwningProcess
        $taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$taskOwner"
        if($taskProcess.Name -ne 'plotter-controller.exe'){throw 'Unexpected process on the controller port.'}
        Stop-ScheduledTask -TaskName 'Xieziji-Controller'
        Stop-Process -Id $taskOwner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        $taskArguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $taskNewRelease 'deploy\run-campus-service.ps1')+'" -Service controller'
        $taskAction=New-ScheduledTaskAction -Execute $taskOld.Actions[0].Execute -Argument $taskArguments -WorkingDirectory $taskNewRelease
        Set-ScheduledTask -TaskName 'Xieziji-Controller' -Action $taskAction | Out-Null
        Start-ScheduledTask -TaskName 'Xieziji-Controller'
        $taskNew=Wait-Controller
        if($taskNew.speed_limit -ne 12000){throw 'New controller did not report the 12000 limit.'}
        if($taskRestorePort){Connect-Native $taskRestorePort}
        $taskNew=Invoke-RestMethod http://127.0.0.1:8090/v1/state
        if($taskNew.error -or $taskNew.phase -notin @('idle','disconnected')){throw 'Unexpected controller state after update.'}
        $taskNew | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskBackup 'state-after.json') -Encoding UTF8
        Remove-Item -LiteralPath $taskFlag
        Publish-Update 'complete' '12000 mm/min software limit enabled; machine settings unchanged.'
        Unregister-ScheduledTask -TaskName 'Xieziji-Limit12000-Apply' -Confirm:$false -ErrorAction SilentlyContinue
        exit 0
    } catch {
        $taskFailure=$_
        try {
            Stop-ScheduledTask -TaskName 'Xieziji-Controller'
            Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
                $taskP=Get-CimInstance Win32_Process -Filter "ProcessId=$_"
                if($taskP.Name -eq 'plotter-controller.exe'){Stop-Process -Id $taskP.ProcessId -Force -ErrorAction SilentlyContinue}
            }
            Set-ScheduledTask -TaskName 'Xieziji-Controller' -Action $taskOld.Actions | Out-Null
            Start-ScheduledTask -TaskName 'Xieziji-Controller'
            Wait-Controller | Out-Null
            if($taskRestorePort){Connect-Native $taskRestorePort}
        } catch { Publish-Update 'error' ('Rollback error: '+$_); throw }
        Publish-Update 'error' ('Previous controller restored: '+$taskFailure)
        throw $taskFailure
    }
}
