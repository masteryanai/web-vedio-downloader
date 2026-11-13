"""Resolve AGE's external player using a disposable installed-browser session."""
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit


def inspect_age(url, cookies=None, log=None, cancel=None):
    from engine import VideoError, _check_cancel, _say, _number, _run_process
    from runtime import find_tool
    if not re.fullmatch(r'/play/\d+/\d+/\d+/?', urlsplit(url).path):
        raise VideoError('请复制 AGE 动漫具体单集的播放页面链接。')
    os.environ['PLAYWRIGHT_NODEJS_PATH'] = find_tool('node')
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    channels = []
    for channel, candidates in (
        ('chrome', [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe', Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe']),
        ('msedge', [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe', Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe']),
    ):
        if any(path.is_file() for path in candidates):
            channels.append(channel)
    if not channels:
        raise VideoError('AGE 动漫解析需要本机安装 Chrome 或 Edge 浏览器。')
    _say(log, '正在使用独立浏览器会话读取 AGE 正片地址；不会将短提示片当成正片。')
    with sync_playwright() as playwright:
        browser = None
        for channel in channels:
            _check_cancel(cancel)
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True,
                    args=['--autoplay-policy=no-user-gesture-required'])
                break
            except Exception:
                continue
        if browser is None:
            raise VideoError('无法启动 Chrome 或 Edge 解析播放页，请确认浏览器能正常运行。')
        try:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=25000)
            except BrowserTimeout:
                pass
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                _check_cancel(cancel)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        media = frame.evaluate('''() => {
                            const video = document.querySelector('video');
                            if (!video || !Number.isFinite(video.duration) || video.duration < 60
                                || !/^https?:/.test(video.currentSrc)) return null;
                            return {url:video.currentSrc,duration:video.duration,
                                width:video.videoWidth,height:video.videoHeight};
                        }''')
                    except Exception:
                        continue
                    if not media:
                        continue
                    title = re.sub(r'\s*-\s*在线播放.*$', '', page.title()).strip() or 'AGE动漫'
                    # Check the selected source itself, including codecs and duration.
                    import json
                    probe = _run_process([find_tool('ffprobe'), '-v', 'error', '-rw_timeout', '15000000',
                        '-show_entries', 'stream=codec_type,width,height:format=duration,size',
                        '-of', 'json=compact=1', media['url']], cancel=cancel, label='AGE正片信息校验')
                    data = json.loads(probe)
                    videos = [stream for stream in data.get('streams', []) if stream.get('codec_type') == 'video']
                    if not videos or _number(data.get('format', {}).get('duration')) < 60:
                        continue
                    video = max(videos, key=lambda item: item.get('width', 0)*item.get('height', 0))
                    width, height = video.get('width', 0), video.get('height', 0)
                    duration = _number(data['format'].get('duration'))
                    _say(log, f'AGE 正片已识别：{width}×{height}，时长约 {int(duration)} 秒。')
                    return {'title':title,'site':'AGE动漫','url':url,'duration':duration,
                        '_engine':'age', 'formats':[{'id':'source','label':f'{width}×{height} · 网站原始视频',
                        'width':width,'height':height,'ext':'mp4','filesize':int(_number(data['format'].get('size'))) or None,
                        '_media_url':media['url'], '_audio_expected':any(s.get('codec_type')=='audio' for s in data.get('streams', []))}]}
                page.wait_for_timeout(700)
            raise VideoError('未取得 AGE 正片视频流，可能需要访问验证或当前线路不可用；短提示片不会作为下载结果。')
        finally:
            browser.close()


def download_age(info, selected, work, cookies, progress, log, cancel):
    from engine import _download_http, _remux
    # Refresh the expiring media link for every new download.
    refreshed = inspect_age(info['url'], cookies, log, cancel)
    source = work / 'source.mp4'
    _download_http(refreshed['formats'][0]['_media_url'], source, cookies=None,
        referer=info['url'], progress=progress, cancel=cancel)
    target = work / 'result.mp4'
    _remux(source, target, progress, cancel)
    return target
