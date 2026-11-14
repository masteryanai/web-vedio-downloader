"""Resolve AGE's external player using a disposable installed-browser session."""
from contextlib import contextmanager
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit


@contextmanager
def _driver(factory):
    import diagnostics
    with factory() as driver:
        try:yield driver
        finally:diagnostics.event('age.driver.stop.begin')
    diagnostics.event('age.driver.stop.end')


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
    _say(log, 'AGE：启动浏览器并加载播放页。')
    import diagnostics
    diagnostics.event('age.driver.begin')
    with _driver(sync_playwright) as playwright:
        browser = None
        for channel in channels:
            _check_cancel(cancel)
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True,
                    args=['--autoplay-policy=no-user-gesture-required'])
                diagnostics.event('age.browser.launched',channel)
                break
            except Exception:
                continue
        if browser is None:
            raise VideoError('无法启动 Chrome 或 Edge 解析播放页，请确认浏览器能正常运行。')
        try:
            context = browser.new_context()
            context.route('**/*', lambda route: route.abort() if route.request.resource_type in ('image','font') else route.continue_())
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
                                width:video.videoWidth,height:video.videoHeight,
                                hasAudio:Boolean(video.audioTracks?.length || video.webkitAudioDecodedByteCount>0)};
                        }''')
                    except Exception:
                        continue
                    if not media:
                        continue
                    title = re.sub(r'\s*-\s*在线播放.*$', '', page.title()).strip() or 'AGE动漫'
                    # The player has already parsed the actual source metadata. Avoid a second
                    # network reader when it supplies coherent dimensions and duration.
                    # The downloaded file still goes through ffprobe and full decode verification.
                    if media.get('width',0)>0 and media.get('height',0)>0:
                        streams = [{'codec_type':'video','width':media['width'],'height':media['height']}]
                        if media.get('hasAudio'): streams.append({'codec_type':'audio'})
                        data = {'streams':streams,'format':{'duration':media['duration']}}
                    else:
                        import json
                        _say(log, 'AGE：播放器元数据不足，正在探测正片信息。')
                        probe = _run_process([find_tool('ffprobe'), '-v', 'error', '-rw_timeout', '15000000',
                            '-analyzeduration', '1000000', '-probesize', '1048576',
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
                        '_engine':'age', '_resolved_at':time.monotonic(), 'formats':[{'id':'source','label':f'{width}×{height} · 网站原始视频',
                        'width':width,'height':height,'ext':'mp4','filesize':int(_number(data['format'].get('size'))) or None,
                        '_media_url':media['url'], '_audio_expected':any(s.get('codec_type')=='audio' for s in data.get('streams', []))}]}
                page.wait_for_timeout(700)
            raise VideoError('未取得 AGE 正片视频流，可能需要访问验证或当前线路不可用；短提示片不会作为下载结果。')
        finally:
            cleanup_started=time.monotonic();diagnostics.event('age.browser.close.begin')
            browser.close()
            diagnostics.event('age.browser.close.end',f'duration={time.monotonic()-cleanup_started:.3f}s')


def download_age(info, selected, work, cookies, progress, log, cancel):
    from engine import _download_http, _remux, _probe, _progress
    from engine import VideoError, _say
    refreshed = info if time.monotonic()-info.get('_resolved_at', -10000) < 300 else inspect_age(info['url'], cookies, log, cancel)
    source = work / 'source.mp4'
    for attempt in range(2):
        current = refreshed['formats'][0]
        if any(selected.get(key) and current.get(key) != selected[key] for key in ('width','height')):
            raise VideoError('AGE 原任务画质已变化，请取消后重新下载。')
        _say(log, 'AGE：连接视频源，确认已下载进度。')
        try:
            _download_http(current['_media_url'], source, cookies=None,
                referer=info['url'], progress=progress, cancel=cancel)
            break
        except VideoError as error:
            if attempt or '过期' not in str(error): raise
            _say(log, 'AGE：媒体地址已失效，正在刷新一次。')
            refreshed = inspect_age(info['url'], cookies, log, cancel)
    _progress(progress, 82, '检查已下载的媒体信息', download_percent=100)
    source_info = _probe(source, cancel)
    selected['_audio_expected'] = bool(selected.get('_audio_expected') or source_info['_has_audio'])
    target = work / 'result.mp4'
    _remux(source, target, progress, cancel)
    return target
