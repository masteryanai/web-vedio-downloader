from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import age_media
import engine


class AgeReuse(unittest.TestCase):
    def test_fresh_metadata_avoids_second_browser_and_expiry_refreshes_once(self):
        info={'url':'https://www.agedm.io/play/20260139/1/1','_resolved_at':time.monotonic(),
              'formats':[{'id':'source','width':1920,'height':1080,'_media_url':'https://example.com/media'}]}
        with tempfile.TemporaryDirectory() as folder:
            with patch('age_media.inspect_age',return_value=info) as inspect, patch('engine._probe',return_value={'_has_audio':True}), patch('engine._remux'):
                with patch('engine._download_http'):
                    age_media.download_age(info,info['formats'][0],Path(folder),[],None,None,None)
                    inspect.assert_not_called()
                with patch('engine._download_http',side_effect=[engine.VideoError('媒体地址已过期'),None]) as download:
                    age_media.download_age(info,info['formats'][0],Path(folder),[],None,None,None)
                    self.assertEqual(download.call_count,2);self.assertEqual(inspect.call_count,1)
                self.assertTrue(info['formats'][0]['_audio_expected'])
                inspect.reset_mock()
                with patch('engine._download_http',side_effect=engine.DownloadCancelled()):
                    with self.assertRaises(engine.DownloadCancelled):
                        age_media.download_age(info,info['formats'][0],Path(folder),[],None,None,None)
                    inspect.assert_not_called()

    def test_stale_info_is_refreshed_and_changed_quality_is_rejected(self):
        info={'url':'https://www.agedm.io/play/20260139/1/1','_resolved_at':0,
              'formats':[{'id':'source','width':1920,'height':1080,'_media_url':'https://example.com/media'}]}
        refreshed={**info,'formats':[{**info['formats'][0],'width':1280,'height':720}]}
        with tempfile.TemporaryDirectory() as folder, patch('age_media.inspect_age',return_value=refreshed) as inspect, patch('engine._download_http') as download:
            with self.assertRaisesRegex(engine.VideoError,'画质已变化'):
                age_media.download_age(info,info['formats'][0],Path(folder),[],None,None,None)
            inspect.assert_called_once();download.assert_not_called()
