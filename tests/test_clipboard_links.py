import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clipboard_links import canonical,extract,SeenLinks

class ClipboardLinks(unittest.TestCase):
    def test_shares_are_filtered_and_episode_identity_is_not_collapsed(self):
        text='分享 https\\://v.douyin.com/95OozWtHOxI/ https://www.bilibili.com/video/BV1xx?p=1&utm_source=x https://www.bilibili.com/video/BV1xx?p=2 https://www.bilibili.com.evil.test/video/BV1xx'
        links=extract(text);self.assertEqual(len(links),3);self.assertIn('p=1',links[1]);self.assertIn('p=2',links[2])
        self.assertEqual(canonical(links[1]+'&vd_source=tracking'),links[1])
    def test_seen_links_survive_restart_without_text_or_url_storage_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=folder):
            link='https://www.bilibili.com/video/BV1xx?p=1';SeenLinks().mark([link]);fresh=SeenLinks()
            self.assertEqual(fresh.unseen([link]),[]);self.assertNotIn('bilibili',fresh.path.read_text())
            self.assertNotIn('BV1xx',fresh.path.read_text());fresh.clear();self.assertEqual(fresh.unseen([link]),[link])
