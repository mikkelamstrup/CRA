"""Real temporary journals/ownership files, with explicit hypervisor fixtures."""
import hashlib
import json
import os
from uuid import uuid4

import pytest
from test_cleanup import agent, resources
from cra.contracts import missions


def initialize():
    mask=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(mask)


@pytest.mark.parametrize('status',['RUNNING','DONE'])
def test_legacy_node_one_operation_digest_survives_upgrade(resources,monkeypatch,status):
    _,owner,_,_,_=resources
    initialize()
    # Exact field order/encoding of the pre-node-id NodeCommand wire format.
    payload={'schema_version':1,'job_id':str(uuid4()),'lab_id':str(owner.lab_id),
        'organization_id':str(owner.organization_id),'user_id':str(owner.user_id),
        'operation':'destroy','generation':1,'slot':0,'mission_id':'first-contact','mission_version':1,
        'resources':missions()[0].resources.model_dump(mode='json'),'operator_user_ids':[]}
    wire=json.dumps(payload,separators=(',',':'));digest=hashlib.sha256(wire.encode()).hexdigest()
    prefix='cra-'+owner.lab_id.hex[:10]+'-1'
    result={'id':str(owner.lab_id),'organization_id':str(owner.organization_id),'user_id':str(owner.user_id),
        'generation':1,'slot':None,'state':'DESTROYED','operator':prefix+'o','target':prefix+'t','target_ip':'192.168.82.10'}
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',tuple(result.values()))
        db.execute('INSERT INTO operations VALUES (?,?,?,?)',(payload['job_id'],digest,status,json.dumps(result) if status=='DONE' else None))
        db.execute('INSERT INTO lab_intents VALUES (?,?)',(str(owner.lab_id),payload['job_id']))
    monkeypatch.setattr(agent.CAPTURE,'healthy',lambda *a:False)
    monkeypatch.setattr(agent.CAPTURE,'stop',lambda *a:None)
    observed=agent.execute(agent.NodeCommand.model_validate_json(wire))
    assert observed['state']=='DESTROYED' and observed['node_id']==1
    with agent.database() as db:
        stored=db.execute('SELECT * FROM operations').fetchone()
        assert stored['digest']==digest and stored['status']=='DONE'
    assert agent.execute(agent.NodeCommand.model_validate_json(wire))['node_id']==1


def test_journal_identity_cannot_change_and_node_two_rejects_legacy(resources,monkeypatch):
    initialize()
    monkeypatch.setenv('CRA_NODE_ID','2')
    with pytest.raises(ValueError,match='Journal node'):initialize()
    with agent.database() as db:
        db.execute('DROP TABLE node_identity')
        db.execute("INSERT INTO operations VALUES ('synthetic','synthetic','DONE','{}')")
    with pytest.raises(ValueError,match='Legacy journal'):initialize()


@pytest.mark.parametrize('kind',['manifest','pending','tombstone'])
def test_node_two_rejects_foreign_legacy_metadata_before_cleanup(resources,monkeypatch,kind):
    name,owner,vm,secret,_=resources
    monkeypatch.setenv('CRA_NODE_ID','2')
    if kind!='manifest':(vm/'manifest.json').unlink()
    if kind=='pending':
        data={'project':'cra','name':name,'nonce':'fixture','ownership':agent.ownership(owner)}
        for folder in (vm,secret):(folder/'pending.json').write_text(json.dumps(data))
    if kind=='tombstone':
        (agent.STATE/'cleanup').mkdir()
        (agent.STATE/'cleanup'/(name+'.json')).write_text(json.dumps(agent.ownership(owner)))
    with pytest.raises(ValueError,match='node identity'):agent.cleanup_vm(name,owner)
    assert vm.exists() and secret.exists()


def test_wrong_node_command_rejected_before_journal_or_hypervisor_access(resources,monkeypatch):
    _,owner,_,_,_=resources
    command=agent.NodeCommand(schema_version=1,node_id=2,job_id=uuid4(),lab_id=owner.lab_id,
        organization_id=owner.organization_id,user_id=owner.user_id,operation='destroy',generation=1,
        slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
    with pytest.raises(ValueError,match='Command node'):agent.execute(command)
    assert not (agent.STATE/'state.sqlite').exists()


def test_terminal_owned_domain_has_real_subprocess_deadline_and_no_pending_cleanup(resources,monkeypatch):
    import importlib.util
    import subprocess
    import sys
    import time
    name,owner,vm,secret,_=resources
    source=agent.ROOT/'infra/linux/runtime.py'
    spec=importlib.util.spec_from_file_location('deadline_runtime',source)
    bounded=importlib.util.module_from_spec(spec);spec.loader.exec_module(bounded)
    # Exercise the real subprocess timeout with a synthetic blocked command, not virsh.
    seen=[]
    def run(argv,**kwargs):
        seen.append(kwargs['timeout'])
        return subprocess.run([sys.executable,'-c','import time; time.sleep(5)'],capture_output=True,text=True,timeout=kwargs['timeout'])
    monkeypatch.setattr(bounded,'run',run)
    monkeypatch.setattr(agent.runtime,'owned_domain',bounded.owned_domain)
    start=time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):agent.owned_vm(name,owner,read_only=True,timeout=.05)
    assert seen==[.05] and time.monotonic()-start<1
    (vm/'manifest.json').unlink();(vm/'pending.json').write_text('{}')
    monkeypatch.setattr(agent,'cleanup_vm',lambda *a:pytest.fail('Terminal must not clean pending VM'))
    assert not agent.owned_vm(name,owner,read_only=True,timeout=.05)
    assert vm.exists() and secret.exists()


def test_concurrent_terminal_and_desktop_allocation_serialize_short_reads(resources,monkeypatch):
    import hmac,time
    from concurrent.futures import ThreadPoolExecutor
    name,owner,vm,secret,_=resources;initialize()
    signing=b'synthetic-only';(agent.runtime.SECRETS/'terminal-signing.key').write_bytes(signing)
    (secret/'client').write_text('synthetic');(secret/'host.pub').write_text('synthetic')
    (vm/'manifest.json').write_text(json.dumps({'port':24001,'desktop_profile':'gui-v1','memory_mib':2048}))
    with agent.database() as db:db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,'cra-synthetic-t','192.168.82.10'))
    def owned(*a,**kw):time.sleep(.08);return True
    monkeypatch.setattr(agent,'owned_vm',owned);monkeypatch.setattr(agent.CAPTURE,'healthy',lambda *a:True)
    requests=[]
    for purpose in ('terminal','desktop'):
        payload=json.dumps({'node_id':1,'schema_version':1,**agent.ownership(owner),'purpose':purpose,'grant_hash':uuid4().hex*2,'expires_at':int(time.time())+15})
        requests.append(agent.TerminalAllocation(payload=payload,signature=hmac.new(signing,payload.encode(),hashlib.sha256).hexdigest()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies=list(pool.map(agent.terminal_allocation,requests))
    assert [r['purpose'] for r in replies]==['terminal','desktop']
    with agent.database() as db:assert db.execute('SELECT count(*) FROM terminal_uses').fetchone()[0]==2
