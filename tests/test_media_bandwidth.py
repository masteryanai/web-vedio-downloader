import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from media_bandwidth import Budget,shared,UnsupportedMediaTransport
import engine
import preferences
from media_ytdlp import MediaYoutubeDL

class BandwidthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,LOCALAPPDATA=self.temp.name,WVD_DATA_DIR=str(self.root/'WebVideoDownloaderPrototype'));self.env.start()
        preferences.update(download_limit_mib=0)
    def tearDown(self):
        preferences.update(download_limit_mib=0);self.env.stop();self.temp.cleanup()
    def test_aggregate_fair_budget(self):
        budget=Budget();budget.configure(128*1024);barrier=threading.Barrier(3);counts=[0]*3
        def run(index):
            stream=io.BytesIO(b'x'*(128*1024));barrier.wait()
            while chunk:=budget.read(stream,65536,lambda:None):counts[index]+=len(chunk)
        start=time.monotonic()
        with ThreadPoolExecutor(3) as pool:list(pool.map(run,range(3)))
        elapsed=time.monotonic()-start
        self.assertEqual(counts,[128*1024]*3);self.assertGreater(elapsed,2.8);self.assertLess(elapsed,5)
    def test_live_change_and_cancel_waiter(self):
        budget=Budget();budget.configure(1);cancel=threading.Event()
        def check():
            if cancel.is_set():raise engine.DownloadCancelled()
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(budget.read,io.BytesIO(b'a'),1,check);time.sleep(.05);start=time.monotonic();cancel.set()
            with self.assertRaises(engine.DownloadCancelled):future.result(1)
            self.assertLess(time.monotonic()-start,.3)
            cancel.clear();future=pool.submit(budget.read,io.BytesIO(b'b'),1,check)
            time.sleep(.03);budget.configure(0);self.assertEqual(future.result(.5),b'b')
        self.assertFalse(budget.waiters)
    def test_decrease_increase_and_refund(self):
        budget=Budget();budget.configure(1024*1024);budget.configure(10*1024)
        start=time.monotonic();stream=io.BytesIO(b'x'*2048)
        while budget.read(stream,65536,lambda:None):pass
        self.assertGreater(time.monotonic()-start,.18)
        budget.configure(1024*1024);start=time.monotonic()
        for _ in range(3):budget.read(io.BytesIO(),65536,lambda:None)
        self.assertLess(time.monotonic()-start,.2)
    def test_persistence_and_invalid_settings(self):
        self.assertEqual(preferences.load()['download_limit_mib'],0)
        for value in (True,-1,.001,float('nan'),float('inf'),'5',102401):
            with self.assertRaises(ValueError):preferences.update(download_limit_mib=value)
        preferences.update(download_limit_mib=1.25)
        self.assertEqual(preferences.load()['download_limit_mib'],1.25)
        self.assertEqual(shared().rate,1.25*1024**2)
        preferences.update(parallel_downloads=3)
        self.assertEqual(preferences.load()['download_limit_mib'],1.25)

