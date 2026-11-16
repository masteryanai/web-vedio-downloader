"""Per-user Windows single-instance identity and memory-only mutexes."""
import ctypes
from ctypes import wintypes as W
import os


def user_id():
    if os.name!='nt':return str(os.getuid())
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);adv=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=W.HANDLE;kernel.CloseHandle.argtypes=[W.HANDLE];kernel.LocalFree.argtypes=[ctypes.c_void_p]
    adv.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,ctypes.POINTER(W.HANDLE)]
    adv.GetTokenInformation.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.LPWSTR)]
    token=W.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        size=W.DWORD();adv.GetTokenInformation(token,1,None,0,ctypes.byref(size));buffer=ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0];text=W.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid,ctypes.byref(text)):raise ctypes.WinError(ctypes.get_last_error())
        try:return text.value
        finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
    finally:kernel.CloseHandle(token)


class UserMutex:
    def __init__(self,name):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,W.BOOL,W.LPCWSTR];self.kernel.CreateMutexW.restype=W.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];self.kernel.ReleaseMutex.argtypes=[W.HANDLE];self.kernel.CloseHandle.argtypes=[W.HANDLE]
        self.handle=self.kernel.CreateMutexW(None,False,'Local\\'+name);self.owned=False
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
    def tryLock(self,_):
        result=self.kernel.WaitForSingleObject(self.handle,0)
        if result in (0,0x80):self.owned=True;return True
        if result!=0x102:raise ctypes.WinError(ctypes.get_last_error())
        return False
    def getLockInfo(self):return (0,'','')
    def unlock(self):
        if self.owned:self.kernel.ReleaseMutex(self.handle);self.owned=False
    def close(self):
        self.unlock()
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def other_downloaders():
    if os.name!='nt':return []
    class Entry(ctypes.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',ctypes.c_size_t),('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('name',W.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    kernel.Process32FirstW.argtypes=[W.HANDLE,ctypes.POINTER(Entry)];kernel.Process32NextW.argtypes=[W.HANDLE,ctypes.POINTER(Entry)];kernel.CloseHandle.argtypes=[W.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    result=[]
    try:
        entry=Entry();entry.size=ctypes.sizeof(entry);found=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while found:
            if entry.pid!=os.getpid() and entry.name.lower()=='webvideodownloader.exe':result.append(entry.pid)
            found=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:kernel.CloseHandle(handle)
    return result
