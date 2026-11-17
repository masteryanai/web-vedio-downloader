param([string]$PythonPath = "", [string]$DistPath = "")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $DistPath) { $DistPath = Join-Path $PSScriptRoot 'build\release-output' }
$DistPath = [IO.Path]::GetFullPath($DistPath)
$BundlePath = Join-Path $DistPath 'WebVideoDownloader'
if (-not $PythonPath) { $PythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) {
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
    & $PythonPath -m pip --isolated install --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败。' }
}
& $PythonPath (Join-Path $PSScriptRoot 'build_guard.py') $BundlePath
if ($LASTEXITCODE -ne 0) { throw '构建目录含用户资料，原目录已保留。' }
foreach ($required in @('tools\ffmpeg.exe','tools\ffprobe.exe','tools\node.exe','vendor\cctv-h5e-decrypt\build\src\cli\main.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $required))) {
        throw "缺少随包资源：$required。请使用完整源码目录。"
    }
}
& $PythonPath (Join-Path $PSScriptRoot 'site_registry.py')
if ($LASTEXITCODE -ne 0) { throw '适配列表同步失败。' }
& $PythonPath -m PyInstaller --clean --noconfirm --distpath $DistPath (Join-Path $PSScriptRoot 'WebVideoDownloader.spec')
if ($LASTEXITCODE -ne 0) { throw 'Windows 打包失败。' }
& $PythonPath (Join-Path $PSScriptRoot 'trim_bundle.py') $BundlePath
if ($LASTEXITCODE -ne 0) { throw '整理发布目录失败。' }
& $PythonPath (Join-Path $PSScriptRoot 'verify_bundle.py') $BundlePath
if ($LASTEXITCODE -ne 0) { throw '发布内容与源码不一致，已停止构建。' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md'),(Join-Path $PSScriptRoot 'LICENSES.md') -Destination $BundlePath -Force
Write-Host "构建完成：$BundlePath\WebVideoDownloader.exe"
