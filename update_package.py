"""Verified update selection and staging. Never writes installed program files."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from runtime import APP_VERSION,app_data_dir,resource_path
from update_protocol import (APPLICATION,INSTALLATION,canonical,digest,validate_inventory,verify_tree,
    differences,assemble_delta,extract_verified,owned_path,version)


class FullConfirmation(RuntimeError):pass


def asset_url(desc):
    import updater
    if not isinstance(desc,dict) or not re.fullmatch(r'[A-Za-z0-9._-]+',str(desc.get('asset',''))):raise ValueError('更新附件名称无效。')
    version(desc.get('tag'));sha=desc.get('sha256','')
    if not re.fullmatch('[0-9a-f]{64}',sha) or type(desc.get('size')) is not int or desc['size']<=0:raise ValueError('更新附件校验信息无效。')
    return updater.PREFIX+desc['tag']+'/'+desc['asset']


def fetch_json(desc):
    import updater
    url=asset_url(desc)
    if desc['size']>2*1024**2:raise ValueError('更新元数据过大。')
    with updater.request(url,asset=True) as response:body=response.read(2*1024**2+1)
    if len(body)!=desc['size'] or hashlib.sha256(body).hexdigest()!=desc['sha256']:raise ValueError('更新元数据 SHA-256 校验失败。')
    return json.loads(body)


def installed_inventory(install):
    try:return validate_inventory(json.loads((Path(install)/INSTALLATION).read_text(encoding='utf-8-sig')))
    except (OSError,ValueError,TypeError):return None


def backup_info(install):
    try:
        info=json.loads((app_data_dir()/'updates'/'backup.json').read_text(encoding='utf-8-sig'))
        root=Path(install).resolve();folder=Path(info['folder']).resolve()
        if folder.parent!=root.parent or not folder.name.startswith(root.name+'.backup-') or folder.is_symlink():return None
        marker=json.loads((folder/'backup-info.json').read_text(encoding='utf-8-sig'))
        if marker.get('application')!=APPLICATION or marker.get('install_dir','').casefold()!=str(root).casefold() or marker.get('token')!=info.get('token'):return None
        inv=validate_inventory(json.loads((folder/'inventory.json').read_text(encoding='utf-8-sig')))
        if inv['version']!=info['version']:return None
        try:
            catalog=json.loads((app_data_dir()/'updates'/'release-catalog.json').read_text(encoding='utf-8'))
            if inv['version'] in catalog.get('revoked_versions',[]):return None
        except (OSError,ValueError,TypeError):pass
        return {**info,'inventory':inv}
    except (OSError,ValueError,KeyError,TypeError):return None


def history(manifest):
    data=fetch_json(manifest['catalog'])
    if data.get('application')!=APPLICATION or data.get('schema')!=2 or not isinstance(data.get('entries'),list) or len(data['entries'])>4:raise ValueError('历史版本清单无效。')
    entries=[];seen=set()
    for entry in data['entries']:
        ver=entry['version'];version(ver)
        if ver in seen:raise ValueError('历史版本重复。')
        seen.add(ver);asset_url(entry['package']);asset_url(entry['inventory'])
        allowed=entry.get('rollback_allowed') is True and entry.get('data_schema')==manifest.get('data_schema',1) and ver not in data.get('revoked_versions',[])
        entries.append({**entry,'rollback_allowed':allowed})
    folder=app_data_dir()/'updates';folder.mkdir(parents=True,exist_ok=True)
    (folder/'release-catalog.json').write_bytes(canonical(data))
    return entries


def select(info,install=None,cancel=None,progress=None,allow_full=False):
    install=Path(install or Path(sys.executable).resolve().parent)
    local=backup_info(install) if info.get('local_restore') else None
    if info.get('local_restore') and not local:raise ValueError('本地备份不可用。')
    target=local['inventory'] if local else validate_inventory(fetch_json(info['manifest']['inventory']))
    if target['version']!=info['version']:raise ValueError('目标程序清单版本不符。')
    base=installed_inventory(install);operation=info.get('operation','upgrade');manifest=info['manifest']
    if base and base['version']!=APP_VERSION:raise ValueError('本地程序版本与安装清单不一致，请使用独立修复工具。')
    if operation=='rollback':
        if not info.get('rollback_allowed') or target['data_schema']!=(base or {}).get('data_schema',1):raise ValueError('该历史版本不允许回退。')
        backup=backup_info(install)
        if backup and backup['version']==target['version'] and verify_tree(backup['folder'],target,cancel,progress)[0]:
            return {'mode':'local','reason':'使用已校验的本地备份，无需下载','target':target,'base':base,'backup':backup,'size':0}
    if info.get('local_restore'):raise ValueError('本地备份校验失败，请改用历史完整包。')
    delta=next((d for d in manifest.get('deltas',[]) if d.get('from_version')==APP_VERSION),None) if operation=='upgrade' else None
    reason=manifest.get('force_full_reason') or '此版本要求完整更新' if manifest.get('force_full') else manifest.get('full_only_from',{}).get(APP_VERSION,'')
    if delta and not reason:
        published=validate_inventory(fetch_json(delta['base_inventory']))
        if published['version']!=APP_VERSION:raise ValueError('增量基线版本错误。')
        base=published
        if verify_tree(install,base,cancel,progress)[0]:
            if delta.get('deleted')!=differences(base,target)[1]:raise ValueError('增量删除清单与文件清单不一致。')
            asset_url(delta)
            return {'mode':'delta','reason':'本地基线完整，优先增量更新','target':target,'base':base,'package':delta,'size':delta['size']}
        reason='本地程序文件与发布基线不一致'
        if not allow_full:raise FullConfirmation(reason+'，是否改用完整包？')
    elif operation=='upgrade' and not reason and not allow_full:raise FullConfirmation('当前版本没有可用增量包，是否改用完整包？')
    if not base:raise ValueError('缺少有效安装清单，请使用完整便携包或独立修复工具。')
    desc={k:manifest[k] for k in ('asset','size','sha256')};desc['tag']=manifest.get('tag','v'+info['version']);asset_url(desc)
    return {'mode':'full','reason':reason or ('历史回退使用完整包' if operation=='rollback' else '用户确认使用完整包'),
        'target':target,'base':base,'package':desc,'size':desc['size']}


def prepare(info,cancel,progress):
    if not getattr(sys,'frozen',False):raise RuntimeError('源码模式不能覆盖安装，请使用发布的 EXE 版本。')
    import updater
    install=Path(sys.executable).resolve().parent
    if not (install/'WebVideoDownloader.exe').is_file():raise ValueError('安装目录无效。')
    plan=select(info,install,cancel,progress,info.get('allow_full',False));folder=app_data_dir()/'updates';folder.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='install-',dir=folder));payload=stage/'payload';payload.mkdir()
    if plan['mode']=='local':
        for row in plan['target']['files']:
            if cancel.is_set():raise RuntimeError('已取消更新。')
            source=owned_path(plan['backup']['folder'],row['path']);dest=owned_path(payload,row['path']);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    else:
        desc=plan['package'];cache=folder/('package-'+desc['sha256']);cache.mkdir(exist_ok=True);archive=cache/'package.zip'
        from update_transfer import download
        from diagnostics import file_lock
        with file_lock(cache):download(archive,desc,lambda headers,timeout:updater.request(asset_url(desc),asset=True,headers=headers,timeout=timeout),cancel,progress)
        progress({'percent':96,'stage':'正在组装并校验完整程序'})
        if plan['mode']=='delta':
            if not verify_tree(install,plan['base'],cancel,progress)[0]:raise FullConfirmation('准备期间本地基线发生变化，是否改用完整包？')
            assemble_delta(install,archive,payload,plan['base'],plan['target'],cancel)
        else:extract_verified(archive,payload,plan['target'],cancel=cancel)
    if not verify_tree(payload,plan['target'],cancel)[0]:raise ValueError('准备后的程序校验失败。')
    if cancel.is_set():raise RuntimeError('已取消更新。')
    config={'schema':2,'install_dir':str(install),'payload_dir':str(payload),'version':info['version'],
        'from_version':APP_VERSION,'pid':os.getpid(),'operation':info.get('operation','upgrade'),'data_dir':str(app_data_dir())}
    for name in ('target','base'):
        file=stage/(name+'.json');file.write_bytes(canonical(plan[name]));config[name+'_inventory']=str(file);config[name+'_sha256']=digest(file)
    (stage/'plan.json').write_bytes(canonical(config))
    shutil.copy2(resource_path('update_install.ps1'),stage/'update_install.ps1')
    return {'plan':str(stage/'plan.json'),'helper':str(stage/'update_install.ps1')}
