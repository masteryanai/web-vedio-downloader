"""Build full packages, three-baseline file deltas and pinned history metadata."""
import json
from datetime import datetime,timezone
from pathlib import Path
import zipfile
from runtime import APP_VERSION
from update_protocol import canonical,digest,tree_inventory,zip_inventory,differences,version


def descriptor(path,tag):
    return {'asset':path.name,'tag':tag,'size':path.stat().st_size,'sha256':digest(path)}


def package(root=None):
    production=root is None;root=Path(root or Path(__file__).resolve().parent);source=root/'dist'/'WebVideoDownloader'
    if production:
        from release_history import ensure_history
        ensure_history(root,APP_VERSION)
    if not (source/'WebVideoDownloader.exe').is_file():raise RuntimeError('请先运行 build.ps1。')
    tag='v'+APP_VERSION;release=root/'release'/tag;release.mkdir(parents=True,exist_ok=True)
    for stale in release.glob(f'WebVideoDownloader-*-to-{tag}-delta.zip'):stale.unlink()
    policy=json.loads((root/'release_policy.json').read_text(encoding='utf-8'))
    target=tree_inventory(source,APP_VERSION);target['data_schema']=policy['data_schema']
    (source/'_internal'/'installation.json').write_bytes(canonical(target))
    if (root/'recovery-root.cmd').exists():
        (source/'修复与回退.cmd').write_bytes((root/'recovery-root.cmd').read_bytes())
    archive=release/f'WebVideoDownloader-{tag}-windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        names=[row['path'] for row in target['files']]+['_internal/installation.json']
        if (source/'修复与回退.cmd').is_file():names.append('修复与回退.cmd')
        for name in sorted(names):output.write(source/name,'WebVideoDownloader/'+name)
    target_file=release/f'inventory-{tag}.json';target_file.write_bytes(canonical(target))
    notes=(release/'notes.md').read_text(encoding='utf-8') if (release/'notes.md').exists() else f'# Web Video Downloader {tag}'
    manifest={'application':'WebVideoDownloader','version':APP_VERSION,'schema':2,**descriptor(archive,tag),
        'inventory':descriptor(target_file,tag),'data_schema':policy['data_schema'],'notes':notes,
        'published_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'force_full':policy['force_full'],'force_full_reason':policy['force_full_reason'],'full_only_from':{},'deltas':[]}
    entries=[{'version':APP_VERSION,'package':descriptor(archive,tag),'inventory':descriptor(target_file,tag),
        'published_at':manifest['published_at'],'notes':notes,'rollback_allowed':True,'data_schema':policy['data_schema']}]
    previous=[]
    for folder in (root/'release').iterdir():
        try:
            if version(folder.name)<version(tag) and (folder/'update.json').is_file():previous.append(folder)
        except ValueError:pass
    for folder in sorted(previous,key=lambda p:version(p.name),reverse=True)[:3]:
        old=json.loads((folder/'update.json').read_text(encoding='utf-8'));oldzip=folder/old['asset']
        if not oldzip.exists():raise RuntimeError('缺少历史完整包：'+str(oldzip))
        base=zip_inventory(oldzip,old['version']);base['data_schema']=old.get('data_schema',1)
        basefile=release/f'inventory-v{base["version"]}.json';basefile.write_bytes(canonical(base))
        oldnotes=(folder/'notes.md').read_text(encoding='utf-8') if (folder/'notes.md').exists() else old.get('notes',f'历史版本 {folder.name}')
        allowed=base['data_schema']==target['data_schema'] and base['version'] not in policy['revoked_versions']
        entries.append({'version':base['version'],'package':descriptor(oldzip,folder.name),'inventory':descriptor(basefile,tag),
            'published_at':old.get('published_at','未知'),'notes':oldnotes,'data_schema':base['data_schema'],'rollback_allowed':allowed})
        native=lambda inv:{r['path']:r['sha256'] for r in inv['files'] if r['path'].lower().endswith(('.dll','.pyd'))}
        reason=(policy['force_full_reason'] or '此版本要求完整安装') if policy['force_full'] else ''
        if base['data_schema']!=target['data_schema']:reason='数据兼容版本已改变'
        if native(base)!=native(target):reason='运行库或原生组件发生变化'
        if reason:manifest['full_only_from'][base['version']]=reason;continue
        changed,deleted=differences(base,target)
        delta=release/f'WebVideoDownloader-v{base["version"]}-to-{tag}-delta.zip'
        with zipfile.ZipFile(delta,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
            for name in changed:output.write(source/name,'WebVideoDownloader/'+name)
        manifest['deltas'].append({**descriptor(delta,tag),'from_version':base['version'],
            'base_inventory':descriptor(basefile,tag),'deleted':deleted})
    catalog=release/'history.json';catalog.write_bytes(canonical({'application':'WebVideoDownloader','schema':2,
        'entries':entries,'revoked_versions':policy['revoked_versions']}))
    manifest['catalog']=descriptor(catalog,tag);(release/'update.json').write_bytes(canonical(manifest))
    files=sorted(p for p in release.iterdir() if p.suffix in ('.zip','.json'))
    (release/'SHA256SUMS.txt').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in files),encoding='utf-8')
    for p in files:print(f'{p.name}: {p.stat().st_size/1048576:.2f} MiB')
    return release


if __name__=='__main__':package()
