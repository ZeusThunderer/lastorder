"""Isolated LiteLLM routing test; no real provider traffic or keys."""
import copy,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import yaml
from litellm import Router
seen=[]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  seen.append(self.path)
  if self.path.startswith('/go/'):
   status=429; payload={'error':{'message':'isolated quota test','type':'rate_limit_error','code':'rate_limit'}}
  else:
   status=200; payload={'id':'fallback-probe','object':'chat.completion','created':0,'model':body['model'],'choices':[{'index':0,'message':{'role':'assistant','content':'reserve-ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  data=json.dumps(payload).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
config=yaml.safe_load(open('/tmp/T-079-config.yaml'))
models=[]
for entry in config['model_list']:
 if entry['model_name'] not in ['z-ai/glm-5.3-flash','openrouter-reserve/smart']: continue
 entry=copy.deepcopy(entry); params=entry['litellm_params']; primary=params['api_key'].endswith('OPENCODE_GO_API_KEY')
 params['api_key']='isolated-test'; params['api_base']=f'http://127.0.0.1:{server.server_port}/'+('go' if primary else 'or')+'/v1'
 params['num_retries']=0; params['max_retries']=0
 models.append(entry)
router=Router(model_list=models,fallbacks=config['router_settings']['fallbacks'],num_retries=0,retry_after=0,timeout=10)
try:
 result=router.completion(model='z-ai/glm-5.3-flash',messages=[{'role':'user','content':'isolated routing test'}])
 assert result.choices[0].message.content=='reserve-ok',result
 assert seen[0].startswith('/go/') and seen[-1].startswith('/or/'),seen
 print(json.dumps({'passed':True,'simulated_primary_status':429,'requests':seen,'result':'reserve-ok'}))
finally: server.shutdown()
