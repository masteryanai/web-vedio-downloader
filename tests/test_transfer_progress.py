import http.cookiejar
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import engine


class TransferProgress(unittest.TestCase):
    def test_video_and_audio_share_one_byte_percentage(self):
        events=[]
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder);source=work/'download.mp4';source.write_bytes(b'fixture')
            class YDL:
                def __init__(self,options):self.options=options;self.cookiejar=http.cookiejar.CookieJar()
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def extract_info(self,*args,**kwargs):
                    hook=self.options['progress_hooks'][0]
                    for component,count,total,state in [('v',900,900,'finished'),('a',50,100,'downloading'),('a',100,100,'finished')]:
                        hook({'status':state,'downloaded_bytes':count,'total_bytes':total,'info_dict':{'format_id':component}})
                    self.options['postprocessor_hooks'][0]({'status':'started'})
                    return {'filepath':str(source)}
                def prepare_filename(self,raw):return str(source)
            selected={'_selector':'v+a','_component_sizes':{'v':900,'a':100}}
            info={'url':'https://www.bilibili.com/video/fixture','site':'B站'}
            with patch('engine._ytdlp_module',return_value=types.SimpleNamespace(YoutubeDL=YDL)),patch('engine._remux'),patch('engine._tool',return_value='ffmpeg.exe'):
                engine._download_ytdlp(info,selected,work,[],events.append,None,None)
            percents=[event['download_percent'] for event in events if event['download_percent'] is not None]
            self.assertEqual(percents,[90,95,100])
            self.assertEqual(events[-1]['stage'],'合并或处理音视频文件')
            self.assertIsNone(events[0]['total_bytes'])
            self.assertEqual(events[-2]['total_bytes'],1000)
            self.assertEqual(events[-2]['downloaded_bytes'],1000)
            self.assertIsNone(events[-2]['phase'])
