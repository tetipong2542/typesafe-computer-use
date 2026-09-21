"""No-network check of the upstream provider-option hostname condition."""
import os,sys,types
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root),str(root/'upstream')]
import bridge
bridge.install_browser_harness_stub()
from jev_ultrafast import model
captured=[]
model.post_json=lambda _url,_key,body: captured.append(body) or {'model':'fixture','choices':[{'message':{'content':'{"text":"Ada"}'}}],'usage':{}}
os.environ['TEXT_MODEL_API_KEY']='fixture-only'
os.environ.pop('TEXT_MODEL_REASONING',None)
for url,deepseek in [('https://api.deepseek.com/v1',True),('https://example.invalid/api.deepseek.com/v1',False),('https://api.deepseek.com.example.invalid/v1',False),('https://api.inceptionlabs.ai/v1',False)]:
    os.environ['TEXT_MODEL_BASE_URL']=url
    model.field_text({})
    assert ('thinking' in captured[-1])==deepseek
print('PASS: provider options depend on the exact hostname, not URL substrings; no network calls.')
