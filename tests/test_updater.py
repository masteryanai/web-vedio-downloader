import io
import json
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
                    'asset':'WebVideoDownloader-v0.2.2-windows-x64.zip', 'size':100, 'sha256':'a'*64}
        release = {'tag_name':'v0.2.2', 'assets':[
            {'name':'update.json','browser_download_url':updater.PREFIX+'v0.2.2/update.json'},
            {'name':'WebVideoDownloader-v0.2.2-windows-x64.zip','browser_download_url':updater.PREFIX+'v0.2.2/WebVideoDownloader-v0.2.2-windows-x64.zip','size':100}]}
        with patch('updater.get_json', side_effect=[release, manifest]):
            self.assertTrue(updater.check_update()['available'])
        manifest['sha256']='invalid'
        with patch('updater.get_json', side_effect=[release, manifest]), self.assertRaises(RuntimeError):
            updater.check_update()

    @patch('updater.APP_VERSION', '0.4.1')
    def test_api_failure_uses_public_pinned_manifest(self):
        tag='v0.4.2'
        manifest={'application':'WebVideoDownloader','version':'0.4.2',
                  'asset':'WebVideoDownloader-v0.4.2-windows-x64.zip','size':100,'sha256':'a'*64}
        calls=[]
        for failure in (403,429,500,'network'):
            calls.clear()
            def request(url,asset=False):
                calls.append(url)
                if url==updater.API:
                    if failure=='network':raise urllib.error.URLError('offline API')
                    raise urllib.error.HTTPError(url,failure,'error',{},io.BytesIO())
                if url.endswith('/releases/latest'):
                    response=io.BytesIO();response.geturl=lambda:'https://github.com/'+updater.REPO+'/releases/tag/'+tag
                    return response
                self.assertEqual(url,updater.PREFIX+tag+'/update.json')
                return io.BytesIO(json.dumps(manifest).encode())
            with self.subTest(failure=failure),patch('updater.request',side_effect=request):
                info=updater.check_update()
                self.assertTrue(info['available'])
                self.assertEqual(info['url'],updater.PREFIX+tag+'/'+manifest['asset'])
                self.assertEqual(info['manifest'],manifest)
                self.assertEqual(len(calls),3)

    def test_missing_release_and_rate_limit(self):
        for status in (404, 403):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(updater.API,status,'error',{},io.BytesIO())
                with patch('updater.request', side_effect=error):
                    if status == 404:
                        self.assertFalse(updater.check_update()['available'])
                    else:
                        with self.assertRaisesRegex(RuntimeError,'发布'):
                            updater.check_update()

    @patch('updater.APP_VERSION', '0.4.1')
    def test_public_fallback_rejects_wrong_redirects_and_bad_manifests(self):
        manifest={'application':'WebVideoDownloader','version':'0.4.2','size':100,
                  'asset':'WebVideoDownloader-v0.4.2-windows-x64.zip','sha256':'a'*64}
        def responses(location,data):
            def request(url,asset=False):
                if url==updater.LATEST:
                    response=io.BytesIO();response.geturl=lambda:location;return response
                return io.BytesIO(json.dumps(data).encode())
            return request
        for location in ('https://github.com/other/repo/releases/tag/v0.4.2',updater.TAGS+'v0.4.2-rc.1',updater.TAGS+'v0.4.2?extra=1'):
            with self.subTest(location=location),patch('updater.request',side_effect=responses(location,manifest)):
                with self.assertRaises(RuntimeError):updater.check_public_update()
        for invalid in ({'sha256':'invalid'},{'version':'0.4.3'},{'asset':'../escape.zip'},{'size':True},{'size':0}):
            with self.subTest(invalid=invalid),patch('updater.request',side_effect=responses(updater.TAGS+'v0.4.2',{**manifest,**invalid})):
                with self.assertRaises(RuntimeError):updater.check_public_update()

    @patch('updater.APP_VERSION', '0.4.2')
    def test_public_latest_does_not_downgrade(self):
        response=io.BytesIO();response.geturl=lambda:updater.TAGS+'v0.4.1'
        with patch('updater.request',return_value=response) as request:
            self.assertFalse(updater.check_public_update()['available'])
            self.assertEqual(request.call_count,1)

    def test_oversized_json_is_rejected(self):
        with patch('updater.request',return_value=io.BytesIO(b' '* (2*1024*1024+1))):
            with self.assertRaisesRegex(RuntimeError,'过大'):updater.get_json(updater.API)


if __name__ == '__main__':
    unittest.main()
