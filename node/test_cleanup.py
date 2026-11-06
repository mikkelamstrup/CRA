"""Fault injection against real temporary storage; libvirt is an explicit fixture."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import xml.etree.ElementTree as ET

import pytest

spec=importlib.util.spec_from_file_location('node_agent',Path(__file__).with_name('agent.py'))
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)


@pytest.fixture
def resources(tmp_path,monkeypatch):
    # Legacy lifecycle fixtures do not model physical RAM. Dedicated budget tests do.
    monkeypatch.setattr(agent,'require_physical_capacity',lambda command,required,names:None)
    monkeypatch.setattr(agent,'STATE',tmp_path/'node')
    agent.STATE.mkdir()
    monkeypatch.setattr(agent.runtime,'STATE',tmp_path/'runtime')
    monkeypatch.setattr(agent.runtime,'SECRETS',tmp_path/'secrets')
    name='cra-cleanup-1o'
    command=SimpleNamespace(lab_id=uuid4(),organization_id=uuid4(),user_id=uuid4(),generation=1)
    vm=agent.runtime.STATE/'vms'/name;secret=agent.runtime.SECRETS/name
    vm.mkdir(parents=True);secret.mkdir(parents=True)
    (vm/'manifest.json').write_text(json.dumps({'name':name,'ownership':agent.ownership(command)}))
    (secret/'synthetic').write_text('not-a-real-key')
    names={name}
    def virsh(*args):
        if args[0]=='undefine':names.remove(args[1])
        return SimpleNamespace(stdout='\n'.join(names))
    domain=ET.Element('domain');meta=ET.SubElement(domain,'metadata')
    ET.SubElement(meta,'{urn:cyber-range-academy:v1}ownership',{k:str(v) for k,v in agent.ownership(command).items()})
    monkeypatch.setattr(agent.runtime,'virsh',virsh)
    monkeypatch.setattr(agent.runtime,'owned_domain',lambda _:domain)
    monkeypatch.setattr(agent.runtime,'stop',lambda _:None)
    return name,command,vm,secret,names


@pytest.mark.parametrize('crash_step',[1,2])
def test_cleanup_resumes_after_undefine_and_after_disk_removal(resources,monkeypatch,crash_step):
    name,command,vm,secret,names=resources
    original=agent.shutil.rmtree;calls=0
    def interrupted(path):
        nonlocal calls
        calls+=1
        if calls==crash_step:raise OSError('synthetic crash')
        original(path)
    with monkeypatch.context() as patch:
        patch.setattr(agent.shutil,'rmtree',interrupted)
        with pytest.raises(OSError):agent.cleanup_vm(name,command)
    assert not names
    agent.cleanup_vm(name,command)
    agent.cleanup_vm(name,command)
    assert not vm.exists() and not secret.exists()
    assert not (agent.STATE/'cleanup'/(name+'.json')).exists()


def test_pending_resources_are_removed_only_for_matching_generation(resources):
    name,command,vm,secret,_=resources
    (vm/'manifest.json').unlink()
    marker={'project':'cra','name':name,'nonce':'synthetic','ownership':agent.ownership(command)}
    for path in [vm,secret]:(path/'pending.json').write_text(json.dumps(marker))
    command.generation=2
    with pytest.raises(ValueError):agent.cleanup_vm(name,command)
    assert vm.exists() and secret.exists()
    command.generation=1
    agent.cleanup_vm(name,command)
    assert not vm.exists() and not secret.exists()


def test_conflicting_manifest_cannot_use_existing_tombstone(resources):
    name,command,vm,secret,_=resources
    (agent.STATE/'cleanup').mkdir()
    (agent.STATE/'cleanup'/(name+'.json')).write_text(json.dumps(agent.ownership(command)))
    (vm/'manifest.json').write_text(json.dumps({'name':name,'ownership':{}}))
    with pytest.raises(ValueError):agent.cleanup_vm(name,command)
    assert secret.exists()


def test_busy_lab_allocations_fail_fast_without_consuming_grants(resources):
    import hashlib
    import hmac
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor

    _,command,_,_,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    key=b'synthetic-node-signing-key-not-installed'
    (agent.runtime.SECRETS/'terminal-signing.key').write_bytes(key)
    claims={'schema_version':1,**agent.ownership(command),'grant_hash':'a'*64,'expires_at':int(time.time())+15}
    payload=json.dumps(claims)
    allocation=agent.TerminalAllocation(payload=payload,signature=hmac.new(key,payload.encode(),hashlib.sha256).hexdigest())
    def redeem(_):
        try:agent.terminal_allocation(allocation)
        except ValueError as error:return str(error)
    with ThreadPoolExecutor(max_workers=8) as pool:
        with agent.PROCESS_LOCK,agent.lab_guard(command.lab_id):
            futures=[pool.submit(redeem,i) for i in range(20)]
            assert all(f.result(timeout=1)=='Lab operation in progress' for f in futures)
    with agent.database() as db:
        assert db.execute('SELECT count(*) FROM terminal_uses').fetchone()[0]==0
    expired=json.dumps({**claims,'expires_at':int(time.time())-1})
    with pytest.raises(ValueError,match='Expired allocation'):
        agent.terminal_allocation(agent.TerminalAllocation(payload=expired,signature=hmac.new(key,expired.encode(),hashlib.sha256).hexdigest()))



def test_partial_reset_keeps_retired_ownership_and_can_be_destroyed(resources,monkeypatch):
    import os
    from cra.contracts import missions

    name,owner,vm,secret,names=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    target='cra-reset-target'
    target_vm=agent.runtime.STATE/'vms'/target;target_secret=agent.runtime.SECRETS/target
    target_vm.mkdir();target_secret.mkdir()
    (target_vm/'manifest.json').write_text(json.dumps({'name':target,'ownership':agent.ownership(owner)}))
    names.add(target)
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',
                   (str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,target,'192.168.82.10'))
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=owner.lab_id,
                              organization_id=owner.organization_id,user_id=owner.user_id,
                              operation='reset',generation=2,slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
    original=agent.cleanup_vm
    def interrupted(name,claims):
        original(name,claims)
        raise OSError('synthetic crash after first old disk removal')
    with monkeypatch.context() as patch:
        patch.setattr(agent,'cleanup_vm',interrupted)
        with pytest.raises(OSError):agent.execute(command)
    with agent.database() as db:
        assert db.execute('SELECT generation FROM labs').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM retired_vms').fetchone()[0]==2
    assert not vm.exists() and target_vm.exists()
    result=agent.execute(command.model_copy(update={'job_id':uuid4(),'operation':'destroy'}))
    assert result['state']=='DESTROYED' and result['slot'] is None
    assert not vm.exists() and not secret.exists() and not target_vm.exists() and not target_secret.exists()
    assert not names
    with agent.database() as db:assert db.execute('SELECT count(*) FROM retired_vms').fetchone()[0]==0


def test_flag_derivation_is_bound_to_owner_and_generation(resources):
    _,owner,_,_,_=resources
    (agent.runtime.SECRETS/'flags.key').write_bytes(b's'*32)
    owner.mission_id='first-contact';owner.mission_version=1
    first=agent.flag_value(owner)
    assert first==agent.flag_value(owner) and len(first)==37
    owner.generation=2
    assert agent.flag_value(owner)!=first
    owner.generation=1;owner.user_id=uuid4()
    assert agent.flag_value(owner)!=first


@pytest.mark.parametrize('recovery,generation',[('destroy',2),('reset',3)])
def test_reset_never_delivered_can_be_recovered_monotonically(resources,monkeypatch,recovery,generation):
    import os
    from cra.contracts import missions

    name,owner,vm,secret,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',
                   (str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,'cra-absent-target','192.168.82.10'))
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=owner.lab_id,
                              organization_id=owner.organization_id,user_id=owner.user_id,
                              operation=recovery,generation=generation,slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
    # Node is still generation 1; the failed control reset had already advanced PG to 2.
    if recovery=='reset':
        def interrupted(*args,**kwargs):raise OSError('synthetic stop before new boot')
        monkeypatch.setattr(agent.runtime,'prepare',interrupted)
        with pytest.raises(OSError):agent.execute(command)
        command=command.model_copy(update={'job_id':uuid4(),'operation':'destroy'})
    result=agent.execute(command)
    assert result['state']=='DESTROYED' and result['generation']==generation
    assert not vm.exists() and not secret.exists()
    with pytest.raises(ValueError):
        agent.execute(command.model_copy(update={'job_id':uuid4(),'generation':1}))
    with pytest.raises(ValueError):
        agent.execute(command.model_copy(update={'job_id':uuid4(),'user_id':uuid4()}))


def test_reset_after_create_never_reached_node_reserves_own_new_generation(resources,monkeypatch):
    import os
    from cra.contracts import missions

    _,owner,unrelated_vm,_,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':24000,'free_disk_gib':100,'labs':[]})
    def interrupted(*args,**kwargs):raise OSError('synthetic stop before new boot')
    monkeypatch.setattr(agent.runtime,'prepare',interrupted)
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=uuid4(),
                              organization_id=owner.organization_id,user_id=owner.user_id,
                              operation='reset',generation=2,slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
    with pytest.raises(OSError):agent.execute(command)
    with agent.database() as db:
        lab=db.execute('SELECT * FROM labs').fetchone()
        assert lab['generation']==2 and lab['id']==str(command.lab_id)
    result=agent.execute(command.model_copy(update={'job_id':uuid4(),'operation':'destroy'}))
    assert result['state']=='DESTROYED' and unrelated_vm.exists()


def test_inventory_checks_physical_ownership_without_cleaning_pending_resources(resources):
    import os
    name,owner,vm,secret,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',
            (str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,'cra-missing-target','192.168.82.10'))
    observed=agent.snapshot()['labs'][0]
    assert observed['ownership_valid'] and observed['operator_present'] and not observed['target_present']
    (vm/'manifest.json').unlink()
    marker={'project':'cra','name':name,'nonce':'synthetic','ownership':agent.ownership(owner)}
    for folder in (vm,secret):(folder/'pending.json').write_text(json.dumps(marker))
    assert agent.snapshot()['labs'][0]['ownership_valid'] and vm.exists() and secret.exists()
    (secret/'pending.json').write_text('{}')
    assert not agent.snapshot()['labs'][0]['ownership_valid'] and vm.exists() and secret.exists()


def test_team_partial_reset_retires_every_operator_and_target(resources,monkeypatch):
    import os
    from cra.contracts import missions
    name,owner,vm,secret,names=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    extra=['cra-team-extra-p','cra-team-extra-q','cra-team-target-t']
    for extra_name in extra:
        disk=agent.runtime.STATE/'vms'/extra_name;private=agent.runtime.SECRETS/extra_name
        disk.mkdir();private.mkdir()
        (disk/'manifest.json').write_text(json.dumps({'name':extra_name,'ownership':agent.ownership(owner)}))
        names.add(extra_name)
    users=[uuid4() for _ in range(3)]
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,extra[-1],'192.168.82.10'))
        for index,op_name in enumerate([name,*extra[:2]]):
            db.execute('INSERT INTO operators VALUES (?,?,?,?)',(str(owner.lab_id),index,str(users[index]),op_name))
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=owner.lab_id,organization_id=owner.organization_id,user_id=owner.user_id,operation='reset',generation=2,slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources,operator_user_ids=users)
    # This fixture tests interrupted cleanup, independent of the live host RAM.
    monkeypatch.setattr(agent,'node_admit',lambda command:None)
    original=agent.cleanup_vm
    def crash(name,claims):
        original(name,claims)
        raise OSError('synthetic crash after one deletion')
    with monkeypatch.context() as patch:
        patch.setattr(agent,'cleanup_vm',crash)
        with pytest.raises(OSError):agent.execute(command)
    with agent.database() as db:
        assert db.execute('SELECT count(*) FROM retired_vms').fetchone()[0]==4
        assert [row['user_id'] for row in db.execute('SELECT * FROM operators ORDER BY ordinal')]==[str(u) for u in users]
    # Orphan cleanup has no participant payload; durable node journal still owns every VM.
    result=agent.execute(command.model_copy(update={'job_id':uuid4(),'operation':'destroy','operator_user_ids':[]}))
    assert result['state']=='DESTROYED' and not names
    for op_name in [name,*extra]:
        assert not (agent.runtime.STATE/'vms'/op_name).exists()
        assert not (agent.runtime.SECRETS/op_name).exists()
    with agent.database() as db:assert db.execute('SELECT count(*) FROM retired_vms').fetchone()[0]==0


def test_terminal_allocation_selects_only_signed_assigned_actor(resources,monkeypatch):
    import hashlib,hmac,os,time
    name,owner,vm,secret,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    actor=uuid4();unassigned=uuid4();key=b'synthetic-not-installed'
    (agent.runtime.SECRETS/'terminal-signing.key').write_bytes(key)
    (vm/'manifest.json').write_text(json.dumps({'port':24001}))
    (secret/'client').write_text('synthetic-private-material')
    (secret/'host.pub').write_text('synthetic-host-material')
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY','cra-other-operator','cra-team-target-t','192.168.82.10'))
        db.execute('INSERT INTO operators VALUES (?,?,?,?)',(str(owner.lab_id),0,str(actor),name))
    selected=[]
    def owned(name,claims,**kwargs):
        assert kwargs["read_only"] and 0<kwargs["timeout"]<=3
        selected.append(name)
        assert claims.user_id==owner.user_id
        return True
    monkeypatch.setattr(agent,'owned_vm',owned)
    monkeypatch.setattr(agent.CAPTURE,'healthy',lambda *args:True)
    def request(user,purpose="terminal"):
        payload=json.dumps({'schema_version':1,'purpose':purpose,**agent.ownership(owner),'operator_user_id':str(user),'grant_hash':uuid4().hex*2,'expires_at':int(time.time())+15})
        return agent.TerminalAllocation(payload=payload,signature=hmac.new(key,payload.encode(),hashlib.sha256).hexdigest())
    with pytest.raises(ValueError,match='Operator identity denied'):agent.terminal_allocation(request(unassigned))
    with pytest.raises(ValueError,match='Operator identity denied'):agent.terminal_allocation(request(owner.user_id))
    with monkeypatch.context() as patch:
        patch.setattr(agent.CAPTURE,'healthy',lambda *args:False)
        with pytest.raises(ValueError,match='Lab network unavailable'):agent.terminal_allocation(request(actor))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        with agent.PROCESS_LOCK,agent.lab_guard(uuid4()):
            result=pool.submit(agent.terminal_allocation,request(actor)).result(timeout=1)
    assert result['port']==24001 and result['session']=='cra-'+actor.hex+'-1'
    assert selected==[name]

    with pytest.raises(ValueError,match='Desktop profile unavailable'):agent.terminal_allocation(request(actor,'desktop'))
    (vm/'manifest.json').write_text(json.dumps({'port':24001,'memory_mib':2048,'desktop_profile':'gui-v1'}))
    gui=agent.terminal_allocation(request(actor,'desktop'))
    assert gui['purpose']=='desktop' and gui['port']==24001
    signed=request(actor)
    forged=json.loads(signed.payload);forged['purpose']='desktop'
    with pytest.raises(ValueError,match='signature'):agent.terminal_allocation(agent.TerminalAllocation(payload=json.dumps(forged),signature=signed.signature))


def test_missing_team_operator_journal_never_falls_back_to_owner(resources):
    import os
    _,owner,_,_,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    row={'id':str(owner.lab_id),'user_id':str(owner.user_id),'generation':1,'operator':'cra-example-1o'}
    users=[owner.user_id,uuid4()]
    with agent.database() as db:
        agent.assign_operators(db,row,users)
        assert len(agent.operators(db,row))==2
        db.execute('DELETE FROM operators WHERE lab_id=?',(row['id'],))
        with pytest.raises(ValueError,match='Incomplete operator assignment journal'):agent.operators(db,row)

@pytest.mark.parametrize('credit,free,accepted',[(0,5000,False),(1024,4864,True),(1792,4096,True),(1792,4095,False)])
def test_retry_admission_counts_only_remaining_memory(resources,monkeypatch,credit,free,accepted):
    _,command,_,_,_=resources
    command.resources=SimpleNamespace(operator_memory_mib=1024,target_memory_mib=768)
    command.operator_user_ids=[]
    own={'id':str(command.lab_id),'organization_id':str(command.organization_id),'user_id':str(command.user_id),'generation':1,'ownership_valid':True,'running_memory_mib':credit}
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':free,'free_disk_gib':100,'labs':[own]})
    if accepted:agent.node_admit(command)
    else:
        with pytest.raises(ValueError,match='capacity'):agent.node_admit(command)
    own['ownership_valid']=False
    with pytest.raises(ValueError,match='capacity'):agent.node_admit(command)
    own['ownership_valid']=True;own['user_id']=str(uuid4())
    with pytest.raises(ValueError,match='capacity'):agent.node_admit(command)


def test_inventory_memory_credit_requires_owned_running_manifest_and_domain(resources,monkeypatch):
    import os
    name,owner,vm,secret,names=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',name,'cra-missing-target','192.168.82.10'))
    domain=agent.runtime.owned_domain(name)
    memory=ET.SubElement(domain,'currentMemory',{'unit':'KiB'});memory.text=str(1024*1024)
    manifest={'name':name,'ownership':agent.ownership(owner),'memory_mib':1024}
    (vm/'manifest.json').write_text(json.dumps(manifest))
    assert agent.snapshot()['labs'][0]['running_memory_mib']==1024
    memory.text=str(768*1024)
    assert agent.snapshot()['labs'][0]['running_memory_mib']==0
    memory.text=str(1024*1024);names.clear()
    assert agent.snapshot()['labs'][0]['running_memory_mib']==0
    names.add(name);manifest['ownership']['user_id']=str(uuid4())
    (vm/'manifest.json').write_text(json.dumps(manifest))
    observed=agent.snapshot()['labs'][0]
    assert not observed['ownership_valid'] and observed['running_memory_mib']==0


def test_reset_retry_reclaims_committed_retired_generation_before_admission(resources,monkeypatch):
    import os
    from cra.contracts import missions
    name,owner,_,_,names=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=owner.lab_id,organization_id=owner.organization_id,user_id=owner.user_id,operation='reset',generation=2,slot=0,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
    prefix='cra-'+owner.lab_id.hex[:10]+'-2'
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),2,0,'PROVISIONING',prefix+'o',prefix+'t','192.168.82.10'))
        db.execute('INSERT INTO retired_vms VALUES (?,?,?,?,?)',(name,str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1))
    # Explicit memory fixture: the retired guest occupies the otherwise sufficient headroom.
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':5000 if names else 7000,'free_disk_gib':100,'labs':[]})
    def prepared(*args,**kwargs):
        assert not names
        with agent.database() as db:assert db.execute('SELECT count(*) FROM retired_vms').fetchone()[0]==0
        raise RuntimeError('reached safe new allocation')
    monkeypatch.setattr(agent.runtime,'prepare',prepared)
    with pytest.raises(RuntimeError,match='reached safe new allocation'):agent.execute(command)


def test_desktop_marker_failure_preserves_complete_ownership_manifest(resources,monkeypatch):
    _,owner,vm,_,_=resources
    path=vm/'manifest.json';original=path.read_bytes();manifest=json.loads(original)
    def interrupted(*args):raise OSError('synthetic disk failure before atomic replace')
    with monkeypatch.context() as patch:
        patch.setattr(agent.os,'replace',interrupted)
        with pytest.raises(OSError):agent.mark_desktop(path,manifest)
    assert path.read_bytes()==original and not list(vm.glob('.manifest-*'))
    agent.mark_desktop(path,manifest)
    actual=json.loads(path.read_text())
    assert actual['ownership']==agent.ownership(owner) and actual['desktop_profile']=='gui-v1'


@pytest.mark.parametrize('mission',['first-contact','web-trail-dns','access-granted','packet-hunter','vulnerable-app','operation-shadow'])
def test_completed_unacknowledged_current_intent_recovers_links_but_never_superseded(resources,monkeypatch,mission):
    import hashlib,os
    from cra.contracts import missions
    _,owner,_,_,_=resources
    previous=os.umask(0o077)
    try:agent.initialize()
    finally:os.umask(previous)
    command=agent.NodeCommand(schema_version=1,job_id=uuid4(),lab_id=owner.lab_id,
        organization_id=owner.organization_id,user_id=owner.user_id,operation='create',generation=1,
        slot=0,mission_id=mission,mission_version=1,resources=missions()[0].resources)
    prefix='cra-'+owner.lab_id.hex[:10]+'-1';target=prefix+'t'
    folder=agent.runtime.STATE/'vms'/target;folder.mkdir()
    (folder/'manifest.json').write_text(json.dumps({'lab_port':23400}))
    with agent.database() as db:
        db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',(str(owner.lab_id),str(owner.organization_id),str(owner.user_id),1,0,'READY',prefix+'o',target,'192.168.82.10'))
        row=db.execute('SELECT * FROM labs').fetchone();agent.assign_operators(db,row,[owner.user_id])
        result={**dict(row),'operator_user_ids':[str(owner.user_id)],'capture_ready':True}
        db.execute('INSERT INTO operations VALUES (?,?,?,?)',(str(command.job_id),hashlib.sha256(command.model_dump_json(exclude={'node_id'}).encode()).hexdigest(),'DONE',json.dumps(result)))
        db.execute('INSERT INTO lab_intents VALUES (?,?)',(str(owner.lab_id),str(command.job_id)))
    healthy=[False];calls=[]
    monkeypatch.setattr(agent.CAPTURE,'healthy',lambda *args:healthy[0])
    monkeypatch.setattr(agent,'owned_vm',lambda name,cmd:True)
    monkeypatch.setattr(agent,'node_admit',lambda command:None)
    monkeypatch.setattr(agent,'boot',lambda name:calls.append(('boot',name)))
    monkeypatch.setattr(agent,'guest',lambda name,cmd:calls.append(('probe',name)))
    def repair(cmd,assigned,links):
        assert cmd==command and assigned[0]['user_id']==str(owner.user_id) and links[0]['port']==23200
        calls.append(('repair',str(cmd.lab_id)));healthy[0]=True
    monkeypatch.setattr(agent,'ensure_capture',repair)
    checks=[]
    if mission!='first-contact':
        if mission in ('packet-hunter','vulnerable-app','operation-shadow'):
            import advanced_recipes as course_recipes
        else:
            import course_recipes
        failing=[True]
        def check_course(cmd,target,operators,links,guest):
            checks.append(cmd.mission_id)
            if failing[0]:raise RuntimeError('Synthetic missing course service')
        monkeypatch.setattr(course_recipes,'check',check_course)
        monkeypatch.setattr(agent.CAPTURE,'stop',lambda lab_id:healthy.__setitem__(0,False))
        with pytest.raises(RuntimeError,match='missing course service'):agent.execute(command)
        assert not healthy[0]
        # A duplicate ACK attempt must not bypass the service check after failure.
        with pytest.raises(RuntimeError,match='missing course service'):agent.execute(command)
        assert len(checks)==2 and not healthy[0]
        failing[0]=False;calls.clear()
    assert agent.execute(command)['capture_ready']
    if mission!='first-contact':assert checks==[mission]*3
    assert [c[0] for c in calls]==['boot','repair','boot','probe']
    calls.clear();assert agent.execute(command)['capture_ready'] and calls==[]
    healthy[0]=False
    with agent.database() as db:db.execute('UPDATE lab_intents SET operation_id=?',(str(uuid4()),))
    assert not agent.execute(command)['capture_ready'] and calls==[]
    with agent.database() as db:
        db.execute('UPDATE lab_intents SET operation_id=?',(str(command.job_id),))
        db.execute("UPDATE labs SET state='STOPPED'")
    assert not agent.execute(command)['capture_ready'] and calls==[]
    with agent.database() as db:db.execute("UPDATE labs SET state='READY',generation=2")
    with pytest.raises(ValueError,match='Completed intent ownership mismatch'):agent.execute(command)


def test_node_repeats_fifteen_lab_limit(resources,monkeypatch):
    _,command,_,_,_=resources
    command.resources=SimpleNamespace(operator_memory_mib=1024,target_memory_mib=768)
    command.operator_user_ids=[]
    labs=[{'id':str(uuid4()),'state':'READY','target_running':True,'operator_running':True,'operators':[]} for _ in range(14)]
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':30000,'free_disk_gib':700,'labs':labs})
    agent.node_admit(command)
    labs.append({'id':str(uuid4()),'state':'PROVISIONING','target_running':False,'operator_running':False,'operators':[]})
    with pytest.raises(ValueError,match='capacity'):agent.node_admit(command)


def test_lab_lock_waiters_share_lock_and_history_is_reclaimed():
    import gc
    import threading
    lab=uuid4();entered=threading.Event();started=threading.Event()
    def lifecycle():
        started.set()
        with agent.lab_guard(lab):entered.set()
    with agent.lab_guard(lab):
        thread=threading.Thread(target=lifecycle);thread.start()
        assert started.wait(1)
        assert not entered.wait(.05)
        with pytest.raises(ValueError,match='Lab operation in progress'):
            with agent.lab_guard(lab,wait=False):pass
    assert entered.wait(1);thread.join(timeout=1);assert not thread.is_alive()
    gc.collect()
    assert str(lab) not in agent.LAB_LOCKS
