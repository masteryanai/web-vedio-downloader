param([string]$PythonPath = "")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) {
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
    & $PythonPath -m pip --isolated install --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败。' }
}
foreach ($required in @('tools\ffmpeg.exe','tools\ffprobe.exe','tools\node.exe','vendor\cctv-h5e-decrypt\build\src\cli\main.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $required))) {
        throw "缺少随包资源：$required。请使用完整源码目录。"
    }
}
& $PythonPath -m PyInstaller --noconfirm (Join-Path $PSScriptRoot 'WebVideoDownloaderPrototype.spec')
if ($LASTEXITCODE -ne 0) { throw 'Windows 打包失败。' }
& $PythonPath (Join-Path $PSScriptRoot 'trim_bundle.py')
if ($LASTEXITCODE -ne 0) { throw '整理发布目录失败。' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md'),(Join-Path $PSScriptRoot 'LICENSES.md') -Destination (Join-Path $PSScriptRoot 'dist\WebVideoDownloaderPrototype') -Force
Write-Host '构建完成：dist\WebVideoDownloaderPrototype\WebVideoDownloaderPrototype.exe'
