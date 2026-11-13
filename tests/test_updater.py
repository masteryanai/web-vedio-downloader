import io
import unittest
import urllib.error
from unittest.mock import patch

import updater


class PublicUpdates(unittest.TestCase):
    def test_requests_are_anonymous(self):
        with patch('updater.urllib.request.urlopen') as open_url:
            updater.request(updater.API)
            request = open_url.call_args.args[0]
            self.assertNotIn('authorization', {k.lower() for k, _ in request.header_items()})

    def test_public_asset_is_used(self):
        public = updater.PREFIX + 'v0.2.2/update.json'
        self.assertEqual(updater.asset_url({'url':updater.ASSETS+'1', 'browser_download_url':public}), public)
        with self.assertRaises(RuntimeError):
            updater.asset_url({'browser_download_url':'https://example.com/package.zip'})

    @patch('updater.APP_VERSION', '0.2.1')
    def test_new_version_and_manifest_validation(self):
        manifest = {'application':'WebVideoDownloader', 'version':'0.2.2',
                    'asset':'package.zip', 'size':100, 'sha256':'a'*64}
        release = {'tag_name':'v0.2.2', 'assets':[
            {'name':'update.json','browser_download_url':updater.PREFIX+'v0.2.2/update.json'},
            {'name':'package.zip','browser_download_url':updater.PREFIX+'v0.2.2/package.zip','size':100}]}
        with patch('updater.get_json', side_effect=[release, manifest]):
            self.assertTrue(updater.check_update()['available'])
        manifest['sha256']='invalid'
        with patch('updater.get_json', side_effect=[release, manifest]), self.assertRaises(RuntimeError):
            updater.check_update()

    def test_missing_release_and_rate_limit(self):
        for status in (404, 403, 429):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(updater.API,status,'error',{},io.BytesIO())
                with patch('updater.get_json', side_effect=error):
                    if status == 404:
                        self.assertFalse(updater.check_update()['available'])
                    else:
                        with self.assertRaisesRegex(RuntimeError,'限制'):
                            updater.check_update()


if __name__ == '__main__':
    unittest.main()
