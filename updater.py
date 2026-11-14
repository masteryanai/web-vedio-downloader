"""User-triggered, checksum-verified updates from this application's Releases."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from runtime import APP_VERSION, app_data_dir, resource_path

REPO = 'masteryanai/web-vedio-downloader'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
PREFIX = f'https://github.com/{REPO}/releases/download/'
ASSETS = f'https://api.github.com/repos/{REPO}/releases/assets/'
LATEST = f'https://github.com/{REPO}/releases/latest'
TAGS = f'https://github.com/{REPO}/releases/tag/'


def request(url, asset=False, headers=None, timeout=None):
    if not (url == LATEST or url.startswith(PREFIX) or url.startswith(f'https://api.github.com/repos/{REPO}/releases')):
        raise RuntimeError('拒绝访问更新仓库之外的地址。')
    headers={**(headers or {}),'User-Agent':'WebVideoDownloader-Updater','Accept':'application/octet-stream' if asset else 'application/vnd.github+json'}
    return urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=timeout or (10 if url==API else 30))


def asset_url(asset):
    url=asset.get('browser_download_url','')
    if not (url.startswith(ASSETS) or url.startswith(PREFIX)):
        raise RuntimeError('更新附件来源不正确。')
    return url


def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('发布版本号格式不正确。')
    return tuple(map(int, value.lstrip('v').split('.')))


def get_json(url, asset=False):
    with request(url,asset) as response:
        content=response.read(2*1024*1024+1)
        if len(content)>2*1024*1024:
            raise RuntimeError('更新清单过大，已停止更新。')
        return json.loads(content)


def check_update():
    try:
        return check_api_update()
    except urllib.error.HTTPError as error:
        error.close()
        if error.code not in (403,404,429) and error.code < 500:
            raise RuntimeError('无法读取 GitHub 更新信息，请到发布页查看。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return check_public_update()


def validate_manifest(manifest, tag):
    if not isinstance(manifest, dict) or manifest.get('version') != tag.lstrip('v') or manifest.get('application') != 'WebVideoDownloader':
        raise RuntimeError('更新信息与发布版本不一致。')
    digest=manifest.get('sha256')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
        raise RuntimeError('更新包缺少有效校验信息。')
    if type(manifest.get('size')) is not int or manifest['size']<=0:
        raise RuntimeError('更新包大小信息无效。')
    if manifest.get('asset') != f'WebVideoDownloader-v{tag.lstrip("v")}-windows-x64.zip':
        raise RuntimeError('更新包名称不正确。')


def check_public_update():
    try:
        with request(LATEST) as response:
            location=response.geturl()
        match=re.fullmatch(re.escape(TAGS)+r'(v?\d+\.\d+\.\d+)', location)
        if not match:
            raise RuntimeError('公开发布页未返回本仓库的正式版本，已停止更新。')
        tag=match[1]
        if version(tag)<=version(APP_VERSION):
            return {'available':False,'message':f'已是最新版本 {APP_VERSION}（通过公开发布页检查）。'}
        # Resolve latest once, then pin manifest and ZIP to the same immutable tag.
        manifest=get_json(PREFIX+tag+'/update.json',asset=True)
        validate_manifest(manifest,tag)
        return {'available':True,'manifest':manifest,'version':manifest['version'],
                'url':PREFIX+tag+'/'+manifest['asset'],
                'notes':'GitHub API 暂不可用，已改用公开发布页检查更新，无需认证。\n发布说明：'+TAGS+tag}
    except urllib.error.HTTPError as error:
        error.close()
        if error.code==404:
            return {'available':False,'message':'公开发布页或更新清单尚未就绪，请稍后重试或到发布页手动下载。'}
        raise RuntimeError('GitHub API 和公开发布地址暂不可用，请稍后重试或到发布页手动下载。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('无法连接公开发布地址，请检查网络，或到发布页手动下载。') from None


def check_api_update():
    release = get_json(API)
    if release.get('draft') or release.get('prerelease'):
        raise RuntimeError('未找到正式发布版本。')
    if version(release['tag_name']) <= version(APP_VERSION):
        return {'available':False,'message':f'已是最新版本 {APP_VERSION}。'}
    asset = next((a for a in release.get('assets',[]) if a['name']=='update.json'), None)
    if not asset:
        raise RuntimeError('此版本未提供兼容的在线更新信息，请到发布页手动下载。')
    manifest = get_json(asset_url(asset),asset=True)
    validate_manifest(manifest,release['tag_name'])
    package = next((a for a in release['assets'] if a['name']==manifest.get('asset')), None)
    if not package or package['size'] != manifest.get('size'):
        raise RuntimeError('更新包与校验信息不一致。')
    return {'available':True,'manifest':manifest,'url':asset_url(package),
            'notes':release.get('body',''),'version':manifest['version']}


def prepare_update(info, cancel, progress):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('源码模式不能覆盖安装，请使用发布的 EXE 版本。')
    install = Path(sys.executable).resolve().parent
    if not (install/'WebVideoDownloader.exe').is_file():
        raise RuntimeError('安装目录验证失败。')
    with tempfile.TemporaryFile(dir=install.parent):
        pass
    folder = app_data_dir()/'updates'
    folder.mkdir(parents=True,exist_ok=True)
    expected = info['manifest']
    if not re.fullmatch('[0-9a-f]{64}',expected.get('sha256','')) or type(expected.get('size')) is not int or expected['size']<=0:
        raise RuntimeError('更新校验信息无效。')
    stage=folder/('package-'+expected['sha256']);stage.mkdir(exist_ok=True)
    archive=stage/'package.zip'
    from update_transfer import download
    # OS lock prevents two application instances from appending to the same partial ZIP.
    lock=stage/'transfer.lock'
    with lock.open('a+b') as guard:
        if os.name=='nt':
            import msvcrt
            guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
            try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('另一个窗口正在下载此更新，请稍后重试。') from None
        else:
            import fcntl
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise RuntimeError('另一个窗口正在下载此更新，请稍后重试。') from None
        try:
            download(archive,expected,lambda headers,timeout:request(info['url'],asset=True,headers=headers,timeout=timeout),cancel,progress)
        finally:
            if os.name=='nt':
                guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_UNLCK,1)
    progress({'percent':97,'stage':'正在检查安装包结构'})
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if 'WebVideoDownloader/WebVideoDownloader.exe' not in names:
            raise RuntimeError('更新包没有正确的主程序。')
        for name in names:
            parts = name.replace('\\','/').split('/')
            if parts[0]!='WebVideoDownloader' or '..' in parts or ':' in name:
                raise RuntimeError('更新包包含不安全路径。')
    if cancel.is_set():raise RuntimeError('已取消更新，已保留下载包。')
    plan_stage=Path(tempfile.mkdtemp(prefix='install-',dir=folder))
    plan = plan_stage/'plan.json'
    plan.write_text(json.dumps({'install_dir':str(install),'archive':str(archive),
        'sha256':expected['sha256'],'version':info['version'],'pid':os.getpid()}),encoding='utf-8')
    helper = plan_stage/'update_helper.ps1'
    shutil.copy2(resource_path('update_helper.ps1'),helper)
    return {'plan':str(plan),'helper':str(helper)}


def launch_update(prepared):
    # Process-scoped script policy; no persistent Windows policy is changed.
    subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',prepared['helper'],'-Plan',prepared['plan']],creationflags=subprocess.CREATE_NO_WINDOW)


def show_update_dialog(parent):
    from PySide6.QtCore import QObject,QThread,Signal,Slot
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPlainTextEdit,QPushButton,QProgressBar,QApplication
    import threading

    class Job(QObject):
        success=Signal(object)
        failure=Signal(str)
        progress=Signal(object)
        finished=Signal()
        def __init__(self, action):
            super().__init__();self.action=action
        @Slot()
        def run(self):
            try:self.success.emit(self.action(self.progress.emit))
            except Exception as error:self.failure.emit(str(error)[:500])
            finally:self.finished.emit()

    class Dialog(QDialog):
        def __init__(self):
            super().__init__(parent);self.setWindowTitle('检查更新');self.resize(620,440)
            self.thread=None;self.job=None;self.info=None;self.prepared=None;self.cancel=threading.Event();self.closing=False
            layout=QVBoxLayout(self);self.label=QLabel(f'当前版本 {APP_VERSION}，正在检查更新…');self.label.setWordWrap(True);layout.addWidget(self.label)
            self.notes=QPlainTextEdit();self.notes.setReadOnly(True);layout.addWidget(self.notes)
            self.bar=QProgressBar();self.bar.setRange(0,0);layout.addWidget(self.bar)
            self.install=QPushButton('下载并安装更新');self.install.setEnabled(False);self.install.clicked.connect(self.download);layout.addWidget(self.install)
            page=QPushButton('打开发布页');layout.addWidget(page)
            def open_page():
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(LATEST))
            page.clicked.connect(open_page)
            self.stop=QPushButton('关闭');self.stop.clicked.connect(self.close);layout.addWidget(self.stop)
            self.start(lambda progress:check_update(),self.checked)
        def start(self,action,success):
            self.thread=QThread(self);self.job=Job(action);self.job.moveToThread(self.thread)
            self.thread.started.connect(self.job.run);self.job.success.connect(success);self.job.failure.connect(self.failed)
            self.job.progress.connect(self.transfer_progress);self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater)
            self.thread.finished.connect(self.done_job);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()
        def transfer_progress(self,value):
            if isinstance(value,int):self.bar.setValue(value);return
            self.bar.setValue(value['percent'])
            detail=value['stage']
            if 'total' in value:
                detail+=f" · {value['bytes']/1048576:.1f} / {value['total']/1048576:.1f} MiB"
                detail+=f" · {value.get('speed',0)/1048576:.2f} MiB/s"
            self.label.setText(detail)

        def checked(self,info):
            self.info=info;self.bar.setRange(0,100);self.bar.setValue(0)
            self.label.setText(f'发现新版本 {info["version"]}。安装时会关闭当前程序并保留旧版备份。' if info['available'] else info['message'])
            self.notes.setPlainText(info.get('notes',''))
        def done_job(self):
            self.thread=None;self.job=None
            if self.prepared and not self.cancel.is_set() and not self.closing:
                try:launch_update(self.prepared)
                except Exception as error:self.failed(error);return
                parent.close();QApplication.instance().quit();return
            self.install.setEnabled(bool(self.info and self.info.get('available')))
            if self.closing:self.close()
        def download(self):
            self.install.setEnabled(False);self.stop.setText('取消并关闭');self.cancel.clear()
            self.label.setText('正在下载更新包，完成校验后将退出并更新。');self.bar.setRange(0,100)
            self.start(lambda progress:prepare_update(self.info,self.cancel,progress),self.ready)
        def ready(self,result):
            if self.cancel.is_set():return
            self.prepared=result;self.bar.setValue(100);self.label.setText('校验完成，正在退出并安装更新…')
        def failed(self,error):self.label.setText('更新未完成：'+str(error));self.bar.setRange(0,100)
        def reject(self):
            if self.thread is not None:
                self.closing=True;self.cancel.set();self.label.setText('正在停止更新请求…')
            else:super().reject()
        def closeEvent(self,event):
            if self.thread is not None:
                self.closing=True;self.cancel.set();self.label.setText('正在停止更新请求…');event.ignore()
            else:
                self.done(QDialog.DialogCode.Rejected);event.accept()
    Dialog().exec()
