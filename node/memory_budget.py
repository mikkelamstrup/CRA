"""Fail closed on declared VM RAM, including control and unjournaled running domains.

Called under the node's lifecycle locks before any allocation/start/reset mutation.
MemAvailable remains a separate guard against current host pressure.
"""
import json
from pathlib import Path
from uuid import UUID
import runtime

RESERVE_MIB=4096


def total_memory_mib():
    value=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))//1024
    if value<=RESERVE_MIB:raise ValueError('Insufficient physical node capacity')
    return value


def declared_memory(domain,manifest):
    declared=manifest.get('memory_mib')
    if type(declared) is not int or declared not in (768,1024,2048,4096):
        raise ValueError('Unverifiable declared memory capacity')
    values=[]
    for tag in ['memory','currentMemory']:
        entry=domain.find(tag)
        if entry is None:
            if tag=='currentMemory':continue
            raise ValueError('Missing declared memory capacity')
        scale={'KiB':1,'MiB':1024,'GiB':1024*1024}.get(entry.get('unit','KiB'))
        if scale is None or entry.text is None:raise ValueError('Unknown memory capacity unit')
        kib=int(entry.text)*scale
        if kib<=0 or kib%1024:raise ValueError('Invalid declared memory capacity')
        values.append(kib//1024)
    if any(value!=declared for value in values):raise ValueError('Declared memory capacity mismatch')
    return declared


def running_declarations():
    names=runtime.virsh('list','--name').stdout.split()
    if len(names)!=len(set(names)):raise ValueError('Duplicate capacity inventory')
    result=[]
    for name in names:
        runtime.validate_name(name)
        path=runtime.STATE/'vms'/name/'manifest.json'
        if path.parent.is_symlink() or path.is_symlink():raise ValueError('Redirected capacity manifest')
        try:manifest=json.loads(path.read_text())
        except (OSError,ValueError) as error:raise ValueError('Missing capacity manifest') from error
        runtime.require_node(manifest)
        if manifest.get('name')!=name:raise ValueError('Capacity identity mismatch')
        domain=runtime.owned_domain(name)
        metadata=domain.find('metadata/{urn:cyber-range-academy:v1}ownership')
        if metadata is None or metadata.get('project')!='cra':raise ValueError('Capacity ownership mismatch')
        owner=manifest.get('ownership')
        if name=='cra-control':
            if owner is not None or metadata.get('lab_id') is not None:raise ValueError('Control capacity identity mismatch')
        else:
            if not isinstance(owner,dict) or set(owner)!={'lab_id','organization_id','user_id','generation'}:
                raise ValueError('Unverifiable running domain capacity')
            for field in ['lab_id','organization_id','user_id']:
                if not isinstance(owner[field],str) or str(UUID(owner[field]))!=owner[field]:raise ValueError('Invalid capacity owner')
            generation=owner['generation']
            if type(generation) is not int or not 1<=generation<=10000:raise ValueError('Invalid capacity generation')
            prefix='cra-'+UUID(owner['lab_id']).hex[:10]+'-'+format(generation,'x')
            if name not in {prefix+suffix for suffix in 'topqr'}:raise ValueError('Capacity resource name mismatch')
            if any(metadata.get(key)!=str(value) for key,value in owner.items()):raise ValueError('Capacity owner mismatch')
        result.append({'name':name,'memory_mib':declared_memory(domain,manifest),'ownership':owner})
    return result


def require_capacity(command,required,allowed_credit_names=frozenset()):
    if type(required) is not int or required<0:raise ValueError('Invalid required memory capacity')
    rows=running_declarations();total=total_memory_mib()
    own=0
    for row in rows:
        owner=row['ownership']
        if owner and owner['lab_id']==str(command.lab_id):
            if owner['organization_id']!=str(command.organization_id) or owner['user_id']!=str(command.user_id) or owner['generation']>command.generation:
                raise ValueError('Existing capacity ownership mismatch')
            if row['name'] in allowed_credit_names:own+=row['memory_mib']
    declared=sum(row['memory_mib'] for row in rows)
    projected=declared-min(own,required)+required+RESERVE_MIB
    if projected>total:raise ValueError('Insufficient physical node capacity')
    return {'physical_mib':total,'running_declared_mib':declared,'projected_with_reserve_mib':projected}
