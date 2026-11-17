import json,os,subprocess,tempfile
from pathlib import Path
from runtime.lastorder.lineages import lineage
from runtime.lastorder.persona import Node,PersonaProvider
from runtime.node.workplace import create_workplace
print(json.dumps({'profiles':{slot:lineage(slot).key for slot in ['LastOrder01','LastOrder02','LastOrder03']},'lo_reference':Path('/app/agent/network/reference/characters-manifest.json').exists()}))

from runtime.lastorder.naming import preset
profiles={}
for i in [1,2,3]:
 slot=f'LastOrder0{i}'; profile=lineage(slot); provider=PersonaProvider(preset('lab'))
 cores=[]
 for serial in [900+i,910+i]:
  place=create_workplace(workplaces=Path('/tmp/image-smoke')/slot,template_dir=Path('/app/agent/last_order'),shared_dir=Path('/app/agent/shared'),serial=serial,traits=profile.traits,persona=provider,node=Node(serial,profile.traits,slot=slot),body_base='http://unused',hive_url='http://unused',node_key='',gateway_url='http://unused',opencode_model='unused',sister_uid=1001,sister_gid=1001)
  core=place/'PERSONA.md'; ref=place/'reference/characters.md'
  assert profile.character in core.read_text()
  for file in [core,ref]:
   result=subprocess.run(['su','-s','/bin/sh','sister','-c',f'printf tamper >> {file}'],capture_output=True)
   assert result.returncode!=0, file
  result=subprocess.run(['su','-s','/bin/sh','sister','-c',f'rm {ref}'],capture_output=True)
  assert result.returncode!=0, ref
 profiles[slot]={'births_checked':2,'lineage':profile.key,'core_write_denied':True,'reference_write_delete_denied':True}
print(json.dumps({'passed':True,'slots':profiles}))
