"""Bounded, hashed clipboard recognition; no clipboard text is persisted."""
import hashlib,json,re,time,uuid
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from runtime import app_data_dir

def canonical(url):
    from engine import _extract_url
    parsed=urlsplit(_extract_url(url));tracking={'spm_id_from','vd_source','share_source','share_medium','share_session_id','share_tag','share_times','from_spmid','unique_k','share_iid','share_link_id','share_token'}
    query=sorted((key,value) for key,value in parse_qsl(parsed.query,keep_blank_values=True) if key.lower() not in tracking and not key.lower().startswith('utm_'))
    return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path or '/',urlencode(query),''))

def extract(text):
    from site_registry import lookup
    text=text[:262144].replace('https\\://','https://').replace('http\\://','http://')
    result=[]
    for value in re.findall(r'https?://[^\s<>"\'，。！？【】（）]+',text,re.I):
        try:
            value=canonical(value)
            if lookup(value) and value not in result:result.append(value)
        except (ValueError,RuntimeError,TypeError,OSError):continue
        if len(result)>=100:break
    return result

def fingerprint(url):return hashlib.sha256(canonical(url).encode('utf-8')).hexdigest()

class SeenLinks:
    def __init__(self):self.path=app_data_dir()/'clipboard-seen.json'
    def read(self):
        try:
            if self.path.stat().st_size>262144:return {}
            value=json.loads(self.path.read_text(encoding='utf-8'))
            return {key:stamp for key,stamp in value.items() if re.fullmatch('[a-f0-9]{64}',key) and type(stamp) in (int,float)} if isinstance(value,dict) else {}
        except (OSError,ValueError,TypeError):return {}
    def unseen(self,urls):
        seen=self.read();return [url for url in urls if fingerprint(url) not in seen]
    def mark(self,urls):
        from diagnostics import file_lock
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with file_lock(self.path.parent):
            seen=self.read();seen.update({fingerprint(url):time.time() for url in urls})
            seen=dict(sorted(seen.items(),key=lambda item:item[1],reverse=True)[:1000])
            temporary=self.path.with_name('clipboard-'+uuid.uuid4().hex+'.tmp')
            try:temporary.write_text(json.dumps(seen),encoding='utf-8');temporary.replace(self.path)
            finally:temporary.unlink(missing_ok=True)
    def clear(self):self.path.unlink(missing_ok=True)
