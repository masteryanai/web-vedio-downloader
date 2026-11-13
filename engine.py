"""网站视频解析、下载与完整性校验。

所有带签名的媒体地址仅在内存中使用；公开日志不会输出这些地址或 Cookie。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from runtime import find_tool, resource_path


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1"
)


class VideoError(RuntimeError):
    """可直接显示给用户的错误。"""


class DownloadCancelled(VideoError):
    def __init__(self):
        super().__init__("操作已取消。")


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled()


def _safe_message(message, cookies=None):
    text = str(message)
    text = re.sub(r"https?://[^\s\"'<>]+", "[媒体地址]", text)
    text = re.sub(r"(?i)(cookie|authorization|access[_-]?token|refresh[_-]?token|client_secret|SESSDATA)\s*[:=]\s*[^\s;,]+", r"\1=[已隐藏]", text)
    for cookie in cookies or []:
        value = str(cookie.get("value") or "")
        if len(value) >= 4:
            text = text.replace(value, "[已隐藏]")
    return text.strip()[:1200]


def _say(log, message, cookies=None):
    if log:
        log(_safe_message(message, cookies))


def _progress(callback, percent=0, stage="下载中", speed=None, eta=None, download_percent=None, phase=None):
    if callback:
        callback({
            "percent": max(0.0, min(100.0, float(percent or 0))),
            "stage": stage,
            "download_percent": max(0.,min(100.,float(download_percent))) if download_percent is not None else None,
            "phase": phase,
            "speed": speed,
            "eta": eta,
        })


def _cookie_jar(cookies=None, fallback_domain=""):
    jar = http.cookiejar.CookieJar()
    for item in cookies or []:
        name, value = item.get("name"), item.get("value")
        if not name or value is None:
            continue
        domain = str(item.get("domain") or fallback_domain)
        if not domain:
            continue
        expires = item.get("expires")
        try:
            expires = int(float(expires)) if expires and float(expires) > 0 else None
        except (TypeError, ValueError, OverflowError):
            expires = None
        if expires is not None and expires <= time.time():
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=str(name), value=str(value), port=None,
            port_specified=False, domain=domain,
            domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
            path=str(item.get("path") or "/"), path_specified=True,
            secure=bool(item.get("secure")), expires=expires,
            discard=expires is None, comment=None, comment_url=None,
            rest={"HttpOnly": None} if item.get("httpOnly") else {}, rfc2109=False,
        ))
    return jar


def _opener(cookies=None, domain=""):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar(cookies, domain)))


def _fetch(url, *, cookies=None, ua=DESKTOP_UA, referer=None, cancel=None, max_bytes=16 * 1024 * 1024):
    _check_cancel(cancel)
    headers = {"User-Agent": ua, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    opener = _opener(cookies, urllib.parse.urlsplit(url).hostname or "")
    for attempt in range(3):
        _check_cancel(cancel)
        try:
            with opener.open(request, timeout=15) as response:
                chunks, length = [], 0
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    length += len(chunk)
                    if length > max_bytes:
                        raise VideoError("页面响应过大，无法按视频页面解析。")
                    chunks.append(chunk)
                return b"".join(chunks), response.geturl(), dict(response.headers)
        except DownloadCancelled:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                if cancel:
                    cancel.wait(0.5 * (attempt + 1))
                continue
            if exc.code in (401, 403, 412, 429):
                raise VideoError("网站拒绝了请求或需要访问验证，请先在账号窗口完成登录/验证后重试。") from None
            raise VideoError(f"网站返回 HTTP {exc.code}，该链接目前无法访问。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise VideoError("网络连接失败或超时，请检查网络后重试。") from None
    raise VideoError("网络请求失败。")


def _text(url, **kwargs):
    body, final_url, headers = _fetch(url, **kwargs)
    charset = re.search(r"charset=([\w-]+)", headers.get("Content-Type", ""), re.I)
    encoding = charset.group(1) if charset else "utf-8"
    try:
        return body.decode(encoding, errors="replace"), final_url
    except LookupError:
        return body.decode("utf-8", errors="replace"), final_url


def _json(url, **kwargs):
    body, _, _ = _fetch(url, **kwargs)
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise VideoError("网站未返回有效的视频数据，可能需要先完成访问验证。") from None


def _extract_url(text):
    match = re.search(r"https?://[^\s<>\"'，。！？【】（）]+", str(text or "").replace("https\\://", "https://").replace("http\\://", "http://"), re.I)
    if not match:
        raise VideoError("请粘贴视频网址，或包含网址的分享文案。")
    url = match.group(0).rstrip(".,;:!?)）]】}＞>")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password:
        raise VideoError("网址格式不正确，请重新复制视频链接。")
    return url


def _site(url):
    from site_registry import lookup
    entry = lookup(url)
    if entry:
        return entry["name"]
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    def belongs(*domains):
        return any(host == domain or host.endswith("." + domain) for domain in domains)
    if belongs("cctv.com", "cntv.cn", "cctv.cn"):
        return "央视网"
    if host == "haokan.baidu.com" or host.endswith(".haokan.baidu.com"):
        return "好看视频"
    if belongs("douyin.com", "iesdouyin.com"):
        return "抖音"
    if belongs("bilibili.com", "b23.tv", "bili2233.cn"):
        return "B站"
    if belongs("agedm.io"):
        return "AGE动漫"
    if belongs("youku.com", "youku.tv"):
        return "优酷"
    return "其他网站"


def _normalize_url(text, cookies=None, cancel=None):
    url = _extract_url(text)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("v.douyin.com", "b23.tv", "www.b23.tv", "bili2233.cn", "bili2233.com"):
        _, url, _ = _fetch(url, cookies=cookies, cancel=cancel)
    if _site(url) == "抖音":
        parsed = urllib.parse.urlsplit(url)
        match = re.search(r"/(?:share/)?(?:video|note|slides)/(\d+)", parsed.path)
        if not match:
            match = re.search(r"(?:^|&)(?:modal_id|aweme_id)=(\d+)", parsed.query)
        if match:
            kind = "note" if re.search(r"/(?:share/)?(?:note|slides)/", parsed.path) else "video"
            url = f"https://www.douyin.com/{kind}/{match.group(1)}"
    if urllib.parse.urlsplit(url).hostname == "m.bilibili.com":
        parsed = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit(("https", "www.bilibili.com", parsed.path, parsed.query, ""))
    return url


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dimensions_label(width, height):
    return f"{int(width)}×{int(height)}" if width and height else "原始分辨率"


def _m3u8_formats(master, base_url):
    lines = [line.strip() for line in master.splitlines() if line.strip()]
    formats = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', line.partition(":")[2]))
        target = next((x for x in lines[index + 1:] if not x.startswith("#")), None)
        if not target:
            continue
        resolution = re.match(r"(\d+)x(\d+)", attrs.get("RESOLUTION", ""))
        width, height = map(int, resolution.groups()) if resolution else (0, 0)
        media_url = urllib.parse.urljoin(base_url, target)
        rate_path = re.search(r"/(450|850|1200|2000)/", urllib.parse.urlsplit(media_url).path)
        rate = int(rate_path.group(1)) if rate_path else int(_number(attrs.get("BANDWIDTH")) / 1024)
        if rate > 2000:
            continue
        formats.append({
            "id": f"cctv-{rate}", "label": f"{_dimensions_label(width, height)} · {rate} kb/s",
            "width": width, "height": height, "filesize": None, "ext": "mp4",
            "_media_url": media_url, "_rate": rate,
        })
    return sorted(formats, key=lambda x: (x["height"], x["width"], x["_rate"]), reverse=True)


def _inspect_cctv(url, cookies, log, cancel):
    page, final_url = _text(url, cookies=cookies, cancel=cancel)
    guid_match = re.search(r'\b(?:var\s+guid\s*=|videoCenterId\s*[:=])\s*["\']([0-9a-fA-F]{32})', page)
    if not guid_match:
        raise VideoError("未找到央视视频编号，请使用具体视频的播放页面链接。")
    guid = guid_match.group(1)
    data = _json("https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?" + urllib.parse.urlencode({
        "pid": guid, "url": final_url, "idl": 32, "idlr": 32, "modifyed": "false",
    }), cookies=cookies, cancel=cancel)
    if data.get("ack") != "yes" or str(data.get("play", "1")) == "0":
        raise VideoError("央视当前未提供该视频的可播放数据。")
    master_url = (data.get("manifest") or {}).get("hls_h5e_url")
    h5e = bool(master_url)
    master_url = master_url or data.get("hls_url")
    if not master_url:
        raise VideoError("央视未返回有效的视频播放地址。")
    master, final_master = _text(master_url, cookies=cookies, referer=final_url, cancel=cancel)
    formats = _m3u8_formats(master, final_master)
    if not formats:
        raise VideoError("央视未返回可选择的清晰度，请稍后重新解析。")
    _say(log, f"央视解析完成：{len(formats)} 个视频档位。")
    return {
        "title": data.get("title") or "央视视频", "site": "央视网", "url": final_url,
        "duration": _number((data.get("video") or {}).get("totalLength")),
        "formats": formats, "_engine": "cctv", "_guid": guid, "_h5e": h5e,
    }


def _haokan_meta(url, cookies=None, cancel=None):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    video_id = (query.get("vid") or [None])[0]
    if not video_id:
        match = re.search(r"/v/(\d+)", urllib.parse.urlsplit(url).path)
        video_id = match.group(1) if match else None
    if not video_id:
        raise VideoError("未找到好看视频编号，请复制含 vid 参数的视频分享链接。")
    canonical = "https://haokan.baidu.com/v?" + urllib.parse.urlencode({"vid": video_id})
    page, _ = _text(canonical, ua=IPHONE_UA, cookies=cookies, cancel=cancel)
    marker = re.search(r"window\.__PRELOADED_STATE__\s*=\s*", page)
    if not marker:
        raise VideoError("好看视频未返回播放数据，可能需要先完成登录或访问验证。")
    try:
        state, _ = json.JSONDecoder().raw_decode(page[marker.end():].lstrip())
        meta = state["curVideoMeta"]
    except (ValueError, KeyError, TypeError):
        raise VideoError("好看视频播放数据格式发生变化，请检查更新后重试。") from None
    return meta, canonical


def _inspect_haokan(url, cookies, log, cancel):
    meta, canonical = _haokan_meta(url, cookies, cancel)
    formats = []
    for index, item in enumerate(meta.get("clarityUrl") or []):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        key = str(item.get("key") or index)
        dims = re.match(r"(\d+)\$\$(\d+)", str(item.get("vodVideoHW") or ""))
        height, width = map(int, dims.groups()) if dims else (0, 0)
        ext_info = (meta.get("videoInfoExt") or {}).get(key) or {}
        if not isinstance(ext_info, dict):
            ext_info = {}
        filesize = _number(ext_info.get("vodVideoSize")) or None
        if not filesize:
            filesize = _number(item.get("videoSize")) * 1024 * 1024 or None
        formats.append({
            "id": f"haokan-{key}", "label": f"{_dimensions_label(width, height)} · {item.get('title') or key}",
            "width": width, "height": height, "filesize": int(filesize) if filesize else None,
            "ext": "mp4", "_media_url": item["url"], "_key": key,
            "_rank": _number(item.get("rank")),
        })
    if not formats:
        raise VideoError("好看视频没有返回可用的视频清晰度。")
    formats.sort(key=lambda x: (x["height"], x["width"], x["_rank"]), reverse=True)
    _say(log, f"好看视频解析完成：{len(formats)} 个清晰度。")
    return {
        "title": meta.get("title") or "好看视频", "site": "好看视频", "url": canonical,
        "duration": _number(meta.get("duration") or meta.get("time_length")),
        "formats": formats, "_engine": "haokan",
    }


class _YtdlpLogger:
    def __init__(self, log, cookies=None):
        self.log, self.cookies = log, cookies
        self.preview = False
        self.last_error = ""

    def debug(self, message):
        # yt-dlp uses debug() for ordinary messages as well. Do not expose verbose diagnostics.
        if not str(message).startswith("[debug]"):
            self._record(message, False)

    def info(self, message):
        self._record(message, False)

    def warning(self, message):
        self._record(message, True)

    def error(self, message):
        self.last_error = str(message)
        # A public-site guest request can produce misleading account-related platform text.
        # Present our classified Chinese error instead of treating that text as account status.
        self._record(message, False)

    def _record(self, message, visible):
        lowered = str(message).lower()
        if any(part in lowered for part in ("only the preview", "only preview format", "试看")):
            self.preview = True
        if visible:
            _say(self.log, message, self.cookies)


def _ytdlp_error(error, site):
    message = str(error).lower()
    if site == "优酷" and any(x in message for x in ("-6004", "限制播放", "播放受限")):
        return VideoError("优酷当前限制了播放请求，请在官方页面确认该内容可播放并完成验证后重试。")
    if "requested format" in message:
        return VideoError("所选清晰度已失效或当前账号无法访问，请重新解析视频。")
    if "fresh cookies" in message or "login" in message or "sign in" in message:
        return VideoError(f"{site}需要有效的登录/访问验证，请在账号窗口完成验证后重新解析。")
    if any(x in message for x in ("premium", "supporter-only", "members-only", "subscriber")):
        return VideoError("该内容需要对应的会员或付费权限；请使用有权访问该内容的账号登录后重试。")
    if any(x in message for x in ("412", "429", "rate limit", "-352", "-401", "captcha", "verification", "challenge")):
        return VideoError(f"{site}要求访问验证或限制了请求频率，请在账号窗口完成验证，稍后重试。")
    if "drm" in message:
        return VideoError("该内容受到 DRM 保护，当前原型无法下载。")
    if "unsupported url" in message:
        return VideoError("当前链接类型尚不支持，请复制具体单个视频的播放或分享链接。")
    if any(x in message for x in ("404", "not available", "removed", "deleted", "unavailable")):
        return VideoError("该视频当前不可访问，可能已删除、限制地区或没有访问权限。")
    if any(x in message for x in ("timed out", "timeout", "unable to download", "network", "connection")):
        return VideoError("网络请求失败或视频数据未返回，请检查网络并完成网站验证后重试。")
    return VideoError(f"{site}解析或下载失败，请确认视频可在网站中播放，并检查引擎更新。")


def _ytdlp_module():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        raise VideoError("下载引擎未安装，请重新安装本工具。") from None


def _yt_options(logger, cancel):
    def check_hook(_):
        _check_cancel(cancel)
    return {
        "logger": logger, "quiet": True, "no_warnings": False,
        "noplaylist": True, "playlist_items": "1", "socket_timeout": 15,
        "retries": 2, "fragment_retries": 2, "extractor_retries": 1,
        "skip_unavailable_fragments": False, "ignoreerrors": False,
        "progress_hooks": [check_hook], "postprocessor_hooks": [check_hook],
        "overwrites": False, "continuedl": True,
    }


def _yt_ui_formats(raw):
    raw_formats = raw.get("formats") or ([raw] if raw.get("url") else [])
    audio_candidates = [f for f in raw_formats if f.get("vcodec") == "none" and not f.get("has_drm")]
    compatible = [f for f in audio_candidates if f.get("ext") in ("m4a", "mp4")]
    best_audio = (compatible or audio_candidates or [None])[-1]
    groups = {}
    for index, item in enumerate(raw_formats):
        if item.get("vcodec") == "none" or item.get("has_drm") or not item.get("url"):
            continue
        if "unplayable" in str(item.get("format_note", "")).lower() or item.get("vcodec") == "bytevc2":
            continue
        fid = str(item.get("format_id") or index)
        # Format IDs originate in the extractor, never in user-supplied commands.
        if not re.fullmatch(r"[\w.:-]+", fid):
            continue
        width, height = int(_number(item.get("width"))), int(_number(item.get("height")))
        codec = str(item.get("vcodec") or "")
        codec_name = "H.265" if codec.startswith(("hev", "hvc", "h265")) else "AV1" if codec.startswith("av01") else "VP9" if codec.startswith(("vp9", "vp09")) else "H.264" if codec.startswith(("avc", "h264")) else ""
        audio = best_audio if item.get("acodec") == "none" else None
        selector = f"{fid}+{audio['format_id']}" if audio else fid
        size = _number(item.get("filesize") or item.get("filesize_approx"))
        if audio:
            size += _number(audio.get("filesize") or audio.get("filesize_approx"))
        fps = _number(item.get("fps"))
        label = " · ".join(x for x in (
            _dimensions_label(width, height), f"{fps:g} 帧" if fps else "",
            codec_name, str(item.get("format_note") or "")[:45],
        ) if x)
        key = (width, height, round(fps), codec_name)
        groups[key] = {
            "id": f"yt-{index}", "label": label, "width": width, "height": height,
            "filesize": int(size) if size else None, "ext": "mp4", "_selector": selector,
            "_component_sizes": {str(row.get("format_id")): _number(row.get("filesize") or row.get("filesize_approx")) for row in ([item,audio] if audio else [item])},
            "_audio_expected": bool(audio or item.get("acodec") not in (None, "none")),
            "_quality": _number(item.get("quality")), "_tbr": _number(item.get("tbr")),
        }
    return sorted(groups.values(), key=lambda f: (f["height"], f["width"], f["_quality"], f["_tbr"]), reverse=True)


def _inspect_ytdlp(url, cookies, log, cancel):
    yt_dlp = _ytdlp_module()
    site = _site(url)
    logger = _YtdlpLogger(log, cookies)
    options = _yt_options(logger, cancel)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            # Keep yt-dlp's specialized jar (get_cookie_header/get_cookies_for_url).
            for cookie in _cookie_jar(cookies, urllib.parse.urlsplit(url).hostname or ""):
                ydl.cookiejar.set_cookie(cookie)
            _check_cancel(cancel)
            raw = ydl.extract_info(url, download=False)
    except Exception as exc:
        _check_cancel(cancel)
        raise _ytdlp_error(exc, site) from None
    _check_cancel(cancel)
    if not isinstance(raw, dict):
        raise VideoError("未获取到视频信息。")
    if raw.get("_type") in ("playlist", "multi_video"):
        raise VideoError("该链接返回了多个视频或特殊分段，请复制具体单个视频/分P的链接。")
    if raw.get("is_live"):
        raise VideoError("当前原型暂不录制直播，请使用已发布的视频链接。")
    if logger.preview:
        raise VideoError("当前账号只能访问试看片段，无法作为完整视频下载。请使用有权访问全片的账号登录后重试。")
    formats = _yt_ui_formats(raw)
    if not formats:
        raise VideoError("没有找到当前账号可下载、可正常播放的视频格式。")
    _say(log, f"{site}解析完成：{len(formats)} 个可选择的视频格式。")
    return {
        "title": raw.get("title") or "视频", "site": site,
        "url": raw.get("webpage_url") or url, "duration": _number(raw.get("duration")),
        "formats": formats, "_engine": "yt-dlp", "_source_url": url,
        "_video_id": raw.get("id"),
    }


def inspect_video(url: str, cookies: list[dict] | None = None, log=None, cancel: threading.Event | None = None) -> dict:
    """解析分享网址，返回真实可用的清晰度，不下载视频本体。"""
    try:
        _check_cancel(cancel)
        canonical = _normalize_url(url, cookies, cancel)
        site = _site(canonical)
        if site == "优酷":
            raise VideoError("优酷适配已暂时移除，当前不支持解析和下载优酷链接。")
        _say(log, f"正在解析{site}视频。")
        from site_registry import lookup, resolve
        entry = lookup(canonical)
        if entry and entry.get('handler'):
            return resolve(entry)(canonical, cookies, log, cancel)
        return _inspect_ytdlp(canonical, cookies, log, cancel)
    except VideoError:
        raise
    except Exception:
        _check_cancel(cancel)
        raise VideoError("视频解析失败，请检查链接和网络后重试。") from None


def _tool(name):
    try:
        path = find_tool(name)
    except Exception:
        path = None
    if not path or not Path(path).is_file():
        raise VideoError(f"未找到 {name} 运行组件，请重新安装本工具。")
    return str(path)


def _run_process(args, *, cancel=None, on_line=None, cwd=None, label="视频处理"):
    _check_cancel(cancel)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(x) for x in args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
        creationflags=creationflags, text=True, encoding="utf-8", errors="replace",
    )
    lines = queue.Queue()

    def read_output():
        try:
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    tail, ended = [], False
    try:
        while not ended or process.poll() is None:
            _check_cancel(cancel)
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                ended = True
                continue
            tail.append(line)
            tail = tail[-200:]
            if on_line:
                on_line(line)
        code = process.wait()
        if code:
            raise VideoError(f"{label}失败，未生成有效成品。请重新解析后重试。")
        return "\n".join(tail)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        reader.join(timeout=1)


def _probe(path, cancel=None):
    text = _run_process([
        _tool("ffprobe"), "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height,duration:format=duration,size", "-of", "json=compact=1", str(path),
    ], cancel=cancel, label="媒体信息读取")
    try:
        data = json.loads(text)
    except ValueError:
        raise VideoError("无法读取下载文件的媒体信息，文件可能不完整。") from None
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise VideoError("下载结果没有视频画面，已停止生成成品。")
    video = max(videos, key=lambda s: _number(s.get("width")) * _number(s.get("height")))
    duration = _number((data.get("format") or {}).get("duration")) or _number(video.get("duration"))
    if duration <= 0:
        raise VideoError("下载文件的时长无效，已停止生成成品。")
    return {
        "width": int(_number(video.get("width"))), "height": int(_number(video.get("height"))),
        "duration": duration, "size": Path(path).stat().st_size,
        "_has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def _verify(path, info, selected, progress, cancel):
    _progress(progress, 90, "校验视频完整性", download_percent=100)
    result = _probe(path, cancel)
    expected_duration = _number(info.get("duration"))
    if expected_duration and result["duration"] < expected_duration - max(3, expected_duration * 0.01):
        raise VideoError("下载文件时长短于预期，可能缺少分段或只有试看内容；已停止生成成品。")
    width, height = int(_number(selected.get("width"))), int(_number(selected.get("height")))
    if width and height and (result["width"] < width - 8 or result["height"] < height - 8):
        raise VideoError("下载结果的实际分辨率低于所选清晰度，已停止生成成品，请重新解析。")
    if selected.get("_audio_expected") and not result["_has_audio"]:
        raise VideoError("下载结果缺少应有的音轨，已停止生成成品。")
    duration = result["duration"]

    def verify_line(line):
        if line.startswith("out_time_us="):
            elapsed = _number(line.partition("=")[2]) / 1_000_000
            _progress(progress, 90 + min(9.5, 9.5 * elapsed / duration), "校验视频完整性")

    _run_process([
        _tool("ffmpeg"), "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode",
        "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-progress", "pipe:1",
        "-nostats", "-f", "null", "-",
    ], cancel=cancel, on_line=verify_line, label="全片解码校验")
    result.pop("_has_audio", None)
    result["verified"] = True
    return result


def _remux(source, target, progress, cancel):
    _progress(progress, 83, "无损封装为 MP4", download_percent=100)
    _run_process([
        _tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart", str(target),
    ], cancel=cancel, label="MP4 无损封装")


def _download_http(url, path, *, cookies, referer, progress, cancel):
    from http_resume import download
    try:
        download(url, path, opener=_opener(cookies, urllib.parse.urlsplit(url).hostname or ""),
                 headers={"User-Agent": IPHONE_UA, "Referer": referer, "Accept": "*/*"},
                 check=lambda: _check_cancel(cancel),
                 report=lambda percent, stage, speed, eta: _progress(progress, percent, stage, speed, eta, percent*1.25 if speed is not None and percent is not None else None, phase="download"))
    except ValueError as error:
        raise VideoError(str(error)) from None


def _download_cctv(info, selected, work, cookies, progress, log, cancel):
    # Refresh the CDN address rather than retaining a possibly expired URL from inspection.
    refreshed = _inspect_cctv(info["url"], cookies, None, cancel)
    current = next((f for f in refreshed["formats"] if f["id"] == selected["id"]), None)
    if not current:
        raise VideoError("所选央视档位已不可用，请重新解析。")
    source, target = work / "cctv.ts", work / "result.mp4"
    _progress(progress, 0, "下载央视视频")
    if refreshed.get("_h5e"):
        script = Path(resource_path("vendor", "cctv-h5e-decrypt", "build", "src", "cli", "main.js"))
        if not script.is_file():
            raise VideoError("央视高清处理组件缺失，请重新安装本工具。")

        playlist, playlist_url = _text(current["_media_url"], cookies=cookies, referer=info["url"], cancel=cancel)
        lines = [line.strip() for line in playlist.splitlines() if line.strip()]
        if any(line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:', '#EXT-X-BYTERANGE:')) for line in lines):
            raise VideoError('当前央视分片格式尚不支持安全续传，已保留任务。')
        urls = [urllib.parse.urljoin(playlist_url, line) for line in lines if not line.startswith('#')]
        import hashlib
        identity = [[urllib.parse.urlsplit(url).path for url in urls],
                    [line for line in lines if line.startswith(('#EXTINF:', '#EXT-X-MEDIA-SEQUENCE:'))]]
        signature = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        marker = work / 'playlist-id.txt'
        if marker.exists() and marker.read_text() != signature:
            raise VideoError('央视分片列表发生变化，请取消此任务后重新下载，避免拼接不同内容。')
        marker.write_text(signature)
        segment_count = len(urls)
        local = []
        for index, url in enumerate(urls):
            _check_cancel(cancel)
            piece = work / f'slice-{index:06d}.ts'
            _download_http(url, piece, cookies=cookies, referer=info['url'], progress=None, cancel=cancel)
            local.append(piece.name)
            _progress(progress, 75*(index+1)/max(1, segment_count), f'央视分片 {index+1}/{segment_count}', download_percent=100*(index+1)/max(1, segment_count))
        local_playlist = work / 'local.m3u8'
        local_playlist.write_text('#EXTM3U\n'+'\n'.join(local)+'\n', encoding='utf-8', newline='\n')

        def node_line(line):
            # CLI progress varies by version. Only relay the safe counter to the UI.
            match = re.search(r"decrypting slice\s+(\d+)", line, re.I)
            if match and segment_count:
                _progress(progress, 75 + min(5, (int(match.group(1)) + 1) / segment_count * 5), "处理已下载的央视分片")
            if "error" in line.lower() or "错误" in line:
                _say(log, "央视高清处理组件报告错误；将检查处理结果。")

        _say(log, "正在获取央视原始高清流并进行专用媒体处理。")
        _run_process([
            _tool("node"), str(script), "--local-m3u8", str(local_playlist), str(source),
        ], cwd=script.parents[3], cancel=cancel, on_line=node_line, label="央视高清下载处理")
    else:
        yt_dlp = _ytdlp_module()
        options = _yt_options(_YtdlpLogger(log, cookies), cancel)
        def native_progress(status):
            _check_cancel(cancel)
            total = status.get('total_bytes') or status.get('total_bytes_estimate') or 0
            count = status.get('downloaded_bytes') or 0
            _progress(progress, min(80, count / total * 80) if total else 0, '下载央视视频', status.get('speed'), status.get('eta'), count/total*100 if total else None, phase='download')
        options.update({'outtmpl':str(work/'cctv.%(ext)s'), 'hls_prefer_native':True,
                        'progress_hooks':[native_progress],
                        'ffmpeg_location':str(Path(_tool('ffmpeg')).parent)})
        with yt_dlp.YoutubeDL(options) as ydl:
            raw = ydl.extract_info(current['_media_url'], download=True)
            source = Path(ydl.prepare_filename(raw))
    _remux(source, target, progress, cancel)
    return target


def _download_haokan(info, selected, work, cookies, progress, log, cancel):
    refreshed = _inspect_haokan(info["url"], cookies, None, cancel)
    current = next((f for f in refreshed["formats"] if f["id"] == selected["id"]), None)
    if not current:
        raise VideoError("所选好看视频清晰度已不可用，请重新解析。")
    target = work / "result.mp4"
    _say(log, "正在下载好看视频原始媒体文件。")
    _download_http(current["_media_url"], target, cookies=cookies, referer=info["url"], progress=progress, cancel=cancel)
    return target


def _download_ytdlp(info, selected, work, cookies, progress, log, cancel):
    yt_dlp = _ytdlp_module()
    logger = _YtdlpLogger(log, cookies)
    options = _yt_options(logger, cancel)
    ffmpeg = _tool("ffmpeg")
    final_paths = []

    component_ids = selected['_selector'].split('+')
    totals = {key: selected.get('_component_sizes', {}).get(key, 0) for key in component_ids}
    counts = {}
    def download_hook(status):
        _check_cancel(cancel)
        if status.get('status') not in ('downloading','finished'): return
        key = str((status.get('info_dict') or {}).get('format_id', ''))
        if len(component_ids)==1: key=component_ids[0]
        total = status.get('total_bytes') or status.get('total_bytes_estimate') or totals.get(key,0)
        count = status.get('downloaded_bytes') or (total if status['status']=='finished' else 0)
        totals[key]=total; counts[key]=count
        known = all(totals.get(part,0)>0 for part in component_ids)
        percent = min(100,100*sum(counts.get(part,0) for part in component_ids)/sum(totals[part] for part in component_ids)) if known else None
        _progress(progress,(percent or 0)*.8,'下载音视频文件',status.get('speed'),status.get('eta'),percent,phase='download')

    def post_hook(status):
        _check_cancel(cancel)
        if status.get("status") == "started":
            _progress(progress, 81, "合并或处理音视频文件")
        if status.get("status") == "finished":
            path = (status.get("info_dict") or {}).get("filepath")
            if path:
                final_paths.append(path)

    options.update({
        "format": selected["_selector"], "ffmpeg_location": str(Path(ffmpeg).parent),
        "outtmpl": str(work / "download.%(ext)s"), "merge_output_format": "mp4",
        "progress_hooks": [download_hook], "postprocessor_hooks": [post_hook],
        "fixup": "detect_or_warn", "windowsfilenames": True,
    })
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            for cookie in _cookie_jar(cookies, urllib.parse.urlsplit(info["url"]).hostname or ""):
                ydl.cookiejar.set_cookie(cookie)
            raw = ydl.extract_info(info.get("_source_url") or info["url"], download=True)
            if isinstance(raw, dict):
                for path in [raw.get("filepath"), raw.get("_filename"), ydl.prepare_filename(raw)]:
                    if path:
                        final_paths.append(path)
    except Exception as exc:
        _check_cancel(cancel)
        raise _ytdlp_error(exc, info["site"]) from None
    if logger.preview:
        raise VideoError("网站只提供了试看内容，已停止生成完整视频成品。")
    candidates = [Path(p) for p in final_paths if Path(p).name != "result.mp4" and Path(p).is_file() and Path(p).resolve().is_relative_to(work.resolve())]
    candidates += [p for p in work.iterdir() if p.is_file() and p.name != "result.mp4" and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".flv", ".ts")]
    if not candidates:
        raise VideoError("下载引擎没有生成可用的视频文件。")
    source = max(candidates, key=lambda p: p.stat().st_size)
    target = work / "result.mp4"
    # Re-muxing also ensures a complete video+audio output and moves the MP4 index to the front.
    _remux(source, target, progress, cancel)
    return target


def _filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(title or "视频")).strip(" .")[:110].rstrip(" .")
    if not name:
        name = "视频"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def _publish(source, output, title, cancel, extension=".mp4"):
    base = _filename(title)
    for index in range(10000):
        _check_cancel(cancel)
        target = output / f"{base}{f' ({index})' if index else ''}{extension}"
        try:
            os.link(source, target)
            return target
        except FileExistsError:
            continue
        except OSError:
            # Exclusive creation is also safe on file systems without hard-link support.
            try:
                with open(target, "xb") as destination, open(source, "rb") as origin:
                    try:
                        while chunk := origin.read(1024 * 1024):
                            _check_cancel(cancel)
                            destination.write(chunk)
                    except BaseException:
                        destination.close()
                        target.unlink(missing_ok=True)
                        raise
                return target
            except FileExistsError:
                continue
    raise VideoError("同名文件过多，请更换保存文件夹。")


def _perform_download(info: dict, format_id: str, output_dir: str, cookies: list[dict] | None = None,
                   progress=None, log=None, cancel: threading.Event | None = None, work_dir=None) -> dict:
    """下载所选格式，只有通过全片校验后才发布最终文件。"""
    work = None
    try:
        _check_cancel(cancel)
        selected = next((f for f in info.get("formats", []) if f["id"] == format_id), None)
        if not selected:
            raise VideoError("请选择有效的视频清晰度，必要时重新解析。")
        output = Path(output_dir).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        if not output.is_dir():
            raise VideoError("保存位置不是文件夹。")
        work = Path(work_dir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        if work.parent != output or not work.name.startswith(".video-downloader-"):
            raise VideoError("临时目录验证失败，已停止下载。")
        _say(log, f"开始下载：{info.get('title', '视频')}。", cookies)
        engine = info.get("_engine")
        if engine == "douyin-post":
            from douyin_media import download_post
            target, extension = download_post(info, selected, work, cookies, progress, log, cancel)
            if extension == ".zip":
                import zipfile
                with zipfile.ZipFile(target) as archive:
                    if archive.testzip() is not None:
                        raise VideoError("图片压缩包校验失败。")
                result = {"width": 0, "height": 0, "duration": 0, "size": target.stat().st_size, "verified": True}
            else:
                result = _verify(target, {**info, "duration": 0}, selected, progress, cancel)
            result["path"] = str(_publish(target, output, info.get("title"), cancel, extension))
            _progress(progress, 100, "下载完成")
            return result
        from age_media import download_age
        handler = {"age": download_age, "cctv": _download_cctv, "haokan": _download_haokan, "yt-dlp": _download_ytdlp}.get(engine)
        if not handler:
            raise VideoError("视频信息已失效，请重新解析。")
        target = handler(info, selected, work, cookies, progress, log, cancel)
        _say(log, "下载已完成，正在核对分辨率、时长并解码全片。")
        result = _verify(target, info, selected, progress, cancel)
        _check_cancel(cancel)
        final = _publish(target, output, info.get("title"), cancel)
        result["path"] = str(final)
        from site_registry import record_success
        record_success(info.get("url", ""), info.get("site", "其他网站"))
        _progress(progress, 100, "下载完成")
        _say(log, "视频已通过完整性校验并保存。")
        return result
    except VideoError:
        raise
    except PermissionError:
        _check_cancel(cancel)
        raise VideoError("无法写入保存文件夹，请选择有写入权限的位置。") from None
    except Exception:
        _check_cancel(cancel)
        raise VideoError("视频下载或处理失败，未生成成品。请检查网络、磁盘空间后重新解析。") from None


def download_video(info, format_id, output_dir, cookies=None, progress=None, log=None, cancel=None, task_id=None, lock_held=False):
    import download_tasks as tasks
    task = tasks.load(task_id) if task_id else tasks.create(info, format_id, output_dir)
    from contextlib import nullcontext
    with (nullcontext() if lock_held else tasks.lock(task['id'])):
        if task['format_id'] != format_id or Path(task['output']).resolve() != Path(output_dir).expanduser().resolve():
            raise VideoError('恢复任务的画质或保存位置不一致。')
        selected = next((row for row in info.get('formats', []) if row['id'] == format_id), {})
        if any(selected.get(key) != value for key, value in task.get('format', {}).items()):
            raise VideoError('原任务的画质参数已变化，请取消此任务后重新下载。')
        if task.get('result'):
            result = task['result']
            tasks.remove(task)
            return result
        task['state'] = 'running'; tasks.save(task)
        last_saved = [0.0]
        def report(data):
            if data.get('download_percent') is not None or data.get('phase') == 'download':
                task['download_percent'] = data['download_percent']
            task['stage'] = data.get('stage', '')
            if time.monotonic()-last_saved[0] >= .75:
                tasks.save(task); last_saved[0] = time.monotonic()
            if progress: progress(data)
        try:
            result = _perform_download(info, format_id, output_dir, cookies, report, log, cancel, tasks.work_path(task))
            task.update(state='completed', result=result); tasks.save(task)
        except Exception:
            task['state'] = 'paused' if cancel is not None and cancel.is_set() else 'failed'
            tasks.save(task)
            raise
        try:
            tasks.remove(task)
        except OSError:
            _say(log, '下载已完成；缓存清理未完成，下次继续任务时将再次清理。')
        return result
