"""Retain a verified prepared update without reusing an obsolete process ID."""
import json
import os
from pathlib import Path
import sys
import uuid
from runtime import APP_VERSION,app_data_dir,resource_path
from update_protocol import canonical,digest,validate_inventory,version


def pointer():return app_data_dir()/'updates'/'prepared.json'


def validate(prepared,info):
    if not isinstance(prepared,dict) or not isinstance(info,dict):raise ValueError('待安装记录无效。')
    from update_cache import roots
    allowed=roots();plan=Path(prepared['plan']).resolve();helper=Path(prepared['helper']).resolve()
    if plan.parent.parent not in allowed or not plan.parent.name.startswith('install-') or helper.parent!=plan.parent or helper.name!='update_install.ps1':raise ValueError('待安装计划路径不安全。')
    config=json.loads(plan.read_text(encoding='utf-8-sig'))
    if not isinstance(config,dict):raise ValueError('待安装计划无效。')
    if config.get('schema')!=2 or Path(config['install_dir']).resolve()!=Path(sys.executable).resolve().parent:raise ValueError('待安装计划与当前安装目录不一致。')
    if config['from_version']!=APP_VERSION or config['version']!=info['version']:raise ValueError('待安装计划版本已过期。')
    version(info['version'])
    payload=Path(config['payload_dir']).resolve()
    if payload!=plan.parent/'payload' or not payload.is_dir() or payload.is_symlink():raise ValueError('待安装文件不存在。')
    for name in ('base','target'):
        path=Path(config[name+'_inventory']).resolve()
        if path.parent!=plan.parent or digest(path)!=config[name+'_sha256']:raise ValueError('待安装文件清单校验失败。')
        inv=validate_inventory(json.loads(path.read_text(encoding='utf-8-sig')))
        if inv['version']!=config['from_version' if name=='base' else 'version']:raise ValueError('待安装文件清单版本不符。')
    if digest(helper)!=digest(resource_path('update_install.ps1')):raise ValueError('待安装助手已变化。')
    return config


def remember(prepared,info):
    if not getattr(sys,'frozen',False):return
    validate(prepared,info);path=pointer();path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('prepared-'+uuid.uuid4().hex+'.tmp')
    try:temporary.write_bytes(canonical({'prepared':prepared,'info':info}));temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def restore():
    if not getattr(sys,'frozen',False):return None
    try:
        path=pointer()
        if path.stat().st_size>2*1024**2:return None
        data=json.loads(path.read_text(encoding='utf-8-sig'));validate(data['prepared'],data['info'])
        result=Path(data['prepared']['plan']).parent/'result.json'
        if result.exists():
            outcome=json.loads(result.read_text(encoding='utf-8-sig'))
            if outcome.get('ok'):return None
            data['error']=str(outcome.get('error','安装未完成'))[:500]
        return data
    except (OSError,ValueError,KeyError,TypeError):return None


def refresh_pid(prepared):
    path=Path(prepared['plan']);config=json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('schema')==2:validate(prepared,{'version':config['version']})
    config['pid']=os.getpid();temporary=path.with_suffix('.tmp')
    try:temporary.write_bytes(canonical(config));temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
