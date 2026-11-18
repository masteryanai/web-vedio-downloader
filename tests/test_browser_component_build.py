import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from build_browser_component import split

class ComponentBuild(unittest.TestCase):
    def test_rebuild_timestamps_do_not_duplicate_unchanged_browser_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);full=root/'full';internal=full/'_internal';internal.mkdir(parents=True)
            (full/'WebVideoDownloaderLite.exe').write_bytes(b'build fixture');(internal/'product.json').write_text(json.dumps({'product':'lite','edition':'full'}))
            dll=internal/'PySide6/Qt6WebEngineCore.dll';dll.parent.mkdir();dll.write_bytes(b'identical runtime content')
            (dll.parent/'QtWebEngineProcess.exe').write_bytes(b'helper fixture');(dll.parent/'Qt6Core.dll').write_bytes(b'shared dependency')
            release=root/'release'
            with patch('build_browser_component.native_imports',side_effect=lambda path:['Qt6Core.dll'] if path.name=='QtWebEngineProcess.exe' else []):
                split(full,root/'base-one',release);archive=next(release.glob('*.zip'));first=hashlib.sha256(archive.read_bytes()).hexdigest()
                os.utime(dll,(1790000000,1790000000));split(full,root/'base-two',release)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),first)
            self.assertEqual((root/'base-one/_internal/PySide6/Qt6Core.dll').read_bytes(),b'shared dependency')
            import zipfile
            with zipfile.ZipFile(archive) as package:self.assertEqual(package.read('PySide6/Qt6Core.dll'),b'shared dependency')

if __name__=='__main__':unittest.main()
