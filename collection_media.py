"""Website-specific collection discovery; playback lines are never seasons."""
from html.parser import HTMLParser
import html
import json
import re
from urllib.parse import urlsplit,urljoin,parse_qs,urlencode

class Page(HTMLParser):
    def __init__(self):super().__init__();self.meta={};self.links=[];self.link=None;self.title='';self.in_title=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta':self.meta[a.get('property',a.get('name',''))]=a.get('content','')
        if tag=='a' and a.get('href'):self.link=[a['href'],'']
        if tag=='title':self.in_title=True
    def handle_data(self,data):
        if self.link:self.link[1]+=data
        if self.in_title:self.title+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.link:self.links.append(self.link);self.link=None
        if tag=='title':self.in_title=False

def season_name(title):
    m=re.search(r'第\s*[零〇一二三四五六七八九十百\d]+\s*季|Season\s*\d+',title,re.I)
    return m.group().replace(' ','') if m else ''

def age_page(text,url):
    page=Page();page.feed(text);parts=urlsplit(url);match=re.search(r'/play/(\d+)/(\d+)/(\d+)',parts.path)
    detail=re.fullmatch(r'/detail/(\d+)/?',parts.path)
    if not match and not detail:raise ValueError('请使用AGE作品详情页或单集播放页。')
    work,line,current=match.groups() if match else (detail[1],None,None)
    routes={}
    for href,label in page.links:
        address=urljoin(url,href);m=re.fullmatch(r'/play/'+work+r'/(\d+)/(\d+)/?',urlsplit(address).path)
        if m:
            rows=routes.setdefault(m[1],[])
            if not any(e['key']==m[2] for e in rows):rows.append({'key':m[2],'title':label.strip() or '第 '+m[2]+' 集','url':address})
    line=line or next(iter(routes),None)
    entries=routes.get(line,[])
    if not entries and current:entries=[{'key':current,'title':'第 '+current+' 集','url':url}]
    if not entries:raise ValueError('详情页未返回可识别选集，请完成访问验证后重试。')
    title=page.meta.get('og:title') or page.title
    title=re.sub(r'\s*-\s*(?:AGE动漫|在线播放).*$','',title).strip()
    return {'site':'AGE动漫','key':work,'url':entries[0]['url'] if detail else url,'title':title,'season':season_name(title),'entries':entries,
            'cover':urljoin(url,page.meta.get('og:image','')) if page.meta.get('og:image') else '',
            'banner':page.meta.get('og:image:secure_url',''),'kind':'video','capability':'season','line':line,'variant':line,'routes':routes}

def bilibili_data(data,url):
    body=data.get('data') or data.get('result') or {};entries=[];season='';key='';title='';cover='';banner=''
    if body.get('episodes'):
        title=body.get('title','B站合集');key='season:'+str(body.get('season_id'));season=season_name(title)
        entries=[{'key':str(e['id']),'title':'第 '+str(e.get('title',''))+' 集'+(' · '+e['long_title'] if e.get('long_title') else ''),
                  'url':e.get('link') or 'https://www.bilibili.com/bangumi/play/ep'+str(e['id'])} for e in body['episodes'] if e.get('id')]
        cover=body.get('cover','');banner=body.get('bkg_cover','')
    elif body.get('ugc_season'):
        group=body['ugc_season'];title=group.get('title') or body.get('title');key='ugc:'+str(group['id']);season=season_name(title);cover=group.get('cover') or body.get('pic','')
        for section in group.get('sections',[]):
            for e in section.get('episodes',[]):
                bvid=e.get('bvid') or e.get('arc',{}).get('bvid')
                if bvid:entries.append({'key':bvid,'title':e.get('title') or bvid,'url':'https://www.bilibili.com/video/'+bvid})
    elif body.get('pages'):
        bvid=body['bvid'];title=body.get('title','B站视频');key='video:'+bvid;cover=body.get('pic','')
        entries=[{'key':str(e['cid']),'title':e.get('part') or '第 '+str(e['page'])+' P','url':'https://www.bilibili.com/video/'+bvid+'?p='+str(e['page'])} for e in body['pages']]
    else:raise ValueError('网站未返回可识别的选集；请使用单集链接，或完成账号验证后重试。')
    if not entries:raise ValueError('网站未返回选集，请完成访问验证后重试。')
    return {'site':'B站','key':key,'url':url,'title':title,'season':season,'entries':entries,'cover':cover,'banner':banner,'kind':'video','capability':'season' if len(entries)>1 else 'single'}

def single(info):
    from urllib.parse import urlunsplit
    url=info['url'];parts=urlsplit(url)
    key=info.get('_video_id') or urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,''))
    return {'site':info['site'],'key':str(key),'url':url,'title':info['title'],'season':'','kind':'images' if info.get('_kind')=='images' else 'video',
            'cover':info.get('thumbnail',''),'banner':info.get('banner',''),'capability':'single',
            'entries':[{'key':str(key),'url':url,'title':info['title']}]}

def discover(text,cookies=None,cancel=None):
    import engine
    url=engine._extract_url(text);host=urlsplit(url).hostname or ''
    if host=='agedm.io' or host.endswith('.agedm.io'):
        body,final=engine._text(url,cookies=cookies,cancel=cancel);return age_page(body,final)
    if host=='b23.tv':
        _,url=engine._text(url,cookies=cookies,cancel=cancel);host=urlsplit(url).hostname or ''
    if host=='bilibili.com' or host.endswith('.bilibili.com'):
        episode=re.search(r'/bangumi/play/(ep|ss)(\d+)',url)
        media=re.search(r'/bangumi/media/md(\d+)',url)
        bvid=re.search(r'/video/(BV[0-9A-Za-z]+)',url)
        media_info={}
        if media:
            body,_=engine._text('https://api.bilibili.com/pgc/review/user?media_id='+media[1],cookies=cookies,cancel=cancel,referer=url)
            media_info=json.loads(body).get('result',{}).get('media',{})
            if not media_info.get('season_id'):raise ValueError('B站没有返回该媒体的季信息，请登录或重试。')
            api='https://api.bilibili.com/pgc/view/web/season?season_id='+str(media_info['season_id'])
        elif episode:api='https://api.bilibili.com/pgc/view/web/season?'+('ep_id=' if episode[1]=='ep' else 'season_id=')+episode[2]
        elif bvid:api='https://api.bilibili.com/x/web-interface/view?bvid='+bvid[1]
        else:return None
        body,_=engine._text(api,cookies=cookies,cancel=cancel,referer=url);group=bilibili_data(json.loads(body),url)
        if media_info.get('horizontal_picture'):group['banner']=media_info['horizontal_picture']
        return group
    return None

def inspect_collection(text,cookies=None,log=None,cancel=None):
    import engine
    try:group=discover(text,cookies,cancel)
    except Exception:
        engine._check_cancel(cancel);group=None
        if log:log('合集检查暂不可用，继续尝试原有单集解析。')
    if group:
        try:info=engine.inspect_video(group['entries'][0]['url'],cookies,log,cancel)
        except engine.VideoError:
            engine._check_cancel(cancel)
            if len(group['entries'])<2:raise
            if log:log('合集已识别，首集媒体信息暂不可用；加入任务后各集独立解析。')
            info={'site':group['site'],'url':group['entries'][0]['url'],'title':group['title'],'duration':0,'formats':[],'_metadata_only':True}
        info['_collection']=group;return info
    info=engine.inspect_video(text,cookies,log,cancel);info['_collection']=single(info);return info
