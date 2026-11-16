param([Parameter(Mandatory=$true)][string]$Plan)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$config=Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$stage=[IO.Path]::GetFullPath((Split-Path -Parent $Plan));$root=[IO.Path]::GetFullPath($config.install_dir)
if($config.ui_schema -ne 1 -or $config.run_token -notmatch '^[0-9a-f]{32}$'){throw 'Invalid update-window protocol.'}
$workerFile=Join-Path $stage 'update_install.ps1'
if(-not (Test-Path -LiteralPath $workerFile -PathType Leaf)){throw 'Installer helper is missing.'}
$script:worker=$null;$script:terminal=$false;$script:successful=$false;$script:shownFailure=$false;$watch=[Diagnostics.Stopwatch]::StartNew()
$labels=@{starting='启动安装器';validation='核对安装计划';verify_payload='校验安装文件';wait_exit='等待原程序退出';backup='备份原程序文件';install='替换程序文件';verify_installed='校验更新后的程序';start_application='启动新版本';confirm_startup='等待主窗口和版本确认';cleanup='清理安装临时文件';complete='更新完成';rollback='恢复原程序';failed='更新未完成'}
function ReadJson($Path){
    try{
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        try{$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);try{return ($reader.ReadToEnd() | ConvertFrom-Json)}finally{$reader.Dispose()}}finally{$stream.Dispose()}
    }catch{return $null}
}
function Ready {
    $sha=[Security.Cryptography.SHA256]::Create();$inputStream=[IO.File]::OpenRead($Plan)
    try{$hash=[BitConverter]::ToString($sha.ComputeHash($inputStream)).Replace('-','').ToLowerInvariant()}finally{$inputStream.Dispose();$sha.Dispose()}
    $value=@{token=$config.run_token;pid=$PID;window=$form.Handle.ToInt64();plan_sha256=$hash} | ConvertTo-Json -Compress
    $file=Join-Path $stage 'window-ready.json';$temp=$file+'.tmp';[IO.File]::WriteAllText($temp,$value,(New-Object Text.UTF8Encoding($false)))
    if([IO.File]::Exists($file)){[IO.File]::Replace($temp,$file,$null)}else{[IO.File]::Move($temp,$file)}
}
$form=New-Object Windows.Forms.Form;$form.Text='正在安装更新 · Web Video Downloader';$form.ClientSize=New-Object Drawing.Size(700,425);$form.MinimumSize=New-Object Drawing.Size(680,445);$form.StartPosition='CenterScreen';$form.BackColor=[Drawing.Color]::FromArgb(246,248,252);$form.Font=New-Object Drawing.Font('Microsoft YaHei UI',10);$form.MaximizeBox=$false
$title=New-Object Windows.Forms.Label;$title.Text=('更新至 v'+$config.version);$title.Location=New-Object Drawing.Point(24,20);$title.Size=New-Object Drawing.Size(650,28);$title.Font=New-Object Drawing.Font('Microsoft YaHei UI',15,[Drawing.FontStyle]::Bold);$form.Controls.Add($title)
$phase=New-Object Windows.Forms.Label;$phase.Text='正在启动独立安装器…';$phase.Location=New-Object Drawing.Point(24,70);$phase.Size=New-Object Drawing.Size(650,34);$phase.Font=New-Object Drawing.Font('Microsoft YaHei UI',16,[Drawing.FontStyle]::Bold);$form.Controls.Add($phase)
$metrics=New-Object Windows.Forms.Label;$metrics.Text='请稍候';$metrics.ForeColor=[Drawing.Color]::FromArgb(40,94,212);$metrics.Location=New-Object Drawing.Point(24,110);$metrics.Size=New-Object Drawing.Size(650,30);$metrics.Font=New-Object Drawing.Font('Microsoft YaHei UI',14,[Drawing.FontStyle]::Bold);$form.Controls.Add($metrics)
$bar=New-Object Windows.Forms.ProgressBar;$bar.Location=New-Object Drawing.Point(24,150);$bar.Size=New-Object Drawing.Size(650,23);$bar.Style='Marquee';$form.Controls.Add($bar)
$detail=New-Object Windows.Forms.TextBox;$detail.Multiline=$true;$detail.ReadOnly=$true;$detail.ScrollBars='Vertical';$detail.BorderStyle='None';$detail.BackColor=$form.BackColor;$detail.Location=New-Object Drawing.Point(24,190);$detail.Size=New-Object Drawing.Size(650,90);$detail.Text='安装期间可以最小化窗口；为保护程序和数据，不能中途取消。';$form.Controls.Add($detail)
$elapsed=New-Object Windows.Forms.Label;$elapsed.Location=New-Object Drawing.Point(24,293);$elapsed.Size=New-Object Drawing.Size(650,25);$form.Controls.Add($elapsed)
$note=New-Object Windows.Forms.Label;$note.Text='新主窗口可见且版本确认成功后，此窗口会自动关闭。';$note.Location=New-Object Drawing.Point(24,324);$note.Size=New-Object Drawing.Size(650,23);$form.Controls.Add($note)
$logs=New-Object Windows.Forms.Button;$logs.Text='查看安装日志';$logs.Location=New-Object Drawing.Point(24,365);$logs.Size=New-Object Drawing.Size(200,36);$form.Controls.Add($logs)
$repair=New-Object Windows.Forms.Button;$repair.Text='修复与回退';$repair.Enabled=$false;$repair.Location=New-Object Drawing.Point(238,365);$repair.Size=New-Object Drawing.Size(200,36);$form.Controls.Add($repair)
$close=New-Object Windows.Forms.Button;$close.Text='安装中，请稍候';$close.Enabled=$false;$close.Location=New-Object Drawing.Point(452,365);$close.Size=New-Object Drawing.Size(222,36);$form.Controls.Add($close)
$logs.Add_Click({$path=Join-Path $stage 'install.log';if(Test-Path -LiteralPath $path){Start-Process -FilePath 'notepad.exe' -ArgumentList ('"'+$path+'"') -WindowStyle Normal}})
$repair.Add_Click({$path=Join-Path $root '修复与回退.cmd';if(Test-Path -LiteralPath $path){Start-Process -FilePath $path -WorkingDirectory $root -WindowStyle Normal}})
$close.Add_Click({$form.Close()})
$form.Add_FormClosing({param($sender,$eventArgs) if(-not $script:terminal){$eventArgs.Cancel=$true;$note.Text='安装尚未结束，可以最小化；请等待事务完成或失败处理结束。'}})
function Failure($Text){
    $script:terminal=$true;$script:shownFailure=$true;$phase.Text='更新未完成';$phase.ForeColor=[Drawing.Color]::FromArgb(185,35,35);$metrics.Text='请查看失败步骤与恢复结果';$bar.Style='Continuous';$detail.Text=$Text;$note.Text='安装资料已保留，可查看日志或使用独立修复入口。';$close.Enabled=$true;$close.Text='关闭';$repair.Enabled=(Test-Path -LiteralPath (Join-Path $root '修复与回退.cmd'))
    $form.WindowState='Normal';$form.Activate()
}
$form.Add_Shown({
    try{
        $script:worker=Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$workerFile+'"'),'-Plan',('"'+$Plan+'"')) -WorkingDirectory $stage -WindowStyle Hidden -PassThru
        Ready
    }catch{Failure ('安装进程启动失败，原程序文件未被替换。'+[Environment]::NewLine+$_.Exception.Message)}
})
$timer=New-Object Windows.Forms.Timer;$timer.Interval=150
$timer.Add_Tick({
    if($script:shownFailure){return}
    $progress=ReadJson (Join-Path $stage 'progress.json')
    $elapsed.Text=('本次交接已用 '+[int]$watch.Elapsed.TotalSeconds+' 秒')
    if($progress -and $progress.token -eq $config.run_token){
        $phase.Text=$(if($labels.ContainsKey($progress.phase)){$labels[$progress.phase]}else{$progress.phase})
        if($progress.total -gt 0){$bar.Style='Continuous';$bar.Value=[Math]::Max(0,[Math]::Min(100,[int](100*$progress.done/$progress.total)));$metrics.Text=($progress.done.ToString()+' / '+$progress.total.ToString()+' 项 · 本阶段 '+$bar.Value+'%')}
        else{$bar.Style='Marquee';$metrics.Text='正在等待或处理…'}
        $detail.Text=[string]$progress.detail
        $delta=[Math]::Max(0,[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0-$progress.updated_at)
        $elapsed.Text=('安装已用 '+[int]($progress.elapsed+$delta)+' 秒 · 本阶段 '+[int]($progress.phase_elapsed+$delta)+' 秒')
    }
    if($script:worker){
        $script:worker.Refresh()
        if($script:worker.HasExited){
            $result=ReadJson (Join-Path $stage 'result.json')
            if($result -and $result.token -eq $config.run_token -and $result.installer_pid -eq $script:worker.Id){
                if($result.ok){$script:terminal=$true;$script:successful=$true;$form.Close()}
                else{
                    $rollback=switch($result.rollback_state){'restored'{'原程序已恢复并校验。'} 'failed'{'原程序未能完整恢复，需要独立修复。'} default{'未进入程序替换，无需回滚。'}}
                    $failed=$(if($labels.ContainsKey($result.failed_phase)){$labels[$result.failed_phase]}else{$result.failed_phase})
                    Failure ('失败步骤：'+$failed+[Environment]::NewLine+$result.error+[Environment]::NewLine+$rollback+[Environment]::NewLine+$result.restore_error)
                }
            }else{Failure '安装进程已退出，但没有有效的结果记录。程序状态尚未确认，请查看日志或使用独立修复入口。'}
        }
    }
})
$timer.Start()
try{[Windows.Forms.Application]::Run($form)}finally{$timer.Stop();$timer.Dispose();$form.Dispose();if($script:worker){$script:worker.Dispose()}}
if(-not $script:successful){exit 1}
