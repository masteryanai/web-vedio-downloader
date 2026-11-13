"""Create the portable Windows release and the updater's verified manifest."""
import hashlib
import json
from pathlib import Path
import zipfile
from runtime import APP_VERSION


def package():
    root = Path(__file__).resolve().parent
    source = root/'dist'/'WebVideoDownloader'
    if not (source/'WebVideoDownloader.exe').is_file():
        raise RuntimeError('请先运行 build.ps1。')
    release = root/'release'/f'v{APP_VERSION}'
    release.mkdir(parents=True,exist_ok=True)
    archive = release/f'WebVideoDownloader-v{APP_VERSION}-windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                output.write(path,Path('WebVideoDownloader')/path.relative_to(source))
    digest = hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
    (release/'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n',encoding='utf-8')
    (release/'update.json').write_text(json.dumps({'application':'WebVideoDownloader',
        'version':APP_VERSION,'asset':archive.name,'size':archive.stat().st_size,
        'sha256':digest},indent=2),encoding='utf-8')
    print(f'Release ready: {archive} ({archive.stat().st_size/1024/1024:.1f} MiB)')


if __name__ == '__main__':
    package()
