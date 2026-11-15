"""Keep a Qt-independent recovery entry outside versioned program files."""
import json
from pathlib import Path
import shutil
import sys
import time
import update_cache
from runtime import APP_VERSION,resource_path
from update_protocol import version


def ensure_maintenance():
    if not getattr(sys,'frozen',False):return
    root=Path(sys.executable).resolve().parent;folder=root/'.maintenance';marker=folder/'owner.json'
    try:
        if folder.is_symlink() or (folder.exists() and getattr(folder.stat(),'st_file_attributes',0)&1024):return
        if folder.exists():
            data=json.loads(marker.read_text(encoding='utf-8'))
            if data.get('application')!='WebVideoDownloader' or version(data['version'])>version(APP_VERSION):return
        folder.mkdir(exist_ok=True)
        for name in ('recovery.ps1','update_install.ps1'):
            source=resource_path(name)
            if source.is_file():shutil.copy2(source,folder/name)
        marker.write_text(json.dumps({'application':'WebVideoDownloader','version':APP_VERSION}),encoding='utf-8')
        shutil.copy2(resource_path('recovery-root.cmd'),root/'修复与回退.cmd')
    except (OSError,ValueError,KeyError):pass


def previous_failure():
    from runtime import app_data_dir
    try:
        updates=(app_data_dir()/'updates').resolve();pointer=updates/'last-install.json'
        if not pointer.exists():pointer=updates/'pending.json'
        pending=json.loads(pointer.read_text(encoding='utf-8-sig'))
        stage=Path(pending['stage']).resolve()
        if stage.parent not in update_cache.roots() or not stage.name.startswith(('install-','recovery-')):return ''
        result=stage/'result.json'
        if not result.exists() and time.time()-stage.stat().st_mtime<120:return ''
        if not result.exists():return '上次更新未完成，可通过“恢复本地备份”或独立修复入口恢复。'
        info=json.loads(result.read_text(encoding='utf-8-sig'))
        if not info.get('ok'):return '上次更新失败，已尝试恢复原版本。详情见更新日志或独立修复入口。'
    except (OSError,ValueError,KeyError,TypeError):pass
    return ''


def result_notice():
    """Read an installer outcome only for this installation, once per result."""
    from runtime import app_data_dir
    try:
        updates=(app_data_dir()/'updates').resolve();pointer=updates/'last-install.json'
        data=json.loads(pointer.read_text(encoding='utf-8-sig'));stage=Path(data['stage']).resolve()
        if stage.parent not in update_cache.roots() or not stage.name.startswith(('install-','recovery-')):return None
        plan=json.loads((stage/'plan.json').read_text(encoding='utf-8-sig'))
        if Path(plan['install_dir']).resolve()!=Path(sys.executable).resolve().parent:return None
        file=stage/'result.json'
        if not file.exists():return None
        token=str(stage)+':'+str(file.stat().st_mtime_ns)+':'+APP_VERSION;ack=updates/'result-notice.json'
        if ack.exists() and json.loads(ack.read_text(encoding='utf-8')).get('token')==token:return None
        result=json.loads(file.read_text(encoding='utf-8-sig'))
        if result.get('ok'):
            ok=plan['version']==APP_VERSION
            message=f'已更新至 v{APP_VERSION}，版本核对成功。' if ok else f'更新记录目标为 v{plan["version"]}，当前运行版本为 v{APP_VERSION}，请检查启动目录或使用独立修复入口。'
        else:
            ok=False;message='上次更新未完成：'+str(result.get('error','安装失败'))[:300]+'；原版本已保留，可重试或使用独立修复入口。'
        ack.write_text(json.dumps({'token':token}),encoding='utf-8')
        ready=updates/'prepared.json'
        if ok and ready.exists():
            cached=json.loads(ready.read_text(encoding='utf-8'))
            if Path(cached['prepared']['plan']).resolve()==stage/'plan.json':ready.unlink()
        if ok:
            try:update_cache.confirm_success(stage)
            except (OSError,ValueError,RuntimeError):pass
        return {'ok':ok,'message':message}
    except (OSError,ValueError,KeyError,TypeError):return None
