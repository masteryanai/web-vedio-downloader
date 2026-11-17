param([Parameter(Mandatory=$true)][string]$Plan,[switch]$NoLaunch,[int]$FailAfter=0)
$ErrorActionPreference='Stop'
$config=Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$stage=[IO.Path]::GetFullPath((Split-Path -Parent $Plan))
$root=[IO.Path]::GetFullPath($config.install_dir).TrimEnd('\')
$backup=$null;$touched=@();$original=@{};$committed=$false;$indexWritten=$false;$oldIndexText=$null;$oldStateText=$null;$installLock=$null;$cacheLock=$null;$blockedByInstaller=$false;$started=$null
$script:clock=[Diagnostics.Stopwatch]::StartNew();$script:phaseClock=[Diagnostics.Stopwatch]::StartNew();$script:phase='starting';$script:timings=@{};$script:lastProgress=-1.0
$script:stats=@{reused_hashes=0;avoided_read_bytes=0;skipped_files=0};$originalHashes=@{};$originalSizes=@{};$script:utf8=[Text.UTF8Encoding]::new($false)
function AtomicJson($Path,$Value){
    $text=$Value | ConvertTo-Json -Depth 20 -Compress;$temporary=$Path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
    try{
        [IO.File]::WriteAllText($temporary,$text,$script:utf8)
        for($retry=0;$retry -lt 8;$retry++){
            try{if([IO.File]::Exists($Path)){[IO.File]::Replace($temporary,$Path,$null)}else{[IO.File]::Move($temporary,$Path)};return}
            catch{if($retry -eq 7){throw};Start-Sleep -Milliseconds 20}
        }
    }finally{if([IO.File]::Exists($temporary)){[IO.File]::Delete($temporary)}}
}
function Log($Message){
    try{$file=Join-Path $stage 'install.log';if([IO.File]::Exists($file) -and ([IO.FileInfo]$file).Length -gt 1048576){[IO.File]::WriteAllText($file,'Earlier log truncated.'+[Environment]::NewLine)};[IO.File]::AppendAllText($file,([DateTime]::UtcNow.ToString('o')+' '+$Message+[Environment]::NewLine),$script:utf8)}catch{}
}
function Progress([int]$Done=0,[int]$Total=0,[string]$Detail='',[switch]$Force){
    if(-not $Force -and $script:clock.Elapsed.TotalSeconds-$script:lastProgress -lt .2 -and $Done -ne $Total){return}
    try{
        $value=@{token=$config.run_token;phase=$script:phase;done=$Done;total=$Total;detail=$Detail;elapsed=$script:clock.Elapsed.TotalSeconds;phase_elapsed=$script:phaseClock.Elapsed.TotalSeconds;updated_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0}
        $bytes=$script:utf8.GetBytes(($value | ConvertTo-Json -Depth 5 -Compress))
        $stream=[IO.File]::Open((Join-Path $stage 'progress.json'),[IO.FileMode]::Create,[IO.FileAccess]::Write,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        try{$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
    }catch{}
    $script:lastProgress=$script:clock.Elapsed.TotalSeconds
}
function Phase([string]$Name){
    $script:timings[$script:phase]=$script:phaseClock.Elapsed.TotalSeconds;$script:phase=$Name;$script:phaseClock.Restart();Log ('phase='+$Name);Progress -Force
}
function Aborted {
    $file=Join-Path $stage 'handoff-abort.json'
    if($config.run_token -and (Test-Path -LiteralPath $file)){
        $abort=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
        if($abort.token -eq $config.run_token){throw 'Main application handoff was aborted; original program was not replaced.'}
    }
}
function CopyHashed([string]$From,[string]$To){
    $null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($To));$inputStream=[IO.File]::OpenRead($From);$outputStream=$null;$algorithm=[Security.Cryptography.SHA256]::Create()
    try{
        $outputStream=[IO.File]::Open($To,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None);$buffer=[byte[]]::new(1048576)
        while(($length=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0){$outputStream.Write($buffer,0,$length);[void]$algorithm.TransformBlock($buffer,0,$length,$buffer,0)}
        [void]$algorithm.TransformFinalBlock(([byte[]]::new(0)),0,0)
        return [BitConverter]::ToString($algorithm.Hash).Replace('-','').ToLowerInvariant()
    }finally{if($outputStream){$outputStream.Dispose()};$inputStream.Dispose();$algorithm.Dispose()}
}
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class WvdInstallWindow {
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
}
'@
function ConfirmStartup($Process,$RequiresAck){
    $seconds=90;if($config.startup_timeout){$seconds=[Math]::Max(1,[Math]::Min(120,[int]$config.startup_timeout))}
    $deadline=(Get-Date).AddSeconds($seconds);$ackFile=Join-Path $stage 'startup-ack.json'
    while((Get-Date) -lt $deadline){
        $Process.Refresh();if($Process.HasExited){throw 'Updated application exited before its main window was confirmed.'}
        $actual=[Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $root 'WebVideoDownloader.exe')).ProductVersion
        try{$versionMatches=([version]$actual).ToString(3) -eq ([version]$target.version).ToString(3)}catch{$versionMatches=$false}
        if(-not $versionMatches){throw 'Installed executable product version does not match the update target.'}
        if($RequiresAck -and (Test-Path -LiteralPath $ackFile)){
            try{$ack=Get-Content -LiteralPath $ackFile -Raw -Encoding UTF8 | ConvertFrom-Json}catch{$ack=$null}
            if($ack -and $ack.token -eq $config.run_token -and $ack.version -eq $target.version -and $ack.pid -eq $Process.Id -and [IO.Path]::GetFullPath($ack.install_dir).TrimEnd('\') -eq $root){
                $owner=[uint32]0;$handle=[IntPtr]([long]$ack.window);$null=[WvdInstallWindow]::GetWindowThreadProcessId($handle,[ref]$owner)
                if($owner -eq $Process.Id -and [WvdInstallWindow]::IsWindowVisible($handle)){return}
            }
        }elseif(-not $RequiresAck){
            $handle=$Process.MainWindowHandle
            if($handle -ne [IntPtr]::Zero -and [WvdInstallWindow]::IsWindowVisible($handle) -and $Process.MainWindowTitle.StartsWith('Web Video Downloader')){return}
        }
        Progress;Start-Sleep -Milliseconds 100
    }
    throw 'Timed out waiting for the updated main window and matching version.'
}
function Sha([string]$File) { $stream=[IO.File]::OpenRead($File);$algorithm=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$algorithm.Dispose()} }
function Safe([string]$Folder,[string]$Name) {
    if ($Name -match '\\|:|\x00' -or $Name -notmatch '^(WebVideoDownloader\.exe|README\.md|LICENSES\.md|_internal/.+)$') {throw 'Non-program path.'}
    foreach($part in $Name.Split('/')) {if ($part -in @('','.','..') -or $part -match '[ .]$|^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)') {throw 'Unsafe path.'}}
    foreach($part in $Name.Split('/')) {if ($part -match '^(?i:data|\.wvd|\.wvd-library|\.portable-data\.json|library\.json|settings\.json|tasks\.json|queue\.json|sessions\.json|library\.db|catalogue\.json|sessions\.dpapi|download-tasks|adapted-sites\.json)$|^(?i:\.wvd-|\.video-downloader-)|(?i:\.(mp4|mkv|avi|mov|webm|m4v|part|srt|ass|mp3|flac))$') {throw 'User data or media path rejected.'}}
    if ($Name -match '(?i:\.ts)$' -and -not $Name.StartsWith('_internal/vendor/cctv-h5e-decrypt/')) {throw 'User media path rejected.'}
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
    for($retry=0;$retry -lt 20;$retry++){
        try{[IO.File]::Copy($From,$To,$true);return}catch{if($retry -eq 19){throw};Start-Sleep -Milliseconds 100}
    }
}
function Verify([string]$Folder,$Inventory){
    $i=0;foreach($row in $Inventory.files){$file=Safe $Folder $row.path;if(-not ([IO.File]::Exists($file)) -or ([IO.FileInfo]$file).Length -ne $row.size -or (Sha $file) -ne $row.sha256){throw ('Program verification failed: '+$row.path)};$i++;Progress $i @($Inventory.files).Count $row.path}
}
function Result($Value){
    $script:timings[$script:phase]=$script:phaseClock.Elapsed.TotalSeconds;$Value.timings=$script:timings;$Value.stats=$script:stats;$Value.elapsed_seconds=$script:clock.Elapsed.TotalSeconds;$Value.token=$config.run_token;$Value.installer_pid=$PID
    AtomicJson (Join-Path $stage 'result.json') $Value
}
try {
    if($config.schema -ne 2 -or $root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\') -or $root -eq $env:USERPROFILE -or $root -eq $env:WINDIR -or -not (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe') -PathType Leaf)){throw 'Unsafe installation folder.'}
    $data=[IO.Path]::GetFullPath($config.data_dir);$updates=Join-Path $data 'updates';$null=New-Item -ItemType Directory -Force -Path $updates
    Phase 'validation';$blockedByInstaller=$true;$lockDeadline=(Get-Date).AddSeconds(15)
    while(-not $cacheLock){
        Aborted
        try{$cacheLock=[IO.File]::Open((Join-Path $updates 'writer.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)}
        catch{if((Get-Date) -ge $lockDeadline){throw};Progress;Start-Sleep -Milliseconds 100}
    }
    $blockedByInstaller=$false
    $payload=[IO.Path]::GetFullPath($config.payload_dir)
    if(-not $payload.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Payload is outside staging folder.'}
    $target=Inventory $config.target_inventory $config.target_sha256;$base=Inventory $config.base_inventory $config.base_sha256
    if($target.version -ne $config.version -or $base.version -ne $config.from_version){throw 'Version mismatch.'}
    if($config.operation -eq 'rollback' -and $target.data_schema -ne $base.data_schema){throw 'Rollback data layout is incompatible; current data must remain portable.'}
    Phase 'verify_payload';Verify $payload $target
    Aborted;Phase 'wait_exit'
    $deadline=(Get-Date).AddSeconds(90)
    while($config.pid -gt 0 -and (Get-Process -Id $config.pid -ErrorAction SilentlyContinue)){
        Aborted;Progress
        if((Get-Date) -gt $deadline){throw 'Application did not exit.'};Start-Sleep -Milliseconds 250
    }
    $running=Get-Process -Name WebVideoDownloader -ErrorAction SilentlyContinue | Where-Object {$_.Path -and $_.Path -eq (Join-Path $root 'WebVideoDownloader.exe')}
    if($running){throw 'Another application instance is still running.'}
    Aborted
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
    Phase 'backup'
    foreach($name in $all.Keys){$original[$name]=[IO.File]::Exists((Safe $root $name))}
    $backupTotal=@($all.Keys | Where-Object {$original[$_]}).Count;$backupDone=0
    foreach($name in $all.Keys){
        if($original[$name]){
            $file=Safe $root $name;$originalSizes[$name]=([IO.FileInfo]$file).Length
            $originalHashes[$name]=CopyHashed $file (Safe $backup $name)
            $backupDone++;Progress $backupDone $backupTotal $name
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
    Phase 'install'
    $targetNames=@{};$count=0;$installDone=0;$installTotal=@($target.files).Count
    foreach($row in $target.files){
        $targetNames[$row.path]=$true;$file=Safe $root $row.path
        $installDone++
        if($originalHashes.ContainsKey($row.path)){
            $script:stats.reused_hashes++;$script:stats.avoided_read_bytes+=$originalSizes[$row.path]
            if($originalHashes[$row.path] -eq $row.sha256){$script:stats.skipped_files++;Progress $installDone $installTotal $row.path;continue}
        }
        $touched+=,$row.path;CopyFile (Safe $payload $row.path) $file;$count++
        if($FailAfter -gt 0 -and $count -ge $FailAfter){throw 'Injected installation failure.'}
        Progress $installDone $installTotal $row.path
    }
    foreach($row in $base.files){if(-not $targetNames.ContainsKey($row.path)){$file=Safe $root $row.path;if([IO.File]::Exists($file)){$touched+=,$row.path;Remove-Item -LiteralPath $file -Force}}}
    $touched+=,'_internal/installation.json';CopyFile $config.target_inventory (Safe $root '_internal/installation.json')
    Phase 'verify_installed';Verify $root $target
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
    if($cacheLock){$cacheLock.Dispose();$cacheLock=$null}
    if(-not $NoLaunch){
        Phase 'start_application';$startArgs=@{FilePath=(Join-Path $root 'WebVideoDownloader.exe');WorkingDirectory=$root;WindowStyle='Normal';PassThru=$true}
        if($config.run_token -and $target.startup_confirmation -eq 1){$startArgs.ArgumentList=@('--update-plan',('"'+$Plan+'"'),'--update-token',$config.run_token)}
        $started=Start-Process @startArgs
        Phase 'confirm_startup'
        if($config.run_token){ConfirmStartup $started ($target.startup_confirmation -eq 1)}
        else{Start-Sleep -Seconds 2;if($started.HasExited -and $started.ExitCode -ne 0){throw 'Updated application failed to start.'}}
    }
    $committed=$true
    if(Test-Path -LiteralPath $pendingFile){Remove-Item -LiteralPath $pendingFile -Force}
    Phase 'cleanup'
    # Discard verified staging files after success; no recursive deletion of user folders.
    $cleanDone=0
    try{foreach($row in $target.files){$cleanDone++;Progress $cleanDone @($target.files).Count $row.path;$file=Safe $payload $row.path;if([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue}}
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
    Phase 'complete';Progress 1 1 -Force;Result @{ok=$true;version=$target.version;backup=$backup;startup_confirmed=(-not $NoLaunch)}
}catch{
    $failure=$_.Exception.Message;$failedPhase=$script:phase;$restoreError='';Log ('failure='+$failure)
    if(-not $committed -and $started){
        try{if(-not $started.HasExited -and [IO.Path]::GetFullPath($started.Path) -eq (Join-Path $root 'WebVideoDownloader.exe')){$started.Kill();$started.WaitForExit(5000) | Out-Null;Start-Sleep -Milliseconds 500}}catch{}
    }
    Phase 'rollback'
    if(-not $committed -and $backup){
        try{
            foreach($name in @($touched | Select-Object -Unique)){
                $file=Safe $root $name
                if($original[$name]){CopyFile (Safe $backup $name) $file;if($originalHashes.ContainsKey($name) -and (Sha $file) -ne $originalHashes[$name]){throw 'Restored program file checksum mismatch.'}}
                elseif([IO.File]::Exists($file)){Remove-Item -LiteralPath $file -Force}
            }
        }catch{$restoreError=$_.Exception.Message}
    }
    if($indexWritten){
        if($null -ne $oldIndexText){$oldIndexText | Set-Content -LiteralPath $index -Encoding UTF8}else{Remove-Item -LiteralPath $index -Force -ErrorAction SilentlyContinue}
        if($config.operation -eq 'rollback'){if($null -ne $oldStateText){$oldStateText | Set-Content -LiteralPath $stateFile -Encoding UTF8}else{Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue}}
    }
    Phase 'failed';Progress -Force;Result @{ok=$false;error=$failure;failed_phase=$failedPhase;restore_error=$restoreError;backup=$backup;rollback_state=$(if($restoreError){'failed'}elseif(@($touched).Count){'restored'}else{'not_needed'})}
    if(-not $NoLaunch -and -not $blockedByInstaller -and -not $restoreError -and (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe'))){try{Start-Process -FilePath (Join-Path $root 'WebVideoDownloader.exe') -WorkingDirectory $root -WindowStyle Normal}catch{}}
    exit 1
}finally{if($installLock){$installLock.Dispose()};if($cacheLock){$cacheLock.Dispose()}}
