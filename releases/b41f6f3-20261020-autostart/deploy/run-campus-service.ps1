param([Parameter(Mandatory=$true)][ValidateSet('backend','controller')][string]$Service)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$configPath=Join-Path $PSScriptRoot 'services.json'
$config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
try {
    if ($Service -eq 'backend') {
        Remove-Item Env:PLOTTER_TOKEN -ErrorAction SilentlyContinue
        $program=$config.Python
        $arguments=@('-u','-m','backend.server','--host','0.0.0.0','--port','8080',
            '--allow-unauthenticated','--allow-host','202.206.255.200','--allow-origin-port','18081',
            '--controller-url','http://127.0.0.1:8090','--font-dir',$config.FontDir,'--jobs-dir',$config.BackendJobs)
    } else {
        $program=Join-Path $config.Release 'controller\bin\plotter-controller.exe'
        $arguments=@('--host','127.0.0.1','--port','8090','--jobs-dir',$config.ControllerJobs)
    }
    $stdout=Join-Path $config.LogDir ($Service+'.stdout.log')
    $stderr=Join-Path $config.LogDir ($Service+'.stderr.log')
    foreach ($log in @($stdout,$stderr)) {
        if (Test-Path -LiteralPath $log) { Copy-Item -LiteralPath $log -Destination ($log+'.previous') -Force }
    }
    # Windows filesystem paths cannot contain double quotes. Keep each argument
    # quoted so the existing Chinese font directory is passed as one argument.
    $argumentLine=($arguments | ForEach-Object { '"'+([string]$_)+'"' }) -join ' '
    $process=Start-Process -FilePath $program -ArgumentList $argumentLine -WorkingDirectory $config.Release `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru
    if ($process.ExitCode -eq 0) { exit 1 }
    exit $process.ExitCode
} catch {
    $_ | Out-String | Add-Content -LiteralPath (Join-Path $config.LogDir ($Service+'.launcher-error.log')) -Encoding UTF8
    exit 1
}
