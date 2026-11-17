"""Build full packages, three-baseline file deltas and pinned history metadata."""
import json
from datetime import datetime,timezone
from pathlib import Path
import zipfile
from runtime import APP_VERSION,PRODUCT_ID,EXECUTABLE,UPDATE_MANIFEST,HISTORY_MANIFEST,IS_LITE,EDITION,package_name,inventory_name
from update_protocol import canonical,digest,tree_inventory,zip_inventory,differences,version


def descriptor(path,tag):
    return {'asset':path.name,'tag':tag,'size':path.stat().st_size,'sha256':digest(path)}


def package(root=None,source=None):
    production=root is None;root=Path(root or Path(__file__).resolve().parent)
    source=Path(source) if source is not None else root/(('build/release-output-lite-full' if IS_LITE else 'build/release-output') if production else 'dist')/PRODUCT_ID
    from build_guard import check_output
    check_output(source, packaging=True)
    metadata=source/'_internal/product.json'
    if metadata.exists():
        value=json.loads(metadata.read_text(encoding='utf-8'))
        if value!={'product':'lite' if IS_LITE else 'manager','edition':EDITION}:raise RuntimeError('构建产物与发布产品/组件版本不一致。')
    if production:
        from release_history import ensure_history
        ensure_history(root,APP_VERSION)
    if not (source/EXECUTABLE).is_file():raise RuntimeError('请先运行 build.ps1。')
    tag='v'+APP_VERSION;release=root/'release'/tag;release.mkdir(parents=True,exist_ok=True)
    policy=json.loads((root/'release_policy.json').read_text(encoding='utf-8'))
    target=tree_inventory(source,APP_VERSION);target['data_schema']=policy['data_schema'];target['startup_confirmation']=1
    (source/'_internal'/'installation.json').write_bytes(canonical(target))
    if (root/'recovery-root.cmd').exists():
        from product_identity import render_script
        (source/'修复与回退.cmd').write_text(render_script((root/'recovery-root.cmd').read_text(encoding='utf-8-sig')),encoding='utf-8')
    archive=release/package_name(APP_VERSION)
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        names=[row['path'] for row in target['files']]+['_internal/installation.json']
        if (source/'修复与回退.cmd').is_file():names.append('修复与回退.cmd')
        for name in sorted(names):output.write(source/name,PRODUCT_ID+'/'+name)
    target_file=release/inventory_name(APP_VERSION);target_file.write_bytes(canonical(target))
    notes=(release/'notes.md').read_text(encoding='utf-8') if (release/'notes.md').exists() else f'# Web Video Downloader {tag}'
    manifest={'application':PRODUCT_ID,'edition':EDITION,'version':APP_VERSION,'schema':2,**descriptor(archive,tag),
        'inventory':descriptor(target_file,tag),'data_schema':policy['data_schema'],'startup_confirmation':1,'notes':notes,
        'published_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'force_full':policy['force_full'],'force_full_reason':policy['force_full_reason'],'full_only_from':{},'deltas':[]}
    entries=[{'version':APP_VERSION,'package':descriptor(archive,tag),'inventory':descriptor(target_file,tag),
        'published_at':manifest['published_at'],'notes':notes,'rollback_allowed':True,'data_schema':policy['data_schema']}]
    previous=[]
    for folder in (root/'release').iterdir():
        try:
            if version(folder.name)<version(tag) and (folder/UPDATE_MANIFEST).is_file():previous.append(folder)
        except ValueError:pass
    for folder in sorted(previous,key=lambda p:version(p.name),reverse=True)[:3]:
        old=json.loads((folder/UPDATE_MANIFEST).read_text(encoding='utf-8'));oldzip=folder/old['asset']
        if not oldzip.exists():raise RuntimeError('缺少历史完整包：'+str(oldzip))
        base=zip_inventory(oldzip,old['version']);base['data_schema']=old.get('data_schema',1);base['startup_confirmation']=old.get('startup_confirmation',0)
        basefile=release/inventory_name(base["version"]);basefile.write_bytes(canonical(base))
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
        prefix=PRODUCT_ID+('-full' if IS_LITE and EDITION=='full' else '')
        delta=release/f'{prefix}-v{base["version"]}-to-{tag}-delta.zip'
        with zipfile.ZipFile(delta,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
            for name in changed:output.write(source/name,PRODUCT_ID+'/'+name)
        manifest['deltas'].append({**descriptor(delta,tag),'from_version':base['version'],
            'base_inventory':descriptor(basefile,tag),'deleted':deleted})
    catalog=release/HISTORY_MANIFEST;catalog.write_bytes(canonical({'application':PRODUCT_ID,'schema':2,
        'entries':entries,'revoked_versions':policy['revoked_versions']}))
    manifest['catalog']=descriptor(catalog,tag);(release/UPDATE_MANIFEST).write_bytes(canonical(manifest))
    files=sorted(p for p in release.iterdir() if p.suffix in ('.zip','.json'))
    (release/'SHA256SUMS.txt').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in files),encoding='utf-8')
    for p in files:print(f'{p.name}: {p.stat().st_size/1048576:.2f} MiB')
    return release


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path)
    package(source=parser.parse_args().source)
