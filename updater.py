"""User-triggered, checksum-verified updates from this application's Releases."""
import hashlib
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
import urllib.parse
import zipfile
from runtime import APP_VERSION, app_data_dir, resource_path

REPO = 'masteryanai/web-vedio-downloader'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
PREFIX = f'https://github.com/{REPO}/releases/download/'
ASSETS = f'https://api.github.com/repos/{REPO}/releases/assets/'
_TOKEN = None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):
        redirected = super().redirect_request(request, *args, **kwargs)
        if redirected and urllib.parse.urlsplit(request.full_url).hostname != urllib.parse.urlsplit(redirected.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


def auth_file():
    return app_data_dir()/'github-update.dpapi'


def token():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = ''
        try:
            if auth_file().is_file():
                from accounts import _dpapi
                _TOKEN = _dpapi(auth_file().read_bytes(),decrypt=True).decode('utf-8')
        except (OSError,ValueError,UnicodeError):
            pass
    return _TOKEN


def set_token(value, remember=False):
    global _TOKEN
    value=value.strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{15,255}',value):
        raise RuntimeError('GitHub 访问令牌格式不正确。')
    _TOKEN=value
    if remember:
        from accounts import _dpapi
        path=auth_file();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(_dpapi(value.encode('utf-8')))
    else:
        auth_file().unlink(missing_ok=True)


def authorize_cli(remember=False):
    executable=shutil.which('gh')
    if not executable:
        raise RuntimeError('未找到 GitHub CLI，请使用访问令牌授权。')
    result=subprocess.run([executable,'auth','token','--hostname','github.com'],capture_output=True,text=True,
        timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('GitHub CLI 尚未登录，请先登录 CLI 或使用访问令牌。')
    set_token(result.stdout,remember)


def request(url, asset=False):
    if not (url.startswith(PREFIX) or url.startswith(f'https://api.github.com/repos/{REPO}/releases')):
        raise RuntimeError('拒绝访问更新仓库之外的地址。')
    headers={'User-Agent':'WebVideoDownloader-Updater','Accept':'application/octet-stream' if asset else 'application/vnd.github+json'}
    credential=token()
    if credential and url.startswith('https://api.github.com/'):
        headers['Authorization']='Bearer '+credential
    return urllib.request.build_opener(SafeRedirect()).open(urllib.request.Request(url,headers=headers),timeout=30)


def asset_url(asset):
    url=asset.get('url') or asset.get('browser_download_url','')
    if not (url.startswith(ASSETS) or url.startswith(PREFIX)):
        raise RuntimeError('更新附件来源不正确。')
    return url


def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('发布版本号格式不正确。')
    return tuple(map(int, value.lstrip('v').split('.')))


def get_json(url, asset=False):
    with request(url,asset) as response:
        return json.loads(response.read(2*1024*1024))


def check_update():
    try:
        release = get_json(API)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {'available':False, 'message':'尚未找到可访问的正式版本。私有仓库请先点击 GitHub 授权。' if not token() else '尚未找到可访问的正式版本，请确认授权有本仓库读取权限，或仓库尚未发布。'}
        raise RuntimeError('无法连接 GitHub 检查更新，请稍后重试。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('无法连接 GitHub 检查更新，请检查网络后重试。') from None
    if release.get('draft') or release.get('prerelease'):
        raise RuntimeError('未找到正式发布版本。')
    if version(release['tag_name']) <= version(APP_VERSION):
        return {'available':False,'message':f'已是最新版本 {APP_VERSION}。'}
    asset = next((a for a in release.get('assets',[]) if a['name']=='update.json'), None)
    if not asset:
        raise RuntimeError('此版本未提供兼容的在线更新信息，请到发布页手动下载。')
    manifest = get_json(asset_url(asset),asset=True)
    if manifest.get('version') != release['tag_name'].lstrip('v') or manifest.get('application') != 'WebVideoDownloader':
        raise RuntimeError('更新信息与发布版本不一致。')
    if not re.fullmatch('[0-9a-f]{64}', manifest.get('sha256','')):
        raise RuntimeError('更新包缺少有效校验信息。')
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
    stage = Path(tempfile.mkdtemp(prefix='release-',dir=folder))
    archive = stage/'package.zip'
    expected = info['manifest']
    digest = hashlib.sha256()
    count = 0
    with request(info['url'],asset=True) as response, archive.open('xb') as output:
        while True:
            if cancel.is_set():
                raise RuntimeError('已取消更新下载。')
            chunk = response.read(512*1024)
            if not chunk:
                break
            count += len(chunk)
            if count > expected['size']:
                raise RuntimeError('更新包大小异常，已停止。')
            digest.update(chunk)
            output.write(chunk)
            progress(int(count*100/expected['size']))
    if count != expected['size'] or digest.hexdigest() != expected['sha256']:
        raise RuntimeError('更新包不完整或 SHA-256 校验失败，当前程序未更改。')
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if 'WebVideoDownloader/WebVideoDownloader.exe' not in names:
            raise RuntimeError('更新包没有正确的主程序。')
        for name in names:
            parts = name.replace('\\','/').split('/')
            if parts[0]!='WebVideoDownloader' or '..' in parts or ':' in name:
                raise RuntimeError('更新包包含不安全路径。')
    plan = stage/'plan.json'
    plan.write_text(json.dumps({'install_dir':str(install),'archive':str(archive),
        'sha256':expected['sha256'],'version':info['version'],'pid':os.getpid()}),encoding='utf-8')
    helper = stage/'update_helper.ps1'
    shutil.copy2(resource_path('update_helper.ps1'),helper)
    return {'plan':str(plan),'helper':str(helper)}


def launch_update(prepared):
    # Process-scoped script policy; no persistent Windows policy is changed.
    subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',prepared['helper'],'-Plan',prepared['plan']],creationflags=subprocess.CREATE_NO_WINDOW)


def github_authorization(parent):
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QLineEdit,QCheckBox,QPushButton
    dialog=QDialog(parent);dialog.setWindowTitle('GitHub 更新授权');dialog.resize(560,300)
    layout=QVBoxLayout(dialog)
    label=QLabel('更新仓库为私有仓库，需要有权读取该仓库的 GitHub 登录。\n授权仅用于从 GitHub 读取本仓库的 Release，不授予视频会员权限。')
    label.setWordWrap(True);layout.addWidget(label)
    field=QLineEdit();field.setEchoMode(QLineEdit.EchoMode.Password);field.setPlaceholderText('可填写仅对本仓库有 Contents 读取权限的访问令牌');layout.addWidget(field)
    remember=QCheckBox('记住授权（Windows 本机加密保存）');layout.addWidget(remember)
    status=QLabel();status.setWordWrap(True);layout.addWidget(status)
    def save(cli=False):
        try:
            if cli:authorize_cli(remember.isChecked())
            else:set_token(field.text(),remember.isChecked())
            dialog.accept()
        except Exception as error:status.setText(str(error))
    cli=QPushButton('使用本机 GitHub CLI 登录');cli.clicked.connect(lambda:save(True));layout.addWidget(cli)
    manual=QPushButton('使用访问令牌');manual.clicked.connect(lambda:save(False));layout.addWidget(manual)
    def clear():
        global _TOKEN
        _TOKEN='';auth_file().unlink(missing_ok=True);status.setText('本工具保存的授权已清除。');field.clear()
    remove=QPushButton('清除本工具的 GitHub 授权');remove.clicked.connect(clear);layout.addWidget(remove)
    return dialog.exec()==QDialog.DialogCode.Accepted


def show_update_dialog(parent):
    from PySide6.QtCore import QObject,QThread,Signal,Slot
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPlainTextEdit,QPushButton,QProgressBar,QApplication
    import threading

    class Job(QObject):
        success=Signal(object)
        failure=Signal(str)
        progress=Signal(int)
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
            self.auth=QPushButton('GitHub 授权');self.auth.clicked.connect(self.authorize);layout.addWidget(self.auth)
            self.stop=QPushButton('关闭');self.stop.clicked.connect(self.close);layout.addWidget(self.stop)
            self.start(lambda progress:check_update(),self.checked)
        def start(self,action,success):
            self.auth.setEnabled(False)
            self.thread=QThread(self);self.job=Job(action);self.job.moveToThread(self.thread)
            self.thread.started.connect(self.job.run);self.job.success.connect(success);self.job.failure.connect(self.failed)
            self.job.progress.connect(self.bar.setValue);self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater)
            self.thread.finished.connect(self.done_job);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()
        def checked(self,info):
            self.info=info;self.bar.setRange(0,100);self.bar.setValue(0)
            self.label.setText(f'发现新版本 {info["version"]}。安装时会关闭当前程序并保留旧版备份。' if info['available'] else info['message'])
            self.notes.setPlainText(info.get('notes',''))
        def done_job(self):
            self.thread=None;self.job=None;self.auth.setEnabled(True)
            if self.prepared:
                try:launch_update(self.prepared)
                except Exception as error:self.failed(error);return
                parent.close();QApplication.instance().quit();return
            self.install.setEnabled(bool(self.info and self.info.get('available')))
            if self.closing:self.close()
        def authorize(self):
            if github_authorization(self):
                self.info=None;self.cancel.clear();self.label.setText('已授权，正在重新检查更新…');self.bar.setRange(0,0)
                self.start(lambda progress:check_update(),self.checked)
        def download(self):
            self.install.setEnabled(False);self.stop.setText('取消并关闭');self.cancel.clear()
            self.label.setText('正在下载更新包，完成校验后将退出并更新。');self.bar.setRange(0,100)
            self.start(lambda progress:prepare_update(self.info,self.cancel,progress),self.ready)
        def ready(self,result):self.prepared=result
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
