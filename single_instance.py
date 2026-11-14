"""One GUI per local user-data profile; repeat launches activate the owner."""
import hashlib
import os
import time
from PySide6.QtCore import QObject,QLockFile,QTimer
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from runtime import app_data_dir


class SingleInstance(QObject):
    def __init__(self,parent=None):
        super().__init__(parent)
        folder=app_data_dir();folder.mkdir(parents=True,exist_ok=True)
        identity=os.path.normcase(str(folder.resolve()))
        self.name='WebVideoDownloader-'+hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        self.lock=QLockFile(str(folder/'instance.lock'));self.lock.setStaleLockTime(0)
        self.server=None;self.callback=None;self.pending=False;self.connections=set()
    def acquire(self):
        deadline=time.monotonic()+3
        while True:
            if self.lock.tryLock(0):
                try:
                    self.server=QLocalServer(self);self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
                    # Only the lock owner can clean an endpoint left by a dead process.
                    QLocalServer.removeServer(self.name)
                    if not self.server.listen(self.name):raise RuntimeError('无法建立程序唤起通道：'+self.server.errorString())
                    self.server.newConnection.connect(self.accept)
                    return True
                except Exception:self.close();raise
            if self.lock.error()==QLockFile.LockError.PermissionError:raise RuntimeError('无法建立单实例锁，请检查程序数据目录的写入权限。')
            if self.notify():return False
            if time.monotonic()>=deadline:raise RuntimeError('下载器已经运行，但暂时无法响应。请稍后重试；不会打开第二个实例。')
    def notify(self):
        socket=QLocalSocket()
        try:
            socket.connectToServer(self.name)
            if not socket.waitForConnected(100):return False
            if os.name=='nt':
                try:
                    import ctypes
                    pid=self.lock.getLockInfo()[0]
                    if pid>0:ctypes.windll.user32.AllowSetForegroundWindow(pid)
                except (OSError,IndexError,TypeError):pass
            socket.write(b'activate\n');socket.flush()
            if not socket.bytesAvailable() and not socket.waitForReadyRead(200):return False
            return bytes(socket.readLine(16))==b'ok\n'
        finally:socket.abort()
    def accept(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection();self.connections.add(socket)
            socket.readyRead.connect(lambda s=socket:self.read(s))
            socket.disconnected.connect(lambda s=socket:self.disconnected(s))
            expiry=QTimer(socket);expiry.setSingleShot(True);expiry.timeout.connect(socket.disconnectFromServer);expiry.start(2000)
            if socket.bytesAvailable():self.read(socket)
    def read(self,socket):
        if socket.property('handled'):return
        if socket.bytesAvailable()>64:socket.disconnectFromServer();return
        if not socket.canReadLine():return
        socket.setProperty('handled',True)
        if bytes(socket.readLine(65))==b'activate\n':
            socket.write(b'ok\n');socket.flush();self.pending=True;self.activate()
        socket.disconnectFromServer()
    def disconnected(self,socket):
        self.connections.discard(socket);socket.deleteLater()
    def attach(self,callback):self.callback=callback;self.activate()
    def activate(self):
        if self.pending and self.callback:
            self.pending=False;QTimer.singleShot(0,self.callback)
    def close(self):
        for socket in tuple(self.connections):socket.abort()
        if self.server is not None:
            self.server.close();QLocalServer.removeServer(self.name);self.server=None
            self.lock.unlock()
