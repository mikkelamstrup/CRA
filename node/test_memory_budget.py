"""Declared memory policy with real temporary manifests; libvirt XML is a fixture."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import xml.etree.ElementTree as ET
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra/linux'))
import memory_budget as budget


@pytest.fixture
def inventory(tmp_path,monkeypatch):
    monkeypatch.setattr(budget.runtime,'STATE',tmp_path)
    domains={}
    monkeypatch.setattr(budget.runtime,'virsh',lambda *args:SimpleNamespace(stdout='\n'.join(domains)))
    monkeypatch.setattr(budget.runtime,'owned_domain',lambda name:domains[name])
    monkeypatch.setattr(budget,'total_memory_mib',lambda:31296)
    def add(memory,owner=None,suffix='o'):
        name='cra-control' if owner is None else 'cra-'+owner['lab_id'].replace('-','')[:10]+'-'+format(owner['generation'],'x')+suffix
        path=tmp_path/'vms'/name;path.mkdir(parents=True)
        (path/'manifest.json').write_text(json.dumps({'name':name,'memory_mib':memory,'ownership':owner}))
        domain=ET.Element('domain');meta=ET.SubElement(domain,'metadata')
        ET.SubElement(meta,'{urn:cyber-range-academy:v1}ownership',{'project':'cra',**{k:str(v) for k,v in (owner or {}).items()}})
        ET.SubElement(domain,'memory',{'unit':'KiB'}).text=str(memory*1024)
        ET.SubElement(domain,'currentMemory',{'unit':'KiB'}).text=str(memory*1024)
        domains[name]=domain
        return name,path,domain
    return add,domains


def owner():return {'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(uuid4()),'generation':1}


def command(data):return SimpleNamespace(**data)


def test_full_declared_memory_rejects_sparse_overcommit_but_keeps_safe_profile(inventory):
    add,_=inventory;add(4096)
    for _ in range(5):
        o=owner();add(768,o,'t')
        for suffix in 'opq':add(1024,o,suffix)
    # Existing fifteen-operator CLI profile plus control and 4GiB reserve fits.
    own=owner();view=budget.require_capacity(command(own),0)
    assert view['running_declared_mib']==23296 and view['projected_with_reserve_mib']==27392
    with pytest.raises(ValueError,match='physical node capacity'):
        budget.require_capacity(command(own),4*2048+768)
    assert budget.require_capacity(command(own),1024+768)['projected_with_reserve_mib']==29184


def test_verified_retry_credit_and_foreign_owner_fail_closed(inventory):
    add,_=inventory;add(4096);o=owner();name,_,_=add(1024,o);target,_,_=add(768,o,'t')
    assert budget.require_capacity(command(o),1792,{name,target})['projected_with_reserve_mib']==9984
    changed={**o,'user_id':str(uuid4())}
    with pytest.raises(ValueError,match='ownership mismatch'):budget.require_capacity(command(changed),1792)
    with pytest.raises(ValueError,match='ownership mismatch'):budget.require_capacity(command({**o,'generation':0}),1792)


@pytest.mark.parametrize('kind',['missing','memory','current','units','owner','redirect'])
def test_unknown_or_mismatched_running_domain_cannot_disappear_from_budget(inventory,kind):
    add,_=inventory;add(4096);o=owner();_,path,domain=add(1024,o)
    manifest=path/'manifest.json'
    if kind=='missing':manifest.unlink()
    elif kind=='memory':domain.find('memory').text='2048'
    elif kind=='current':domain.find('currentMemory').text='524288'
    elif kind=='units':domain.find('memory').set('unit','unknown')
    elif kind=='owner':domain.find('metadata/{urn:cyber-range-academy:v1}ownership').set('user_id',str(uuid4()))
    else:
        saved=manifest.with_suffix('.saved');manifest.rename(saved);manifest.symlink_to(saved)
    with pytest.raises(ValueError):budget.require_capacity(command(owner()),1792)


def test_node_admission_calls_physical_guard_even_when_memavailable_is_high(monkeypatch):
    from test_cleanup import agent
    o=owner();c=command(o);c.resources=SimpleNamespace(operator_memory_mib=1024,target_memory_mib=768);c.operator_user_ids=[]
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':30000,'free_disk_gib':700,'labs':[]})
    calls=[]
    def denied(cmd,required,names):calls.append(required);raise ValueError('physical node capacity')
    monkeypatch.setattr(agent,'require_physical_capacity',denied)
    with pytest.raises(ValueError,match='physical node capacity'):agent.node_admit(c)
    assert calls==[1792]


@pytest.mark.parametrize('untracked',['old_generation','extra_operator'])
def test_same_lab_orphans_never_replace_stopped_registered_vms(inventory,monkeypatch,untracked):
    add,_=inventory;add(4096);o=owner();o['generation']=2
    if untracked=='old_generation':add(1024,{**o,'generation':1})
    else:add(1024,o,'p')
    prefix='cra-'+o['lab_id'].replace('-','')[:10]+'-2'
    monkeypatch.setattr(budget,'total_memory_mib',lambda:10800)
    # The registered current operator/target are stopped. Neither running orphan
    # will be reclaimed or reused by start; both must count in full.
    with pytest.raises(ValueError,match='physical node capacity'):
        budget.require_capacity(command(o),1792,{prefix+'o',prefix+'t'})


def test_node_credit_names_come_only_from_verified_current_journal(monkeypatch):
    from test_cleanup import agent
    o=owner();c=command(o);c.resources=SimpleNamespace(operator_memory_mib=1024,target_memory_mib=768);c.operator_user_ids=[]
    prefix='cra-'+o['lab_id'].replace('-','')[:10]+'-1'
    own={'id':o['lab_id'],'organization_id':o['organization_id'],'user_id':o['user_id'],'generation':1,'ownership_valid':True,'running_memory_mib':1024,'target':prefix+'t','operator':prefix+'o','operators':[{'name':prefix+'o'}]}
    monkeypatch.setattr(agent,'snapshot',lambda:{'available_memory_mib':30000,'free_disk_gib':700,'labs':[own]})
    names=[];monkeypatch.setattr(agent,'require_physical_capacity',lambda cmd,required,allowed:names.append(allowed))
    agent.node_admit(c);assert names==[{prefix+'t',prefix+'o'}]
    own['ownership_valid']=False
    agent.node_admit(c);assert names[-1]==set()
