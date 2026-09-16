param([switch]$UpdateOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\pyinstaller-cache'
$temporaryRoot = Join-Path $projectRoot 'build\inno-temp'
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
$env:TEMP = $temporaryRoot
$env:TMP = $temporaryRoot
$portablePath = Join-Path $projectRoot ('build\installer-app-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$compiler = Join-Path $projectRoot 'build\inno\ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '缺少 Inno Setup 编译器 build/inno/ISCC.exe' }
# Models never enter the installer. Check the build machine without forcing a download.
& (Join-Path $projectRoot '.venv\Scripts\python.exe') tools\download_sr_assets.py --check-only
if ($LASTEXITCODE -eq 2) { Write-Host '部分本地模型缺失，将由安装后的模型准备程序按需下载。' }
elseif ($LASTEXITCODE -ne 0) { throw '模型清单检查失败' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m PyInstaller `
    --noconfirm --distpath $portablePath --workpath build\installer-pyinstaller packaging\DLSS5_Standalone.spec
if ($LASTEXITCODE -ne 0) { throw '应用打包失败' }
$application = Join-Path $portablePath 'DLSS5_App'
Set-Content -LiteralPath (Join-Path $projectRoot 'build\installer-app-path.txt') -Value $application -Encoding utf8
$compileArguments = @("/DProjectRoot=$projectRoot", "/DAppSource=$application")
if ($UpdateOnly) { $compileArguments += '/DUpdateOnly' }
& $compiler @compileArguments packaging\DLSS5_Installer.iss
if ($LASTEXITCODE -ne 0) { throw '安装包编译失败' }
$installerSource = Get-Content -LiteralPath packaging\DLSS5_Installer.iss -Raw
$version = [regex]::Match($installerSource, '#define AppVersion "([^"]+)"').Groups[1].Value
$packagePrefix = if ($UpdateOnly) { "DLSS5_Update_$version" } else { "DLSS5_Setup_$version" }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') tools\hash_installer.py --prefix $packagePrefix
if ($LASTEXITCODE -ne 0) { throw '安装包校验清单生成失败' }