class NetworkBandwidthTests(unittest.TestCase):
    def setUp(self):
        BandwidthTests.setUp(self);self.body=b'actual network bytes'*8192;owner=self;self.ranges=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/list.m3u8':
                    data=b'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n/a.ts\n#EXTINF:1,\n/b.ts\n#EXT-X-ENDLIST\n'
                else:data=owner.body
                offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]);owner.ranges.append(offset)
                self.send_response(206 if offset else 200);self.send_header('Content-Length',str(len(data)-offset));self.send_header('ETag','"fixture"')
                if offset:self.send_header('Content-Range',f'bytes {offset}-{len(data)-1}/{len(data)}')
                self.end_headers()
                try:self.wfile.write(data[offset:])
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();BandwidthTests.tearDown(self)
    def native(self,path,event=None,progress=None):
        engine._download_http(self.url+'/file',path,cookies=[],referer=self.url,progress=progress or (lambda *_:None),cancel=event or threading.Event())
    def ytdlp(self,name,protocol='http'):
        info={'id':'fixture','title':'fixture','url':self.url+('/list.m3u8' if protocol=='m3u8_native' else '/file'),'ext':'mp4','protocol':protocol,'extractor_key':'Generic'}
        if protocol=='http_dash_segments':info['fragments']=[{'url':self.url+'/a.ts'},{'url':self.url+'/b.ts'}]
        with MediaYoutubeDL({'quiet':True,'noprogress':True,'cachedir':False,'concurrent_fragment_downloads':2},lambda:None) as ydl:
            result=ydl.dl(str(self.root/name),info)
            self.assertTrue(result[0])
        return (self.root/name).read_bytes()
    def test_mixed_http_ytdlp_share_one_limit(self):
        preferences.update(download_limit_mib=.2);start=time.monotonic()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.native,self.root/'native');b=pool.submit(self.ytdlp,'ydl');a.result(10);data=b.result(10)
        elapsed=time.monotonic()-start
        self.assertEqual(data,self.body);self.assertEqual((self.root/'native').read_bytes(),self.body)
        self.assertGreater(elapsed,1.4);self.assertLess(elapsed,4)
    def test_native_hls_and_dash_fragments(self):
        for protocol in ('m3u8_native','http_dash_segments'):
            preferences.update(download_limit_mib=.3);start=time.monotonic()
            self.assertEqual(self.ytdlp(protocol,protocol),self.body*2)
            self.assertGreater(time.monotonic()-start,.9)
    def test_cancel_and_range_resume_under_limit(self):
        preferences.update(download_limit_mib=.05);event=threading.Event();path=self.root/'resume'
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.native,path,event);time.sleep(.25);event.set()
            with self.assertRaises(engine.DownloadCancelled):future.result(1)
        offset=path.stat().st_size;self.assertGreater(offset,0);self.assertLess(offset,len(self.body))
        preferences.update(download_limit_mib=0);self.native(path)
        self.assertIn(offset,self.ranges);self.assertEqual(path.read_bytes(),self.body)
    def test_live_adjustment_on_existing_http_task(self):
        preferences.update(download_limit_mib=.15);path=self.root/'dynamic';event=threading.Event()
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.native,path,event)
            time.sleep(.2);preferences.update(download_limit_mib=.02)
            before=path.stat().st_size;time.sleep(.4);after=path.stat().st_size
            self.assertLess(after-before,20000);self.assertFalse(future.done())
            preferences.update(download_limit_mib=1);start=time.monotonic();future.result(2)
            self.assertLess(time.monotonic()-start,1)
        self.assertEqual(path.read_bytes(),self.body)
    def test_ytdlp_wait_cancellation(self):
        preferences.update(download_limit_mib=.01);event=threading.Event()
        def run():
            with MediaYoutubeDL({'quiet':True,'noprogress':True},lambda:engine._check_cancel(event)) as ydl:
                return ydl.dl(str(self.root/'cancel'),{'id':'cancel','url':self.url+'/file','ext':'mp4','protocol':'http'})
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(run);time.sleep(.2);event.set();start=time.monotonic()
            with self.assertRaises(engine.DownloadCancelled):future.result(1)
            self.assertLess(time.monotonic()-start,.4)
    def test_external_network_fallback_rejected(self):
        from media_ytdlp import NativeHls
        with MediaYoutubeDL({'quiet':True},lambda:None) as ydl:
            with self.assertRaisesRegex(ValueError,'外部网络下载器'):
                ydl.dl(str(self.root/'unsupported'),{'url':'rtmp://127.0.0.1/video','protocol':'rtmp','ext':'flv'})
        self.assertIn('原生分片',str(engine._ytdlp_error(UnsupportedMediaTransport('原生分片暂不支持'),'央视网')))
        with self.assertRaises(ValueError):NativeHls.can_download('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="x"',{'url':self.url})
    def test_updater_exempt(self):
        import update_transfer
        preferences.update(download_limit_mib=.01);start=time.monotonic()
        update_transfer.download(self.root/'update',{'size':len(self.body),'sha256':hashlib.sha256(self.body).hexdigest()},lambda headers,timeout:urllib.request.urlopen(urllib.request.Request(self.url+'/update',headers=headers),timeout=timeout),threading.Event(),lambda *_:None)
        self.assertLess(time.monotonic()-start,2);self.assertEqual((self.root/'update').read_bytes(),self.body)

if __name__=='__main__':unittest.main()
