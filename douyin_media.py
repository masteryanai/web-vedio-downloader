"""Interpret the current Douyin page's own post response and save its media."""
import urllib.parse
import zipfile
from pathlib import Path


def media_url(value):
    if not isinstance(value, dict):
        return None
    for url in value.get('url_list', []):
        if isinstance(url, str) and urllib.parse.urlsplit(url).scheme in ('https', 'http'):
            return url
    return None


def post_info(post, url):
    from engine import VideoError
    title = post.get('desc') or '抖音作品'
    images = []
    for item in post.get('images') or (post.get('image_post_info') or {}).get('images') or []:
        address = media_url(item.get('display_image') or item)
        if address:
            images.append({'url': address, 'width': int(item.get('width') or 0), 'height': int(item.get('height') or 0)})
    music = media_url((post.get('music') or {}).get('play_url') or {})
    video = post.get('video') or {}
    if images:
        width = min(1920, max((i['width'] for i in images), default=1080)) or 1080
        height = min(1920, max((i['height'] for i in images), default=1920)) or 1920
        width, height = width // 2 * 2, height // 2 * 2
        formats = [{'id': 'images', 'label': f'保存原图 · {len(images)} 张 · ZIP', 'width': 0, 'height': 0, 'filesize': None, 'ext': 'zip'},
                   {'id': 'slideshow', 'label': '合成带配乐的视频 · MP4', 'width': width, 'height': height, 'filesize': None, 'ext': 'mp4', '_audio_expected': True}]
        return {'title': title, 'site': '抖音', 'url': url, 'duration': 0, 'formats': formats,
                '_engine': 'douyin-post', '_kind': 'images', '_images': images, '_music': music}
    streams = video.get('bit_rate') or [{'play_addr': video.get('play_addr'), 'bit_rate': 0}]
    formats = []
    for index, stream in enumerate(streams):
        address = stream.get('play_addr') or {}
        source = media_url(address)
        if source:
            width = int(address.get('width') or video.get('width') or 0)
            height = int(address.get('height') or video.get('height') or 0)
            formats.append({'id': f'video-{index}', 'label': f'{width}×{height} · {int(stream.get("bit_rate") or 0)//1000} kbps', 'width': width, 'height': height, 'filesize': address.get('data_size'), 'ext': 'mp4', '_url': source})
    if not formats:
        raise VideoError('作品已识别，但网站没有返回可下载的原图或视频；请在官方页面完成访问验证后重试。')
    formats.sort(key=lambda f: f['width'] * f['height'], reverse=True)
    return {'title': title, 'site': '抖音', 'url': url, 'duration': float(video.get('duration') or 0)/1000,
            'formats': formats, '_engine': 'douyin-post', '_kind': 'video'}


def download_post(info, selected, work, cookies, progress, log, cancel):
    from engine import _download_http, _run_process, _tool, _remux, _check_cancel, _progress, VideoError
    if info['_kind'] == 'video':
        source = work / 'source.mp4'
        _download_http(selected['_url'], source, cookies=cookies, referer=info['url'], progress=progress, cancel=cancel)
        target = work / 'result.mp4'
        _remux(source, target, progress, cancel)
        return target, '.mp4'
    if selected['id'] == 'slideshow' and not info.get('_music'):
        raise VideoError('网站未返回这条图文的配乐，无法合成带配乐的视频；请选择保存原图，或完成官方页面验证后重新解析。')
    files = []
    for index, image in enumerate(info['_images']):
        _check_cancel(cancel)
        path = work / f'{index+1:03d}.image'
        _download_http(image['url'], path, cookies=cookies, referer=info['url'], progress=None, cancel=cancel)
        head = path.read_bytes()[:32]
        extension = '.jpg' if head.startswith(b'\xff\xd8\xff') else '.png' if head.startswith(b'\x89PNG\r\n\x1a\n') else '.webp' if head[:4] == b'RIFF' and head[8:12] == b'WEBP' else '.avif' if b'ftyp' in head and (b'avif' in head or b'avis' in head) else None
        if extension is None:
            raise VideoError('图片服务器未返回有效图片，请重新解析；未生成成品。')
        final = path.with_suffix(extension)
        path.rename(final)
        files.append(final)
        _progress(progress, 60 * (index+1)/len(info['_images']), f'保存原图 {index+1}/{len(info["_images"])}')
    if selected['id'] == 'images':
        target = work / 'images.zip'
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as archive:
            for path in files:
                _check_cancel(cancel)
                archive.write(path, path.name)
        return target, '.zip'
    music = work / 'music.audio'
    _download_http(info['_music'], music, cookies=cookies, referer=info['url'], progress=None, cancel=cancel)
    # Normalize each source separately; originals are never modified.
    normalized = []
    for index, path in enumerate(files):
        frame = work / f'frame-{index:03d}.png'
        w, h = selected['width'], selected['height']
        _run_process([_tool('ffmpeg'), '-v', 'error', '-i', str(path), '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1', '-frames:v', '1', str(frame)], cancel=cancel, label='处理图文画面')
        normalized.append(frame)
    playlist = work / 'slides.txt'
    playlist.write_text(''.join(f"file '{path.name}'\nduration 3\n" for path in normalized) + f"file '{normalized[-1].name}'\n", encoding='utf-8')
    target = work / 'result.mp4'
    _progress(progress, 70, '合成带配乐的视频')
    _run_process([_tool('ffmpeg'), '-v', 'error', '-f', 'concat', '-safe', '1', '-i', str(playlist), '-stream_loop', '-1', '-i', str(music), '-t', str(len(files)*3), '-r', '25', '-c:v', 'libopenh264', '-b:v', '4000k', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(target)], cancel=cancel, cwd=work, label='合成图文配乐视频')
    return target, '.mp4'
