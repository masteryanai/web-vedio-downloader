import hashlib,json,os,tempfile,threading,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import optional_components as components

class OptionalComponents(unittest.TestCase):
    def fixture(self,root,malicious=False):
        archive=root/'browser.zip';name='../data/settings.json' if malicious else 'PySide6/Qt6WebEngineCore.dll';body=b'verified component fixture'
        with zipfile.ZipFile(archive,'w') as output:output.writestr(name,body)
        return archive,{'size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
             'asset':'browser.zip','tag':'v'+components.APP_VERSION,'files':[{'path':name,'size':len(body),'sha256':hashlib.sha256(body).hexdigest()}]}
    def test_verified_component_stays_local_and_changed_original_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive,value=self.fixture(root)
            with patch('optional_components.program_dir',return_value=root):
                installed=components.install_from_archive(archive,value,threading.Event());file=installed/'PySide6/Qt6WebEngineCore.dll'
                self.assertEqual(file.read_bytes(),b'verified component fixture')
                self.assertEqual(components.install_from_archive(archive,value,threading.Event()),installed)
                file.write_bytes(b'changed original')
                with self.assertRaises(ValueError):components.install_from_archive(archive,value,threading.Event())
                self.assertEqual(file.read_bytes(),b'changed original')
    def test_traversal_cancellation_and_corrupt_package_publish_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive,value=self.fixture(root,True)
            with patch('optional_components.program_dir',return_value=root):
                with self.assertRaises(ValueError):components.install_from_archive(archive,value,threading.Event())
                self.assertFalse((root/'data/settings.json').exists())
                archive,value=self.fixture(root);cancel=threading.Event();cancel.set()
                with self.assertRaises(RuntimeError):components.install_from_archive(archive,value,cancel)
                self.assertFalse(components.folder(value).exists())
                archive.write_bytes(b'broken')
                with self.assertRaises(ValueError):components.install_from_archive(archive,value,threading.Event())
                self.assertFalse(components.folder(value).exists())
    def test_foreign_component_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'components.json';path.write_text(json.dumps({'application':'OtherProduct','version':components.APP_VERSION}))
            with patch('optional_components.resource_path',return_value=path):
                with self.assertRaises(ValueError):components.descriptor()
