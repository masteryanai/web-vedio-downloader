param([Parameter(Mandatory=$true)][string]$Plan,[switch]$NoLaunch,[int]$FailAfter=0)
$ErrorActionPreference='Stop'
$config=Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$stage=[IO.Path]::GetFullPath((Split-Path -Parent $Plan))
$root=[IO.Path]::GetFullPath($config.install_dir).TrimEnd('\')
$backup=$null;$touched=@();$original=@{};$committed=$false;$indexWritten=$false;$oldIndexText=$null;$oldStateText=$null;$installLock=$null;$blockedByInstaller=$false
function Sha([string]$File) { $stream=[IO.File]::OpenRead($File);$algorithm=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$algorithm.Dispose()} }
function Safe([string]$Folder,[string]$Name) {
    if ($Name -match '\\|:|\x00' -or $Name -notmatch '^(WebVideoDownloader\.exe|README\.md|LICENSES\.md|_internal/.+)$') {throw 'Non-program path.'}
    foreach($part in $Name.Split('/')) {if ($part -in @('','.','..') -or $part -match '[ .]$|^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)') {throw 'Unsafe path.'}}
    $folderFull=[IO.Path]::GetFullPath($Folder).TrimEnd('\');$path=[IO.Path]::GetFullPath([IO.Path]::Combine($folderFull,$Name))
    if(-not $path.StartsWith($folderFull+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escapes folder.'}
    $cursor=$path
    while($cursor -and $cursor -ne $folderFull){
        if(([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) -and ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse point rejected.'}
        $cursor=[IO.Path]::GetDirectoryName($cursor)
    }
    if([IO.Directory]::Exists($folderFull) -and ([IO.File]::GetAttributes($folderFull) -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked root rejected.'}
    return $path
}
function Inventory([string]$File,[string]$Hash) {
    if((Sha $File) -ne $Hash){throw 'Inventory checksum mismatch.'}
    $data=Get-Content -LiteralPath $File -Raw -Encoding UTF8 | ConvertFrom-Json
    if($data.application -ne 'WebVideoDownloader' -or $data.schema -ne 2 -or $data.version -notmatch '^\d+\.\d+\.\d+$' -or @($data.files).Count -lt 1 -or @($data.files).Count -gt 20000){throw 'Invalid inventory.'}
    $seen=@{}
    foreach($row in $data.files){$null=Safe $root $row.path;if($seen.ContainsKey($row.path) -or $row.path -eq '_internal/installation.json' -or $row.sha256 -notmatch '^[a-f0-9]{64}$' -or $row.size -lt 0 -or $row.size -gt 8589934592){throw 'Invalid inventory entry.'};$seen[$row.path]=$true}
    if(-not $seen.ContainsKey('WebVideoDownloader.exe')){throw 'Missing executable.'}
    return $data
}
function CopyFile([string]$From,[string]$To){
    $null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($To))
    [IO.File]::Copy($From,$To,$true)
}
function Verify([string]$Folder,$Inventory){
    foreach($row in $Inventory.files){$file=Safe $Folder $row.path;if(-not ([IO.File]::Exists($file)) -or ([IO.FileInfo]$file).Length -ne $row.size -or (Sha $file) -ne $row.sha256){throw ('Program verification failed: '+$row.path)}}
}
function Result($Value){$Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $stage 'result.json') -Encoding UTF8}
try {
    if($config.schema -ne 2 -or $root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\') -or $root -eq $env:USERPROFILE -or $root -eq $env:WINDIR -or -not (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe') -PathType Leaf)){throw 'Unsafe installation folder.'}
    $data=[IO.Path]::GetFullPath($config.data_dir);$updates=Join-Path $data 'updates';$null=New-Item -ItemType Directory -Force -Path $updates
    $payload=[IO.Path]::GetFullPath($config.payload_dir)
    if(-not $payload.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Payload is outside staging folder.'}
    $target=Inventory $config.target_inventory $config.target_sha256;$base=Inventory $config.base_inventory $config.base_sha256
    if($target.version -ne $config.version -or $base.version -ne $config.from_version){throw 'Version mismatch.'}
    Verify $payload $target
    $deadline=(Get-Date).AddSeconds(90)
    while($config.pid -gt 0 -and (Get-Process -Id $config.pid -ErrorAction SilentlyContinue)){
        if((Get-Date) -gt $deadline){throw 'Application did not exit.'};Start-Sleep -Milliseconds 250
    }
    $running=Get-Process -Name WebVideoDownloader -ErrorAction SilentlyContinue | Where-Object {$_.Path -and $_.Path -eq (Join-Path $root 'WebVideoDownloader.exe')}
    if($running){throw 'Another application instance is still running.'}
    $lockFile=Join-Path $root '.WebVideoDownloader-update.lock'
    if((Test-Path -LiteralPath $lockFile) -and ((Get-Item -LiteralPath $lockFile -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked installer lock rejected.'}
    $blockedByInstaller=$true
    $installLock=[IO.File]::Open($lockFile,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $blockedByInstaller=$false
    @{stage=$stage;install_dir=$root} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $updates 'last-install.json') -Encoding UTF8
    $backup=$root+'.backup-'+[Guid]::NewGuid().ToString('N');$token=[Guid]::NewGuid().ToString('N')
    if((Split-Path -Parent $backup) -ne (Split-Path -Parent $root)){throw 'Invalid backup location.'}
    $null=New-Item -ItemType Directory -Path $backup
    $all=@{};foreach($row in $base.files){$all[$row.path]=$true};foreach($row in $target.files){$all[$row.path]=$true};$all['_internal/installation.json']=$true
    foreach($name in $all.Keys){
        $file=Safe $root $name;$original[$name]=[IO.File]::Exists($file)
        if($original[$name]){
            CopyFile $file (Safe $backup $name)
        }
    }
    # Inventory retains the release baseline, so a damaged backup is never offered as verified.
    CopyFile $config.base_inventory (Join-Path $backup 'inventory.json')
    @{application='WebVideoDownloader';install_dir=$root;token=$token;version=$base.version} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'backup-info.json') -Encoding UTF8
    $data=[IO.Path]::GetFullPath($config.data_dir);$updates=Join-Path $data 'updates';$null=New-Item -ItemType Directory -Force -Path $updates
    $settings=Join-Path $data 'settings.json'
    if(Test-Path -LiteralPath $settings -PathType Leaf){CopyFile $settings (Join-Path $backup 'settings-before-update.json')}
    $pendingFile=Join-Path $updates 'pending.json'
    @{application='WebVideoDownloader';install_dir=$root;folder=$backup;token=$token;version=$base.version;stage=$stage} | ConvertTo-Json | Set-Content -LiteralPath $pendingFile -Encoding UTF8
    $targetNames=@{};$count=0
    foreach($row in $target.files){
        $targetNames[$row.path]=$true;$file=Safe $root $row.path
        if(([IO.File]::Exists($file)) -and (Sha $file) -eq $row.sha256){continue}
        $touched+=,$row.path;CopyFile (Safe $payload $row.path) $file;$count++
        if($FailAfter -gt 0 -and $count -ge $FailAfter){throw 'Injected installation failure.'}
    }
    foreach($row in $base.files){if(-not $targetNames.ContainsKey($row.path)){$file=Safe $root $row.path;if([IO.File]::Exists($file)){$touched+=,$row.path;Remove-Item -LiteralPath $file -Force}}}
    $touched+=,'_internal/installation.json';CopyFile $config.target_inventory (Safe $root '_internal/installation.json')
    Verify $root $target
    $index=Join-Path $updates 'backup.json';$old=$null
    if(Test-Path -LiteralPath $index){$oldIndexText=Get-Content -LiteralPath $index -Raw -Encoding UTF8;try{$old=Get-Content -LiteralPath $index -Raw -Encoding UTF8 | ConvertFrom-Json}catch{}}
    @{folder=$backup;token=$token;version=$base.version} | ConvertTo-Json | Set-Content -LiteralPath $index -Encoding UTF8
    $indexWritten=$true
    $stateFile=Join-Path $updates 'state.json';if(Test-Path -LiteralPath $stateFile){$oldStateText=Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8}
    if($config.operation -eq 'rollback'){
        $stateFile=Join-Path $updates 'state.json';$state=@{}
        if(Test-Path -LiteralPath $stateFile){try{(Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object {$state[$_.Name]=$_.Value}}catch{}}
        $state.ignored_version=$base.version;$state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    }
    if(-not $NoLaunch){
        $started=Start-Process -FilePath (Join-Path $root 'WebVideoDownloader.exe') -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 2
        if($started.HasExited -and $started.ExitCode -ne 0){throw 'Updated application failed to start.'}
    }
    $committed=$true
    if(Test-Path -LiteralPath $pendingFile){Remove-Item -LiteralPath $pendingFile -Force}
    Result @{ok=$true;version=$target.version;backup=$backup}
    # Discard verified staging files after success; no recursive deletion of user folders.
    try{foreach($row in $target.files){$file=Safe $payload $row.path;if([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue}}
    Get-ChildItem -LiteralPath $payload -Directory -Recurse -Force | Sort-Object FullName -Descending | ForEach-Object {if(-not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0){Remove-Item -LiteralPath $_.FullName -ErrorAction SilentlyContinue}}
    }catch{}
    # Retire only files owned by a previous, matching backup marker. Unknown files remain.
    if($old -and $old.folder -ne $backup){
        try{
            $prior=[IO.Path]::GetFullPath($old.folder)
            if((Split-Path -Parent $prior) -eq (Split-Path -Parent $root) -and (Split-Path -Leaf $prior).StartsWith((Split-Path -Leaf $root)+'.backup-')){
                $marker=Get-Content -LiteralPath (Join-Path $prior 'backup-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
                if($marker.application -eq 'WebVideoDownloader' -and $marker.token -eq $old.token -and $marker.install_dir -eq $root){
                    $previous=Get-Content -LiteralPath (Join-Path $prior 'inventory.json') -Raw -Encoding UTF8 | ConvertFrom-Json
                    foreach($row in $previous.files){$file=Safe $prior $row.path;if([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force}}
                    foreach($name in @('_internal/installation.json')){$file=Safe $prior $name;if([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force}}
                    foreach($name in @('inventory.json','backup-info.json','settings-before-update.json')){$file=Join-Path $prior $name;if([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force}}
                    Get-ChildItem -LiteralPath $prior -Directory -Recurse -Force | Sort-Object FullName -Descending | ForEach-Object {if(-not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0){Remove-Item -LiteralPath $_.FullName}}
                    if(@(Get-ChildItem -LiteralPath $prior -Force).Count -eq 0){Remove-Item -LiteralPath $prior}
                }
            }
        }catch{ } # Backup retention must never invalidate a successful installation.
    }
}catch{
    $failure=$_.Exception.Message;$restoreError=''
    if(-not $committed -and $backup){
        try{
            foreach($name in @($touched | Select-Object -Unique)){
                $file=Safe $root $name
                if($original[$name]){CopyFile (Safe $backup $name) $file}
                elseif([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force}
            }
        }catch{$restoreError=$_.Exception.Message}
    }
    if($indexWritten){
        if($null -ne $oldIndexText){$oldIndexText | Set-Content -LiteralPath $index -Encoding UTF8}else{Remove-Item -LiteralPath $index -Force -ErrorAction SilentlyContinue}
        if($config.operation -eq 'rollback'){if($null -ne $oldStateText){$oldStateText | Set-Content -LiteralPath $stateFile -Encoding UTF8}else{Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue}}
    }
    Result @{ok=$false;error=$failure;restore_error=$restoreError;backup=$backup}
    if(-not $NoLaunch -and -not $blockedByInstaller -and -not $restoreError -and (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe'))){try{Start-Process -FilePath (Join-Path $root 'WebVideoDownloader.exe') -WorkingDirectory $root -WindowStyle Hidden}catch{}}
    exit 1
}finally{if($installLock){$installLock.Dispose()}}
