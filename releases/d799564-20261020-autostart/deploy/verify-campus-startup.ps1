$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$release='C:\Users\102\plotter-web-20261014\releases\d799564-20261020-autostart'
$config=Get-Content -LiteralPath (Join-Path $release 'deploy\services.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$tasks=@(foreach ($name in @('Xieziji-Web','Xieziji-Controller')) {
    $task=Get-ScheduledTask -TaskName $name
    $info=Get-ScheduledTaskInfo -TaskName $name
    [pscustomobject]@{Name=[string]$task.TaskName;State=[string]$task.State;User=[string]$task.Principal.UserId;
        Enabled=[bool]$task.Settings.Enabled;BootTrigger=[bool](@($task.Triggers|ForEach-Object{[string]$_.CimClass.CimClassName}) -contains 'MSFT_TaskBootTrigger');
        RestartCount=[int]$task.Settings.RestartCount;RestartInterval=[string]$task.Settings.RestartInterval;
        ExecutionTimeLimit=[string]$task.Settings.ExecutionTimeLimit;LastResult=$info.LastTaskResult;
        Action=[string]$task.Actions[0].Arguments}
})
$state=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/state' -TimeoutSec 8
$ports=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/ports' -TimeoutSec 8
$fonts=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/fonts' -TimeoutSec 8
$font=@($fonts.fonts|Where-Object{$_.name -eq 'blood8-2_kvenjoy_blood8'})[0]
if (-not $font) { throw 'Expected existing font is missing' }
$body=@{layout_mode='notebook';font_mix=@(@{font_id=$font.id;weight=100});pagination='auto';seed='startup-acceptance';
    pages=@(@{text='你好世界。Hello world!'},@{text='中文与英文可以一起排版。 $x^2+rac{1}{2}$'})} | ConvertTo-Json -Depth 5
$preview=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/preview' -Method Post -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 15
if ($preview.page_count -ne 2) { throw 'Two-page document acceptance failed' }
$gridBody=@{layout_mode='grid';width=157;height=215;margin=0;rows=20;grid=@{columns=20;row_gap=2.9;column_gap=0}}|ConvertTo-Json -Depth 4
$grid=Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/coordinate-preview' -Method Post -ContentType 'application/json' -Body $gridBody -TimeoutSec 8
$page=Invoke-WebRequest -Uri 'http://127.0.0.1:8080/' -UseBasicParsing -TimeoutSec 8
$archive=Invoke-WebRequest -Uri ('http://127.0.0.1:8080/api/documents/'+$preview.document_id+'/archive') -UseBasicParsing -TimeoutSec 8
if ($state.connected -or $state.mode -ne 'serial' -or $state.controller -ne 'cpp') { throw 'Service mode or connection state is unexpected' }
foreach ($task in $tasks) { if ($task.State -ne 'Running' -or $task.User -ne 'SYSTEM' -or -not $task.BootTrigger -or $task.ExecutionTimeLimit -ne 'PT0S') { throw 'Startup task verification failed' } }
[pscustomobject]@{Commit=[string]$config.Commit;Release=$release;Backup=[string]$config.Backup;Logs=[string]$config.LogDir;
    Tasks=$tasks;State=$state;Fonts=@($fonts.fonts).Count;SerialPorts=@($ports.ports|ForEach-Object{[string]$_.device});
    MultiPagePreview=$preview.page_count;FormulaCount=$preview.pages[1].metadata.formula_count;
    GridCellWidth=$grid.metadata.grid.cell_width;GridCellHeight=$grid.metadata.grid.cell_height;
    FrontendHasNewModes=[bool]$page.Content.Contains('id="layoutMode"');ZipStatus=[int]$archive.StatusCode;
    Bindings=@(Get-NetTCPConnection -LocalPort 8080,8090 -State Listen | Select-Object LocalAddress,LocalPort)
} | ConvertTo-Json -Depth 5
