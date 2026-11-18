import hashlib,json,os,tempfile,unittest
from pathlib import Path
from build_browser_component import split

class ComponentBuild(unittest.TestCase):
    def test_rebuild_timestamps_do_not_duplicate_unchanged_browser_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);full=root/'full';internal=full/'_internal';internal.mkdir(parents=True)
            (full/'WebVideoDownloaderLite.exe').write_bytes(b'build fixture');(internal/'product.json').write_text(json.dumps({'product':'lite','edition':'full'}))
            dll=internal/'PySide6/Qt6WebEngineCore.dll';dll.parent.mkdir();dll.write_bytes(b'identical runtime content')
            release=root/'release';split(full,root/'base-one',release);archive=next(release.glob('*.zip'));first=hashlib.sha256(archive.read_bytes()).hexdigest()
            os.utime(dll,(1790000000,1790000000));split(full,root/'base-two',release)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),first)

if __name__=='__main__':unittest.main()
