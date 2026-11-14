import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import package_release
import update_protocol as protocol


class ReleasePackages(unittest.TestCase):
    def test_three_baselines_full_delta_and_required_full_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'dist'/'WebVideoDownloader';(source/'_internal').mkdir(parents=True)
            (root/'release_policy.json').write_text(json.dumps({'data_schema':1,'force_full':False,'force_full_reason':'','revoked_versions':['0.6.2']}))
            (source/'WebVideoDownloader.exe').write_bytes(b'new exe');(source/'_internal'/'core.dll').write_bytes(b'fixed runtime')
            (source/'_internal'/'added.dat').write_bytes(b'added')
            (source/'.maintenance').mkdir();(source/'.maintenance'/'private.txt').write_text('not a release file')
            for ver in ('0.6.1','0.6.2','0.7.0','0.8.0'):
                release=root/'release'/('v'+ver);release.mkdir(parents=True);archive=release/f'WebVideoDownloader-v{ver}-windows-x64.zip'
                with zipfile.ZipFile(archive,'w') as output:
                    output.writestr('WebVideoDownloader/WebVideoDownloader.exe',ver.encode());output.writestr('WebVideoDownloader/_internal/core.dll',b'fixed runtime' if ver!='0.7.0' else b'different runtime');output.writestr('WebVideoDownloader/_internal/obsolete.dat',b'old')
                (release/'update.json').write_text(json.dumps({'asset':archive.name,'version':ver}))
            with patch('package_release.APP_VERSION','0.9.0'):release=package_release.package(root)
            with zipfile.ZipFile(next(release.glob('*windows-x64.zip'))) as archive:self.assertFalse(any('.maintenance' in name for name in archive.namelist()))
            manifest=json.loads((release/'update.json').read_text(encoding='utf-8'));catalog=json.loads((release/'history.json').read_text(encoding='utf-8'))
            self.assertEqual(len(catalog['entries']),4);self.assertEqual(len(manifest['deltas']),2)
            self.assertIn('0.7.0',manifest['full_only_from']);self.assertFalse(catalog['entries'][-1]['rollback_allowed'])
            target=json.loads((release/manifest['inventory']['asset']).read_text(encoding='utf-8'))
            for delta in manifest['deltas']:
                baseline=protocol.zip_inventory(root/'release'/('v'+delta['from_version'])/f'WebVideoDownloader-v{delta["from_version"]}-windows-x64.zip',delta['from_version'])
                installed=root/('base'+delta['from_version']);installed.mkdir()
                archive=root/'release'/('v'+delta['from_version'])/f'WebVideoDownloader-v{delta["from_version"]}-windows-x64.zip'
                protocol.extract_verified(archive,installed,baseline)
                payload=root/('payload'+delta['from_version']);protocol.assemble_delta(installed,release/delta['asset'],payload,baseline,target)
                self.assertTrue(protocol.verify_tree(payload,target)[0]);self.assertEqual(delta['deleted'],['_internal/obsolete.dat'])
            full=root/'full';protocol.extract_verified(release/manifest['asset'],full,target);self.assertTrue(protocol.verify_tree(full,target)[0])


if __name__=='__main__':unittest.main()
