#!/usr/bin/env python3
"""Offline integrity/security gate for the published Jev code and traces."""
import gzip,hashlib,importlib.util,json,pathlib,re,sys
if not __debug__: raise RuntimeError("Do not run publication checks with Python optimization enabled")
sys.dont_write_bytecode=True
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('redaction',ROOT/'scripts/publish-jev-transcripts.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
secret='fixture-sensitive-value'
sample={'arguments':json.dumps({'password':secret}), 'copied':secret,'access_token':'opaque-session-value','echo':'opaque-session-value', 'nested':json.dumps({'authorization':'Bearer privatevalue'})}
secrets=set();r.collect(sample,secrets);clean=r.Redactor(secrets).clean(sample)
assert secret not in json.dumps(clean) and 'opaque-session-value' not in json.dumps(clean)
assert json.loads(clean['arguments'])['password']=='[redacted]'
assert r.Redactor([]).text('file:///Users/example/secret.txt')=='[redacted path]'
assert r.Redactor([]).text('10.0.0.4')=='[redacted private IP]'
assert r.Redactor([]).text('routes/admin/Users/index.tsx')=='routes/admin/Users/index.tsx'
assert not r.PASSWORD.search('change the password for the account')
schema={'password':{'type':'string','description':'Account password'}}
secrets=set();r.collect(schema,secrets)
assert not secrets and r.Redactor(secrets).clean(schema)==schema
sample={'cookie':['session=fixture-cookie'], 'secret':{'nested':'fixture-nested'},'echo':'session=fixture-cookie fixture-nested'}
secrets=set();r.collect(sample,secrets);clean=r.Redactor(secrets).clean(sample)
assert clean['cookie']==['[redacted]'] and clean['secret']=={'nested':'[redacted]'}
assert 'fixture-cookie' not in json.dumps(clean) and 'fixture-nested' not in json.dumps(clean)
for encoded in (r.quote(secret,safe=''),r.quote_plus(secret,safe=''),r.base64.b64encode(secret.encode()).decode()):
    assert encoded not in r.Redactor({secret}).text(encoded)
try:r.collect({'password':'abc'},set())
except ValueError:pass
else:raise AssertionError('Short sensitive values must block export')
release=ROOT/'results/2026-09-18-jev-mercury'
canonical=json.loads((release/'run.json').read_text());expected={x['run_id']:x for x in canonical['rows']}
manifest=json.loads((release/'traces-manifest.json').read_text());seen=set();events=0
for f in manifest['files']:
    p=release/f['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==f['sha256']
    count=0;event_count=0
    with gzip.open(p,'rt') as handle:
        for line in handle:
            item=json.loads(line);key=item['run_id'];assert key in expected and key not in seen
            assert expected[key]['arm']==f['arm'];seen.add(key);count+=1
            assert item['transcript'];event_count+=len(item['transcript'])
            assert not r.TOKEN.search(line),f['file']
            assert not re.search(r'/(?:Users|home)/|/(?:private/)?tmp/',line),f['file']
            assert not r.PRIVATE_IP.search(line),f['file']
            secrets=set();r.collect(item,secrets)
            assert secrets <= {'[redacted]'},f"Unredacted sensitive field in {f['file']}"
    assert count==f['attempts']==147 and event_count==f['events'];events+=event_count
assert seen==set(expected)
source=json.loads((ROOT/'experiments/jev/source-manifest.json').read_text())
for item in source['files']:
    p=ROOT/item['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==item['published_sha256'],item['file']
    assert '/Users/' not in p.read_text(),item['file']
print(f'PASS: {len(seen)} matching attempts, {events} transcript events, {len(source["files"])} source hashes; redaction regression checks and artifact scans.')
