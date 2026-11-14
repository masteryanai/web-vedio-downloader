"""Populate the publisher's ignored cache with the last three stable releases."""
import json
from pathlib import Path
import threading
import updater
from update_protocol import canonical,digest,version
from update_transfer import download


def ensure_history(root,current):
    root=Path(root);cache=root/'release';cache.mkdir(exist_ok=True)
    try:
        releases=updater.get_json(f'https://api.github.com/repos/{updater.REPO}/releases?per_page=20')
        eligible=[]
        for release in releases:
            try:
                tag=release['tag_name']
                if not release.get('draft') and not release.get('prerelease') and version(tag)<version(current):eligible.append(release)
            except (ValueError,KeyError,TypeError):continue
        eligible=sorted(eligible,key=lambda r:version(r['tag_name']),reverse=True)[:3]
    except Exception as error:
        available=[]
        for folder in cache.iterdir():
            try:
                data=json.loads((folder/'update.json').read_text(encoding='utf-8'))
                updater.validate_manifest(data,folder.name);archive=folder/data['asset']
                if version(folder.name)<version(current) and archive.is_file() and archive.stat().st_size==data['size'] and digest(archive)==data['sha256']:available.append(folder)
            except (OSError,ValueError,KeyError,TypeError,RuntimeError):pass
        if len(available)>=3:return
        raise RuntimeError('无法获取最近三版的发行缓存；请连接 GitHub 后重试，避免发布缺失历史版本的更新。') from error
    for release in eligible:
        tag=release['tag_name'];folder=cache/tag;folder.mkdir(exist_ok=True);file=folder/'update.json'
        if file.exists():manifest=json.loads(file.read_text(encoding='utf-8'))
        else:manifest=updater.get_json(updater.PREFIX+tag+'/update.json',True)
        updater.validate_manifest(manifest,tag);archive=folder/manifest['asset']
        if not archive.is_file() or archive.stat().st_size!=manifest['size'] or digest(archive)!=manifest['sha256']:
            download(archive,manifest,lambda headers,timeout:updater.request(updater.PREFIX+tag+'/'+manifest['asset'],True,headers,timeout),threading.Event(),lambda _:None)
        manifest['published_at']=release.get('published_at',manifest.get('published_at','未知'));file.write_bytes(canonical(manifest))
        if not (folder/'notes.md').exists():(folder/'notes.md').write_text(release.get('body',''),encoding='utf-8')
