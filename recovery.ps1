param([string]$InstallDir=(Split-Path -Parent $PSScriptRoot),[ValidateSet('menu','restore','online')][string]$Action='menu',[switch]$NoLaunch)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$root=[IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$data=Join-Path $env:LOCALAPPDATA 'WebVideoDownloaderPrototype';$updates=Join-Path $data 'updates'
$prefix='https://github.com/masteryanai/web-vedio-downloader/releases/download/'
function Sha($File){$stream=[IO.File]::OpenRead($File);$algorithm=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$algorithm.Dispose()}}
function Fetch($Descriptor,$Destination){
    if($Descriptor.tag -notmatch '^v?\d+\.\d+\.\d+$' -or $Descriptor.asset -notmatch '^[a-zA-Z0-9._-]+$' -or $Descriptor.sha256 -notmatch '^[a-f0-9]{64}$' -or $Descriptor.size -le 0){throw 'Invalid release descriptor.'}
    $url=$prefix+$Descriptor.tag+'/'+$Descriptor.asset
    for($attempt=0;$attempt -lt 4;$attempt++){
        try{
            $offset=0;if(Test-Path -LiteralPath $Destination){$offset=(Get-Item -LiteralPath $Destination).Length}
            if($offset -eq $Descriptor.size){
                if((Sha $Destination) -eq $Descriptor.sha256){return}
                Remove-Item -LiteralPath $Destination;$offset=0
            }
            if($offset -gt $Descriptor.size){Remove-Item -LiteralPath $Destination;$offset=0}
            $request=[Net.HttpWebRequest]::Create($url);$request.UserAgent='WebVideoDownloader-Recovery';$request.Timeout=15000;$request.ReadWriteTimeout=5000
            if($offset -gt 0){$request.AddRange([long]$offset)}
            $response=$request.GetResponse()
            try{
                if($response.StatusCode -eq 206){if($response.Headers['Content-Range'] -notmatch ('^bytes '+$offset+'-\d+/'+$Descriptor.size+'$')){throw 'Invalid partial response.'}}
                else{$offset=0}
                $stream=$response.GetResponseStream();$file=[IO.File]::Open($Destination, $(if($offset -gt 0){[IO.FileMode]::Append}else{[IO.FileMode]::Create}))
                try{$buffer=New-Object byte[] 262144;while(($count=$stream.Read($buffer,0,$buffer.Length)) -gt 0){$file.Write($buffer,0,$count);$offset+=$count;if($offset -gt $Descriptor.size){throw 'Oversized download.'};Write-Progress -Activity 'Downloading verified package' -PercentComplete ([Math]::Min(99,100*$offset/$Descriptor.size))}}
                finally{$file.Dispose();$stream.Dispose()}
            }finally{$response.Dispose()}
            if((Get-Item -LiteralPath $Destination).Length -ne $Descriptor.size -or (Sha $Destination) -ne $Descriptor.sha256){Remove-Item -LiteralPath $Destination;throw 'Checksum mismatch.'}
            Write-Progress -Activity 'Downloading verified package' -Completed;return
        }catch{if($attempt -eq 3){throw};Start-Sleep -Seconds 1}
    }
}
function Metadata($Descriptor,$Name){if($Descriptor.size -gt 2097152){throw 'Metadata too large.'};$path=Join-Path $stage $Name;Fetch $Descriptor $path;return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)}
function ProgramPath($Folder,$Name){
    if($Name -notmatch '^(WebVideoDownloader\.exe|README\.md|LICENSES\.md|_internal/.+)$' -or $Name -match '\\|:|\x00'){throw 'Unsafe program path.'}
    foreach($part in $Name.Split('/')){if($part -in @('','.','..') -or $part -match '[ .]$'){throw 'Unsafe path.'}}
    $full=[IO.Path]::GetFullPath((Join-Path $Folder $Name));$base=[IO.Path]::GetFullPath($Folder).TrimEnd('\')
    if(-not $full.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escape.'}
    $cursor=$full;while($cursor -and $cursor -ne $base){if(([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) -and ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint)){throw 'Link rejected.'};$cursor=[IO.Path]::GetDirectoryName($cursor)}
    if(([IO.Directory]::Exists($base)) -and ([IO.File]::GetAttributes($base) -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked root rejected.'}
    return $full
}
try{
    Write-Host 'Web Video Downloader - independent repair and rollback'
    Write-Host 'Close the downloader first. Tasks, accounts and downloaded media are preserved.'
    if(-not (Test-Path -LiteralPath (Join-Path $root 'WebVideoDownloader.exe'))){throw 'Invalid application directory.'}
    $null=New-Item -ItemType Directory -Force -Path $updates
    $stage=Join-Path $updates ('recovery-'+[Guid]::NewGuid().ToString('N'));$null=New-Item -ItemType Directory -Path $stage
    $payload=Join-Path $stage 'payload';$null=New-Item -ItemType Directory -Path $payload
    $baseFile=Join-Path $root '_internal\installation.json'
    if(-not (Test-Path -LiteralPath $baseFile)){throw 'Missing installed inventory. Extract a complete portable release to a new folder.'}
    $base=Get-Content -LiteralPath $baseFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if($Action -eq 'menu'){Write-Host '1. Restore verified local backup (offline)';Write-Host '2. Select an online version';$choice=Read-Host 'Choose 1 / 2';if($choice -eq '1'){$Action='restore'}elseif($choice -eq '2'){$Action='online'}else{return}}
    $operation='rollback'
    if($Action -eq 'restore'){
        $backupIndex=Join-Path $updates 'backup.json';$pending=Join-Path $updates 'pending.json'
        if(Test-Path -LiteralPath $pending){$backupIndex=$pending}
        $info=Get-Content -LiteralPath $backupIndex -Raw -Encoding UTF8 | ConvertFrom-Json
        $folder=[IO.Path]::GetFullPath($info.folder)
        if((Split-Path -Parent $folder) -ne (Split-Path -Parent $root) -or -not (Split-Path -Leaf $folder).StartsWith((Split-Path -Leaf $root)+'.backup-')){throw 'Unsafe backup location.'}
        $marker=Get-Content -LiteralPath (Join-Path $folder 'backup-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($marker.application -ne 'WebVideoDownloader' -or $marker.install_dir -ne $root -or $marker.token -ne $info.token){throw 'Backup ownership mismatch.'}
        $targetFile=Join-Path $folder 'inventory.json';$target=Get-Content -LiteralPath $targetFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $cachedCatalog=Join-Path $updates 'release-catalog.json'
        if(Test-Path -LiteralPath $cachedCatalog){$known=Get-Content -LiteralPath $cachedCatalog -Raw -Encoding UTF8 | ConvertFrom-Json;if($target.version -in $known.revoked_versions){throw 'This backup version was revoked.'}}
        if($target.data_schema -ne $base.data_schema){throw 'Backup data version is incompatible.'}
        foreach($row in $target.files){$from=ProgramPath $folder $row.path;if((Sha $from) -ne $row.sha256){throw 'Local backup is damaged; use online repair.'};$to=ProgramPath $payload $row.path;$null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($to));[IO.File]::Copy($from,$to,$true)}
    }else{
        $request=[Net.HttpWebRequest]::Create('https://github.com/masteryanai/web-vedio-downloader/releases/latest');$request.UserAgent='WebVideoDownloader-Recovery';$request.Timeout=15000
        $response=$request.GetResponse();$location=$response.ResponseUri.AbsoluteUri;$response.Dispose()
        if($location -notmatch '^https://github.com/masteryanai/web-vedio-downloader/releases/tag/(v\d+\.\d+\.\d+)$'){throw 'Invalid release redirect.'};$tag=$Matches[1]
        $client=New-Object Net.WebClient;$client.Headers['User-Agent']='WebVideoDownloader-Recovery'
        try{$manifest=$client.DownloadString($prefix+$tag+'/update.json') | ConvertFrom-Json}finally{$client.Dispose()}
        if($manifest.schema -ne 2 -or $manifest.version -ne $tag.TrimStart('v')){throw 'Unsupported release manifest.'}
        $catalog=Metadata $manifest.catalog 'catalog.json';$entries=@($catalog.entries | Where-Object {$_.rollback_allowed -eq $true -and $_.data_schema -eq $base.data_schema -and $_.version -notin $catalog.revoked_versions})
        for($i=0;$i -lt $entries.Count;$i++){Write-Host ($i.ToString()+'. '+$entries[$i].version)}
        $index=[int](Read-Host 'Select version index');if($index -lt 0 -or $index -ge $entries.Count){throw 'Invalid selection.'};$entry=$entries[$index]
        $target=Metadata $entry.inventory 'target-download.json';$targetFile=Join-Path $stage 'target-download.json'
        if($target.version -ne $entry.version){throw 'Target version mismatch.'}
        $archive=Join-Path $updates ('recovery-'+$entry.package.sha256+'.zip');Fetch $entry.package $archive
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip=[IO.Compression.ZipFile]::OpenRead($archive)
        try{
            $rows=@{};foreach($row in $target.files){$null=ProgramPath $payload $row.path;if($rows.ContainsKey($row.path)){throw 'Duplicate inventory path.'};$rows[$row.path]=$row};$seen=@{}
            foreach($item in $zip.Entries){
                if($item.FullName.EndsWith('/')){continue};if(-not $item.FullName.StartsWith('WebVideoDownloader/')){throw 'Invalid archive prefix.'};$name=$item.FullName.Substring(19)
                if($name -in @('_internal/installation.json','修复与回退.cmd')){continue}
                $to=ProgramPath $payload $name
                if(-not $rows.ContainsKey($name) -or $seen.ContainsKey($name) -or $item.Length -ne $rows[$name].size){throw 'Unexpected archive file.'};$seen[$name]=$true
                $null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($to));[IO.Compression.ZipFileExtensions]::ExtractToFile($item,$to)
                if((Sha $to) -ne $rows[$name].sha256){throw 'Extracted checksum mismatch.'}
            }
            if($seen.Count -ne $rows.Count){throw 'Incomplete archive.'}
        }finally{$zip.Dispose()}
        if([version]$target.version -ge [version]$base.version){$operation='upgrade'}
    }
    Copy-Item -LiteralPath $baseFile -Destination (Join-Path $stage 'base.json');Copy-Item -LiteralPath $targetFile -Destination (Join-Path $stage 'target.json')
    $plan=@{schema=2;install_dir=$root;payload_dir=$payload;version=$target.version;from_version=$base.version;pid=0;operation=$operation;data_dir=$data;
        base_inventory=(Join-Path $stage 'base.json');base_sha256=(Sha (Join-Path $stage 'base.json'));target_inventory=(Join-Path $stage 'target.json');target_sha256=(Sha (Join-Path $stage 'target.json'))}
    $planFile=Join-Path $stage 'plan.json';$plan | ConvertTo-Json | Set-Content -LiteralPath $planFile -Encoding UTF8
    if(-not $NoLaunch -and (Read-Host ('Install '+$target.version+'? Type YES to confirm')) -cne 'YES'){return}
    $helper=Join-Path $PSScriptRoot 'update_install.ps1'
    $arguments=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$helper,'-Plan',$planFile)
    if($NoLaunch){$arguments+='-NoLaunch'}
    & powershell.exe @arguments
    if($LASTEXITCODE -ne 0){throw ('Installation failed. See '+(Join-Path $stage 'result.json'))}
    Write-Host 'Completed. Previous managed program files retained as the local backup.'
}catch{Write-Host ('Recovery failed: '+$_.Exception.Message) -ForegroundColor Red;exit 1}
