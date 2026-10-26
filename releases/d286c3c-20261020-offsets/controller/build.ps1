param([string]$Compiler = 'g++')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskBuild = Join-Path $taskRoot 'build'
New-Item -ItemType Directory -Force -Path $taskBuild | Out-Null
$taskCommon = @('-std=c++17', '-O2', '-pthread', '-static-libgcc', '-static-libstdc++', '-I', "$taskRoot/include", '-I', "$taskRoot/third_party", "$taskRoot/src/controller.cpp", "$taskRoot/src/serial.cpp")
& $Compiler @taskCommon "$taskRoot/src/main.cpp" '-lws2_32' '-lshell32' '-o' "$taskBuild/plotter-controller.exe"
if ($LASTEXITCODE -ne 0) { throw 'Controller build failed.' }
& $Compiler @taskCommon "$taskRoot/tests/test_controller.cpp" '-o' "$taskBuild/controller-tests.exe"
if ($LASTEXITCODE -ne 0) { throw 'Controller tests build failed.' }
& "$taskBuild/controller-tests.exe"
if ($LASTEXITCODE -ne 0) { throw 'Controller tests failed.' }
