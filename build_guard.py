"""Read-only preflight for disposable build outputs; never follows reparse points."""
import os
from pathlib import Path
import stat

STABLE_RUNTIME = r'D:\Project\tools\WebVideoDownloader'
RESERVED = {'data', '.wvd', '.wvd-library', '.portable-data.json', 'library.json',
            'settings.json', 'tasks.json', 'queue.json', 'sessions.json', 'library.db'}
MEDIA = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.part', '.srt', '.ass', '.mp3', '.flac'}
ROOT_PROGRAM = {'webvideodownloader.exe', '_internal', 'readme.md', 'licenses.md', '修复与回退.cmd'}


def protected_name(name):
    value=name.casefold()
    return value in RESERVED or value.startswith(('.wvd-', '.video-downloader-')) or Path(value).suffix in MEDIA


def _reject(path):
    raise RuntimeError(f'检测到用户数据、未知文件或链接，已停止构建/打包且未修改原目录：{path}。请保留该目录，使用全新的构建目录；长期运行目录为 {STABLE_RUNTIME}。')


def check_output(folder, *, packaging=False):
    """Fail before COLLECT can remove an output, including via direct spec usage."""
    root=Path(os.path.abspath(folder))
    # Inspect ancestors top down: do not lstat a child through an unchecked junction.
    for path in [*reversed(root.parents), root]:
        try: info=path.lstat()
        except FileNotFoundError: return
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0)&1024: _reject(path)
    if not stat.S_ISDIR(info.st_mode): _reject(root)
    pending=[root]
    while pending:
        parent=pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path=Path(entry.path);info=entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0)&1024: _reject(path)
                if protected_name(entry.name): _reject(path)
                if path.suffix.casefold()=='.ts' and not path.relative_to(root).as_posix().startswith('_internal/vendor/cctv-h5e-decrypt/'): _reject(path)
                if parent==root and entry.name.casefold() not in ROOT_PROGRAM:
                    # Packaging ignores update/maintenance folders, but rebuilding must not erase them.
                    if not packaging: _reject(path)
                    continue
                if stat.S_ISDIR(info.st_mode): pending.append(path)


if __name__=='__main__':
    import sys
    check_output(sys.argv[1])
