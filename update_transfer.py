"""Recoverable release transfers; the pinned manifest is the content identity."""
import hashlib
import http.client
import re
import time
import urllib.error

READ_TIMEOUT=5
ATTEMPTS=4


def download(path,expected,open_response,cancel,progress):
    total=expected['size']
    def check():
        if cancel.is_set():raise RuntimeError('已取消更新下载，已保留进度；再次更新可续传。')
    def report(count,stage,speed=0):
        progress({'percent':min(95,int(count*95/total)),'stage':stage,'bytes':count,'total':total,'speed':speed})
    check()
    for attempt in range(ATTEMPTS):
        check();offset=path.stat().st_size if path.exists() else 0
        if offset>total:path.unlink();offset=0
        if offset==total:break
        headers={'Accept-Encoding':'identity'}
        if offset:headers['Range']=f'bytes={offset}-'
        report(offset,f'正在续传更新包（第 {attempt+1} 次连接）' if offset else '正在下载更新包')
        try:
            with open_response(headers,READ_TIMEOUT) as response:
                status=response.getcode()
                if status==206:
                    match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                    if not match or tuple(map(int,match.groups()))!=(offset,total-1,total):
                        raise RuntimeError('更新包续传范围异常，已停止，当前程序未更改。')
                    mode='ab'
                elif status==200:offset=0;mode='wb'
                else:raise RuntimeError('更新服务器未返回有效文件。')
                length=response.headers.get('Content-Length')
                if length is not None and int(length)!=total-offset:
                    raise RuntimeError('更新包响应大小异常，当前程序未更改。')
                count=offset;started=time.monotonic();last_report=0
                with path.open(mode) as output:
                    # read1 returns currently available bytes instead of waiting to fill a large buffer.
                    while count<total:
                        check();chunk=response.read1(min(64*1024,total-count))
                        if not chunk:raise OSError('更新连接提前断开')
                        output.write(chunk);count+=len(chunk)
                        now=time.monotonic()
                        if now-last_report>=.1 or count==total:
                            report(count,'正在续传更新包' if offset else '正在下载更新包',(count-offset)/max(.01,now-started))
                            last_report=now
                    check()
                break
        except urllib.error.HTTPError as error:
            code=error.code;error.close()
            if code==416 and offset:
                path.unlink(missing_ok=True)
            elif code not in (408,429,500,502,503,504):
                raise RuntimeError(f'更新服务器返回 HTTP {code}，已保留进度，可稍后重试。') from None
        except (OSError,urllib.error.URLError,http.client.HTTPException):
            pass
        check()
        if attempt==ATTEMPTS-1:
            raise RuntimeError('更新下载连接中断或超时，自动重试已用尽；已保留进度，点击下载可继续。')
        report(path.stat().st_size if path.exists() else 0,f'连接中断或 {READ_TIMEOUT} 秒未收到数据，正在重试 {attempt+1}/{ATTEMPTS-1}')
        if cancel.wait(min(2, .3*(attempt+1))):check()
    check();report(total,'正在校验更新包 SHA-256')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):check();digest.update(chunk)
    if path.stat().st_size!=total or digest.hexdigest()!=expected['sha256']:
        path.unlink(missing_ok=True)
        raise RuntimeError('更新包 SHA-256 校验失败，已移除损坏缓存；请重试，当前程序未更改。')
    check()
