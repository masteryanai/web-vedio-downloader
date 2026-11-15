"""Pinned yt-dlp native media adapter with one shared byte budget."""
import yt_dlp
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.http import HttpFD
from yt_dlp.downloader.dash import DashSegmentsFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.dependencies import Cryptodome
from media_bandwidth import MediaResponse,UnsupportedMediaTransport


class NativeHls(HlsFD):
    @classmethod
    def can_download(cls,manifest,info_dict,allow_unplayable_formats=False):
        supported=super().can_download(manifest,info_dict,allow_unplayable_formats)
        if not supported or ('#EXT-X-KEY:METHOD=AES-128' in manifest and not Cryptodome.AES):
            raise UnsupportedMediaTransport('该媒体不支持受控的原生分片下载，请选择其他画质；不会绕过全局限速调用外部网络下载器。')
        return True


class MediaYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(self,options,check):
        options={**options,'external_downloader':'native','hls_prefer_native':True}
        super().__init__(options);self.media_check=check;self.media_depth=0
    def urlopen(self,request):
        response=super().urlopen(request)
        return MediaResponse(response,self.media_check) if self.media_depth else response
    def dl(self,name,info,subtitle=False,test=False):
        # This is the native FileDownloader seam in the pinned yt-dlp version.
        # Extraction requests remain outside this scope; fragment workers share this instance.
        params={**self.params,**({'test':True,'quiet':True,'nopart':True,'overwrites':True} if test else {})}
        downloader=get_suitable_downloader(info,params,to_stdout=(name=='-'))
        if downloader is None or not issubclass(downloader,(HttpFD,HlsFD,DashSegmentsFD)):
            raise UnsupportedMediaTransport('该媒体需要外部网络下载器，暂不支持统一限速，请选择其他画质或具体视频链接。')
        if issubclass(downloader,HlsFD):downloader=NativeHls
        fd=downloader(self,params)
        if not test:
            for hook in self._progress_hooks:fd.add_progress_hook(hook)
        copied=self._copy_infodict(info)
        if copied.get('http_headers') is None:copied['http_headers']=self._calc_headers(copied)
        self.media_depth+=1
        try:return fd.download(name,copied,subtitle)
        finally:self.media_depth-=1
