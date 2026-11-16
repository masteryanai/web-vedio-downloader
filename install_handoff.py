"""Installer handoff and visible-window startup acknowledgement."""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from runtime import APP_VERSION,app_data_dir,program_dir,resource_path
from update_protocol import digest,canonical


def atomic(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temporary.write_bytes(canonical(value));temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def launch(prepared,timeout=15):
    import update_pending,update_cache
    with update_cache.operation():
        update_pending.refresh_pid(prepared);plan=Path(prepared['plan']);config=json.loads(plan.read_text(encoding='utf-8-sig'));stage=plan.parent
        if config.get('ui_schema')!=1:raise RuntimeError('待安装包使用旧安装助手，请重新准备更新包。')
        helper=stage/'update_window.ps1'
        if digest(helper)!=digest(resource_path('update_window.ps1')):raise ValueError('独立更新窗口文件校验失败。')
        nonce=uuid.uuid4().hex;config['run_token']=nonce;atomic(plan,config)
        for name in ('window-ready.json','startup-ack.json','handoff-abort.json','progress.json','result.json'):(stage/name).unlink(missing_ok=True)
        expected=digest(plan)
        with (stage/'window-error.log').open('wb') as errors:
            process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',str(helper),'-Plan',str(plan)],cwd=stage,stdout=subprocess.DEVNULL,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('独立更新窗口未能启动，原程序和待安装文件已保留。')
                try:
                    ready=json.loads((stage/'window-ready.json').read_text(encoding='utf-8-sig'))
                    if ready.get('token')==nonce and ready.get('pid')==process.pid and ready.get('plan_sha256')==expected:
                        return {'pid':process.pid,'token':nonce}
                except (OSError,ValueError):pass
                time.sleep(.05)
            raise RuntimeError('独立更新窗口启动超时，原程序继续运行；可查看窗口日志后重试。')
        except BaseException:
            atomic(stage/'handoff-abort.json',{'token':nonce});raise


def acknowledge(window,plan_path,token):
    if not plan_path or not re.fullmatch('[0-9a-f]{32}',token or ''):return False
    import update_cache
    plan=Path(plan_path).resolve();stage=plan.parent
    if stage.parent not in update_cache.roots() or not stage.name.startswith(('install-','recovery-')):return False
    config=json.loads(plan.read_text(encoding='utf-8-sig'))
    if config.get('run_token')!=token or config.get('version')!=APP_VERSION or Path(config['install_dir']).resolve()!=program_dir():return False
    pointer=json.loads((app_data_dir()/'updates/last-install.json').read_text(encoding='utf-8-sig'))
    if Path(pointer['stage']).resolve()!=stage:return False
    if not window.isVisible() or window.isMinimized() or not window.windowHandle() or not window.windowHandle().isExposed():return False
    atomic(stage/'startup-ack.json',{'token':token,'version':APP_VERSION,'install_dir':str(program_dir()),'pid':os.getpid(),'window':int(window.winId())})
    return True
