param([Parameter(Mandatory=$true)][string]$Plan, [switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$root = [IO.Path]::GetFullPath($config.install_dir).TrimEnd('\')
$stage = [IO.Path]::GetFullPath((Split-Path -Parent $Plan))
$payload = Join-Path (Split-Path -Parent $root) ('WebVideoDownloader.update-' + [Guid]::NewGuid().ToString('N'))
$backup = $root + '.backup-' + [Guid]::NewGuid().ToString('N')
$owned = @('WebVideoDownloader.exe','_internal','README.md','LICENSES.md')
$backedUp = @()
$installedNames = @()
try {
    if (-not (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe'))) { throw 'Application folder is not valid.' }
    if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\') -or $root -eq $env:USERPROFILE -or $root -eq $env:WINDIR) { throw 'Unsafe application folder.' }
    $hashStream = [IO.File]::OpenRead($config.archive)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($algorithm.ComputeHash($hashStream)).Replace('-','').ToLowerInvariant() }
    finally { $hashStream.Dispose(); $algorithm.Dispose() }
    if ($actualHash -ne $config.sha256) { throw 'Update archive checksum mismatch.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($config.archive)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\','/')
            if (-not $name.StartsWith('WebVideoDownloader/') -or $name.Split('/') -contains '..' -or $name.Contains(':')) { throw 'Unsafe archive entry.' }
            $target = [IO.Path]::GetFullPath((Join-Path $payload $name))
            if (-not $target.StartsWith($payload + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Archive path escapes staging folder.' }
        }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($config.archive, $payload)
    $newRoot = [IO.Path]::GetFullPath((Join-Path $payload 'WebVideoDownloader'))
    if (-not (Test-Path -LiteralPath (Join-Path $newRoot 'WebVideoDownloader.exe')) -or -not (Test-Path -LiteralPath (Join-Path $newRoot '_internal'))) { throw 'Incomplete update package.' }
    $deadline = (Get-Date).AddSeconds(90)
    while ($config.pid -gt 0 -and (Get-Process -Id $config.pid -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) { throw 'The running application did not exit.' }
        Start-Sleep -Milliseconds 300
    }
    # Both destinations are resolved siblings; only the verified application folder is moved.
    if ((Split-Path -Parent $backup) -ne (Split-Path -Parent $root)) { throw 'Invalid backup folder.' }
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($name in $owned) {
        $source = Join-Path $root $name
        if (Test-Path -LiteralPath $source) {
            $finished = $false
            while (-not $finished) {
                try { Move-Item -LiteralPath $source -Destination (Join-Path $backup $name); $finished = $true; $backedUp += $name }
                catch { if ((Get-Date) -gt $deadline) { throw }; Start-Sleep -Milliseconds 500 }
            }
        }
    }
    foreach ($name in $owned) {
        $source = Join-Path $newRoot $name
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination (Join-Path $root $name)
            $installedNames += $name
        }
    }
    if (-not $NoLaunch) {
        $started = Start-Process -FilePath (Join-Path $root 'WebVideoDownloader.exe') -WorkingDirectory $root -WindowStyle Normal -PassThru
        Start-Sleep -Seconds 2
        if ($started.HasExited -and $started.ExitCode -ne 0) { throw 'Updated application failed to start.' }
    }
    @{ok=$true;backup=$backup;version=$config.version} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'result.json') -Encoding UTF8
} catch {
    $failure = $_.Exception.Message
    $failedRoot = $root + '.failed-' + [Guid]::NewGuid().ToString('N')
    if ($installedNames.Count -gt 0) {
        if ((Split-Path -Parent $failedRoot) -ne (Split-Path -Parent $root)) { throw 'Invalid recovery folder.' }
        New-Item -ItemType Directory -Path $failedRoot | Out-Null
        foreach ($name in $installedNames) { Move-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $failedRoot $name) }
    }
    foreach ($name in $backedUp) { Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $root $name) }
    @{ok=$false;error=$failure;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'result.json') -Encoding UTF8
    if (-not $NoLaunch -and (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe'))) { Start-Process -FilePath (Join-Path $root 'WebVideoDownloader.exe') -WorkingDirectory $root -WindowStyle Normal }
    exit 1
}
