$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\pyinstaller-cache'
$portablePath = Join-Path $projectRoot ('build\portable-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$compressor = Join-Path $projectRoot 'build\7zip\x64\7za.exe'
if (-not (Test-Path -LiteralPath $compressor)) {
    throw '请先将官方 7-Zip Extra 解压到 build\7zip，详见 packaging\打包说明.md。'
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m PyInstaller `
    --noconfirm --distpath $portablePath --workpath build\onefile packaging\DLSS5_Standalone.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败，退出代码 $LASTEXITCODE" }
$application = Join-Path $portablePath 'DLSS5_App'
Copy-Item -LiteralPath (Join-Path $projectRoot 'build\7zip\License.txt') `
    -Destination (Join-Path $application '7-Zip-License.txt')
$payload = Join-Path $projectRoot 'build\payload.7z'
if (Test-Path -LiteralPath $payload) { Remove-Item -LiteralPath $payload }
Push-Location -LiteralPath $application
try {
    & $compressor a -t7z $payload '.\*' -mx=7 -mmt=8 -md=64m -ms=on -bsp0
    if ($LASTEXITCODE -ne 0) { throw '压缩失败' }
} finally { Pop-Location }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') packaging\assemble_exe.py
if ($LASTEXITCODE -ne 0) { throw '单文件 EXE 封装失败' }
