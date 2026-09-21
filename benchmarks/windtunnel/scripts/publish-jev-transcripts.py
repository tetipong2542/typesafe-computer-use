#!/usr/bin/env python3
"""Export redacted Jev traces from the two frozen cohort folders. No provider calls."""
import argparse, base64, gzip, hashlib, json, pathlib, re, subprocess
from urllib.parse import quote, quote_plus

if not __debug__: raise RuntimeError("Do not run publication checks with Python optimization enabled")

ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'results/2026-09-18-jev-mercury'
SECRET_KEY = re.compile(r'^(?:password|passwd|passphrase|authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?(?:token|id)|csrf[_-]?token|secret|client[_-]?secret)$', re.I)
TOKEN = re.compile(r'\b(?:sk-(?:proj-|svcacct-|ant-api\d\d-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{24,}|xox[baprs]-[A-Za-z0-9-]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b')
PERSONAL_PATH = re.compile(r'(?<![A-Za-z0-9_./-])(?:file://)?/(?:Users|home)/[^\s"\'<>\\]+|(?<![A-Za-z0-9_./-])/(?:private/)?tmp/[^\s"\'<>\\]+')
PRIVATE_IP = re.compile(r'(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])')
PASSWORD = re.compile(r'\bwith\s+password(?:\s*[:=]\s*|\s+(?:is\s+)?)[`"\']?([^\s,`"\']+)', re.I)

def embedded(s):
    if s.lstrip().startswith(('{','[')):
        try: return json.loads(s)
        except (ValueError,RecursionError): pass
    return None

def secret_field(key,value):
    # A tool schema describes a password field; it does not contain its value.
    schema=isinstance(value,dict) and value.get("type") in ("string","number","integer","boolean","array","object","null") and set(value)<= {"type","description","title","format","minLength","maxLength","pattern","nullable"}
    return bool(SECRET_KEY.fullmatch(key)) and not schema

def collect(value, secrets, sensitive=False):
    if isinstance(value,dict):
        for key,item in value.items():collect(item,secrets,sensitive or secret_field(key,item))
    elif isinstance(value,list):
        for item in value:collect(item,secrets,sensitive)
    elif isinstance(value,str):
        if sensitive and value and value!='[redacted]':
            if len(value)<4:raise ValueError('Short sensitive value requires manual redaction before export')
            secrets.add(value)
        nested=embedded(value)
        if nested is not None:collect(nested,secrets,sensitive)

class Redactor:
    def __init__(self,secrets):
        values={s for s in secrets if s and s!='[redacted]'}
        self.secrets=sorted({encoded for s in values for encoded in (s,quote(s,safe=''),quote_plus(s,safe=''),base64.b64encode(s.encode()).decode())},key=len,reverse=True)
        self.redactions=0
    def text(self,s):
        original=s
        for secret in self.secrets:s=s.replace(secret,'[redacted]')
        s=TOKEN.sub('[redacted token]',s)
        s=re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*','Bearer [redacted]',s)
        s=PERSONAL_PATH.sub('[redacted path]',s)
        s=PRIVATE_IP.sub('[redacted private IP]',s)
        self.redactions+=s!=original
        return s
    def clean(self,value,sensitive=False):
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                result[key]=self.clean(item,sensitive or secret_field(key,item))
            return result
        if isinstance(value,list):return [self.clean(x,sensitive) for x in value]
        if isinstance(value,str):
            if sensitive:self.redactions+=1;return '[redacted]'
            nested=embedded(value)
            if nested is not None:return json.dumps(self.clean(nested),ensure_ascii=False,separators=(',',':'))
            return self.text(value)
        return value

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('webmcp_cohort',type=pathlib.Path);ap.add_argument('dom_cohort',type=pathlib.Path)
    args=ap.parse_args()
    subprocess.run(['node','--input-type=module','-e',"""
      import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto'; import assert from 'node:assert/strict';
      for(const dir of process.argv.slice(1))for(const f of fs.readdirSync(dir).filter(f=>/-mercury-[123]\\.json$/.test(f))){
        const {checksum,...body}=JSON.parse(fs.readFileSync(path.join(dir,f)));
        assert.equal(crypto.createHash('sha256').update(JSON.stringify(body)).digest('hex'),checksum,'Checkpoint checksum mismatch');
      }
    """,str(args.webmcp_cohort),str(args.dom_cohort)],check=True)
    public=json.loads((RELEASE/'run.json').read_text())
    published={r['run_id']:r for r in public['rows']}
    expected=['wm-jev-mercury-v3','a11y-jev-mercury-ultrafast']
    records=[];secrets=set();summaries=[]
    for directory,arm in zip([args.webmcp_cohort,args.dom_cohort],expected):
        manifest=json.loads((directory/'manifest.json').read_text())
        for task in manifest['config']['tasks']:
            for match in PASSWORD.finditer(task['definition']['prompt']):secrets.add(match[1].rstrip('.;'))
        cohort=[]
        for file in sorted(directory.glob('*-mercury-[123].json')):
            record=json.loads(file.read_text());row=record['row'];assert record['status']=='complete'
            assert row['arm']==arm and row['run_id'] in published
            assert record['configuration_hash']==manifest['configuration_hash']
            # Checkpoints use JavaScript JSON.stringify hashes; verify those with the Node gate.
            for key,value in published[row['run_id']].items():
                if isinstance(value,(int,float,bool)):assert row[key]==value,(row['run_id'],key)
            item={'run_id':row['run_id'],'source_checkpoint_sha256':record['checksum'],'transcript':row['transcript'],'jev_request_bodies':record.get('request_bodies',[]),'decision_config':row.get('decision_config',{}),'provider_usage':row.get('provider_usage',{})}
            collect(item,secrets);cohort.append(item)
        assert len(cohort)==147 and len({r['run_id'] for r in cohort})==147
        records.append(cohort)
    redactor=Redactor(secrets)
    for cohort,arm,name in zip(records,expected,['webmcp','dom']):
        cleaned=[redactor.clean(row) for row in cohort]
        blob=''.join(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n' for row in cleaned).encode()
        compressed=gzip.compress(blob,mtime=0)
        filename=f'{name}-traces.jsonl.gz';(RELEASE/filename).write_bytes(compressed)
        summaries.append({'file':filename,'arm':arm,'attempts':len(cohort),'events':sum(len(r['transcript']) for r in cohort),'sha256':hashlib.sha256(compressed).hexdigest(),'bytes':len(compressed)})
    (RELEASE/'traces-manifest.json').write_text(json.dumps({'format':'gzip-compressed JSON Lines; one record per run_id','privacy':'Credentials, session tokens, private IPs and local filesystem paths redacted, including embedded JSON. Requests, responses, actions, model usage and timing retained. These are sanitized copies, not byte-identical raw checkpoints.','redacted_string_fields':redactor.redactions,'files':summaries},indent=2)+'\n')
    print(json.dumps({'attempts':294,'files':summaries,'redacted_string_fields':redactor.redactions}))

if __name__=='__main__':main()
