"""Byte-range continuation with validators and strict response-range checks."""
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


def download(url, path, *, opener, headers, check, report, bytes_report=None):
    path = Path(path)
    sidecar = path.with_name(path.name + '.resume.json')
    for attempt in range(3):
        check()
        try:
            meta = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        except (ValueError, OSError):
            meta = {}
        offset = path.stat().st_size if path.exists() else 0
        if meta.get('done') and offset == meta.get('total') and offset > 0:
            if bytes_report:bytes_report(offset,offset)
            report(80, '下载文件已就绪', 0, 0)
            return
        validator = meta.get('etag') or meta.get('modified')
        request_headers = {**headers, 'Accept-Encoding':'identity'}
        if offset and validator:
            request_headers.update({'Range':f'bytes={offset}-', 'If-Range':validator})
        elif offset:
            report(0, '源站未提供可靠续传标识，重新下载当前文件', None, None)
            offset = 0
        try:
            with opener.open(urllib.request.Request(url, headers=request_headers), timeout=15) as response:
                status = response.getcode()
                length = int(response.headers.get('Content-Length') or 0)
                etag = response.headers.get('ETag', '')
                if etag.startswith('W/'):
                    etag = ''
                modified = response.headers.get('Last-Modified', '')
                if status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if (not offset or not match or int(match[1]) != offset
                            or int(match[2]) != int(match[3])-1 or int(match[3]) <= offset
                            or length and length != int(match[3])-offset):
                        raise ValueError('服务器续传范围不正确，已停止，未拼接文件。')
                    total = int(match[3])
                    if meta.get('total') and total != meta['total']:
                        raise ValueError('源文件大小已改变，请取消此任务后重新下载。')
                    if meta.get('etag') and etag != meta['etag'] or meta.get('modified') and not meta.get('etag') and modified != meta['modified']:
                        raise ValueError('源文件标识已改变，请取消此任务后重新下载。')
                    mode = 'ab'
                elif status == 200:
                    if offset:
                        report(0, '源文件变化或不支持续传，重新下载当前文件', None, None)
                    offset, total, mode = 0, length, 'wb'
                else:
                    raise ValueError('下载服务器未返回有效文件。')
                meta = {'etag':etag, 'modified':modified, 'total':total, 'done':False}
                started = time.monotonic(); count = offset; last = 0
                with path.open(mode) as output:
                    temporary = sidecar.with_suffix('.tmp')
                    temporary.write_text(json.dumps(meta)); temporary.replace(sidecar)
                    while True:
                        check()
                        chunk = response.read(256*1024)
                        if not chunk:
                            break
                        output.write(chunk); count += len(chunk)
                        if bytes_report:bytes_report(count,total or None)
                        now = time.monotonic()
                        if now-last >= .15:
                            speed = (count-offset)/max(.01, now-started)
                            report(80*count/total if total else None, '续传中' if offset else '下载中', speed,
                                   (total-count)/speed if total and speed else None)
                            last = now
                if not count or total and count != total:
                    raise OSError('incomplete response')
                if bytes_report:bytes_report(count,count)
                report(80, '下载文件已就绪', 0, 0)
                meta.update(total=count, done=True)
                temporary = sidecar.with_suffix('.tmp')
                temporary.write_text(json.dumps(meta)); temporary.replace(sidecar)
                return
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 416:
                sidecar.unlink(missing_ok=True)
                if attempt < 2:
                    continue
            if error.code in (401,403,412):
                raise ValueError('媒体地址已过期或需要访问验证，请重新解析后继续任务。') from None
            if attempt == 2:
                raise ValueError(f'下载服务器返回 HTTP {error.code}，已保留进度。') from None
        except (OSError, urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ValueError('下载中断，已保留进度，可稍后继续。') from None
