import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import release_history


class ReleaseHistory(unittest.TestCase):
    def manifest(self,ver):
        return {'application':'WebVideoDownloader','version':ver,'asset':f'WebVideoDownloader-v{ver}-windows-x64.zip','sha256':hashlib.sha256(b'fixture').hexdigest(),'size':7}
    def test_fresh_publisher_fetches_previous_stable_package_and_notes(self):
        ver='0.8.0';manifest=self.manifest(ver)
        listing=[{'tag_name':'v0.8.0','body':'# Old notes','published_at':'2026-11-14','draft':False,'prerelease':False},{'tag_name':'v0.8.1','draft':True}]
        class Response(io.BytesIO):
            headers={'Content-Length':'7'}
            def getcode(self):return 200
        with tempfile.TemporaryDirectory() as folder,patch('updater.get_json',side_effect=[listing,manifest]),patch('updater.request',return_value=Response(b'fixture')):
            release_history.ensure_history(folder,'0.9.0');cache=Path(folder)/'release/v0.8.0'
            self.assertEqual((cache/manifest['asset']).read_bytes(),b'fixture');self.assertEqual((cache/'notes.md').read_text(encoding='utf-8'),'# Old notes')
            self.assertEqual(json.loads((cache/'update.json').read_text(encoding='utf-8'))['published_at'],'2026-11-14')
    def test_offline_publisher_uses_three_verified_cache_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            for ver in ('0.6.2','0.7.0','0.8.0'):
                cache=Path(folder)/'release'/('v'+ver);cache.mkdir(parents=True);manifest=self.manifest(ver)
                (cache/manifest['asset']).write_bytes(b'fixture');(cache/'update.json').write_text(json.dumps(manifest))
            with patch('updater.get_json',side_effect=OSError('offline')):release_history.ensure_history(folder,'0.9.0')
            (cache/manifest['asset']).write_bytes(b'bad')
            with patch('updater.get_json',side_effect=OSError('offline')),self.assertRaises(RuntimeError):release_history.ensure_history(folder,'0.9.0')
    def test_unavailable_history_fails_instead_of_silently_publishing_empty_history(self):
        with tempfile.TemporaryDirectory() as folder,patch('updater.get_json',side_effect=OSError('offline')),self.assertRaises(RuntimeError):release_history.ensure_history(folder,'0.9.0')


if __name__=='__main__':unittest.main()
