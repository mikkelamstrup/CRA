"""Project-owned KVM node. mTLS, fixed profiles and durable idempotent operations."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import sys
import threading
import weakref
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'),str(ROOT/'infra/linux'),str(ROOT/'node')]
from cra.capacity import MAX_ACTIVE_LABS, lab_link, target_ip
from cra.node_contracts import NodeCommand,TerminalAllocation,TerminalClaims
import runtime
import lab_tools
from memory_budget import require_capacity as require_physical_capacity, running_declarations, total_memory_mib
from capture import MANAGER as CAPTURE

STATE=runtime.STATE/'node'
CERTS=runtime.SECRETS/'node-tls'
PROCESS_LOCK=threading.Lock()
LAB_LOCKS_LOCK=threading.Lock()
LAB_LOCKS=weakref.WeakValueDictionary()


@contextmanager
def lab_guard(lab_id, *, wait=True, timeout=None):
    # A strong local reference keeps the same lock alive for holders/waiters.
    # Historical lab IDs do not accumulate once no call needs their lock.
    key=str(lab_id)
    with LAB_LOCKS_LOCK:
        lock=LAB_LOCKS.get(key)
        if lock is None:
            lock=threading.Lock()
            LAB_LOCKS[key]=lock
    acquired=lock.acquire(blocking=wait) if timeout is None else lock.acquire(timeout=timeout)
    if not acquired:
        raise ValueError('Lab operation in progress')
    try:
        yield
    finally:
        lock.release()


@contextmanager
def database(*, timeout=10):
    connection=sqlite3.connect(STATE/'state.sqlite',timeout=timeout)
    connection.row_factory=sqlite3.Row
    try:
        with connection:yield connection
    finally:connection.close()


def initialize():
    os.umask(0o077)
    STATE.mkdir(parents=True,exist_ok=True)
    with database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS node_identity (id INTEGER PRIMARY KEY CHECK(id=1), node_id INTEGER NOT NULL CHECK(node_id IN (1,2)))')
        identity=db.execute('SELECT node_id FROM node_identity WHERE id=1').fetchone()
        if identity is None:
            # Only the original node may adopt pre-identity journals.
            legacy=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('labs','operations','retired_vms')").fetchall()
            if runtime.node_id()!=1 and any(db.execute('SELECT 1 FROM '+row['name']+' LIMIT 1').fetchone() for row in legacy):
                raise ValueError('Legacy journal belongs to node 1')
            db.execute('INSERT INTO node_identity VALUES (1,?)',(runtime.node_id(),))
        elif identity['node_id']!=runtime.node_id():raise ValueError('Journal node identity mismatch')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS labs (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, user_id TEXT NOT NULL,
            generation INTEGER NOT NULL, slot INTEGER, state TEXT NOT NULL,
            operator TEXT NOT NULL, target TEXT NOT NULL, target_ip TEXT NOT NULL,
            UNIQUE(slot)
        );
        CREATE TABLE IF NOT EXISTS lab_intents (lab_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operator_sets (lab_id TEXT PRIMARY KEY, participants TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operators (
            lab_id TEXT NOT NULL, ordinal INTEGER NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL,
            PRIMARY KEY(lab_id,ordinal), UNIQUE(lab_id,user_id), UNIQUE(name)
        );
        CREATE TABLE IF NOT EXISTS retired_vms (
            name TEXT PRIMARY KEY, lab_id TEXT NOT NULL, organization_id TEXT NOT NULL,
            user_id TEXT NOT NULL, generation INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS terminal_uses (grant_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, result TEXT
        );
        ''')


def operators(db, lab):
    rows=[dict(row) for row in db.execute('SELECT * FROM operators WHERE lab_id=? ORDER BY ordinal',(lab['id'],))]
    assignment=db.execute('SELECT participants FROM operator_sets WHERE lab_id=?',(lab['id'],)).fetchone()
    if assignment and json.loads(assignment['participants'])!=[row['user_id'] for row in rows]:raise ValueError('Incomplete operator assignment journal')
    return rows or [{'lab_id':lab['id'],'ordinal':0,'user_id':lab['user_id'],'name':lab['operator']}]


def assign_operators(db, lab, users):
    prefix='cra-'+str(lab['id']).replace('-','')[:10]+'-'+format(lab['generation'],'x')
    db.execute('INSERT OR REPLACE INTO operator_sets VALUES (?,?)',(lab['id'],json.dumps([str(user) for user in users])))
    db.execute('DELETE FROM operators WHERE lab_id=?',(lab['id'],))
    for index,user in enumerate(users):
        db.execute('INSERT INTO operators VALUES (?,?,?,?)',(lab['id'],index,str(user),prefix+'opqr'[index]))


def snapshot():
    with database() as db:
        labs=[dict(row) for row in db.execute("SELECT * FROM labs WHERE state<>'DESTROYED'")]
        for lab in labs:lab['operators']=operators(db,lab)
    names=set(runtime.virsh('list','--name').stdout.splitlines())
    defined=set(runtime.virsh('list','--all','--name').stdout.splitlines())
    for lab in labs:
        lab['operator_running']=lab['operator'] in names
        lab['target_running']=lab['target'] in names
        lab['operator_present']=lab['operator'] in defined
        lab['target_present']=lab['target'] in defined
        lab['operators']=[{'user_id':op['user_id'],'name':op['name'],'present':op['name'] in defined,'running':op['name'] in names} for op in lab['operators']]
        observed_captures=CAPTURE.get(lab['id'],lab['generation'],[op['user_id'] for op in lab['operators']])
        lab['captures']=[item.model_dump(mode='json') for item in observed_captures]
        lab['capture_ready']=bool(observed_captures) and all(item.connected for item in observed_captures)
        lab['ownership_valid']=True
        lab['running_memory_mib']=0
        if lab['state']!='DESTROYED':
            expected={'lab_id':lab['id'],'organization_id':lab['organization_id'],'user_id':lab['user_id'],'generation':lab['generation']}
            try:
                for name in [op['name'] for op in lab['operators']]+[lab['target']]:
                    vm=runtime.STATE/'vms'/name;secret=runtime.SECRETS/name
                    if vm.is_symlink() or secret.is_symlink():raise ValueError('Redirected resource')
                    manifest=None
                    if (vm/'manifest.json').exists():
                        manifest=json.loads((vm/'manifest.json').read_text());runtime.require_node(manifest)
                        if manifest.get('name')!=name or manifest.get('ownership')!=expected:raise ValueError('Manifest mismatch')
                    elif vm.exists() or secret.exists():
                        pending=json.loads((vm/'pending.json').read_text());runtime.require_node(pending)
                        if pending!=json.loads((secret/'pending.json').read_text()) or pending.get('ownership')!=expected or pending.get('name')!=name or pending.get('project')!='cra' or not pending.get('nonce'):raise ValueError('Pending mismatch')
                    if name in defined:
                        domain=runtime.owned_domain(name)
                        meta=domain.find('metadata/{urn:cyber-range-academy:v1}ownership')
                        if meta is None or any(meta.get(k)!=str(v) for k,v in expected.items()):raise ValueError('Domain mismatch')
                        if name in names and manifest:
                            memory=domain.find('currentMemory')
                            if memory is None:memory=domain.find('memory')
                            units={'KiB':1024,'MiB':1}
                            if memory is not None and memory.get('unit') in units:
                                actual=int(memory.text)//units[memory.get('unit')]
                                declared=manifest.get('memory_mib',0)
                                if type(declared) is int and 0<declared<=4096 and actual==declared:
                                    lab['running_memory_mib']+=actual
            except Exception:
                lab['ownership_valid']=False
                lab['running_memory_mib']=0
    available=int(re.search(r'^MemAvailable:\s+(\d+)',Path('/proc/meminfo').read_text(),re.M)[1])*1024
    try:declared=sum(row['memory_mib'] for row in running_declarations())
    except (ValueError,OSError):declared=None
    return {'node_id':runtime.node_id(),'physical_memory_mib':total_memory_mib(),'running_declared_memory_mib':declared,'schema_version':1,'architecture':'x86_64','connected':True,'labs':labs,
            'available_memory_mib':available//1024**2,'free_disk_gib':shutil.disk_usage(runtime.NODE).free//1024**3,
            'max_labs':MAX_ACTIVE_LABS,'observed_at':time.time()}


def save_state(lab_id,state):
    with database() as db:
        db.execute('UPDATE labs SET state=? WHERE id=?',(state,str(lab_id)))


def ownership(command):
    return {'lab_id':str(command.lab_id),'organization_id':str(command.organization_id),
            'user_id':str(command.user_id),'generation':command.generation}


def owned_vm(name,command,*,read_only=False,timeout=60):
    path=runtime.STATE/'vms'/name/'manifest.json'
    if not path.exists():
        if not read_only and (path.parent/'pending.json').exists():cleanup_vm(name,command)
        return False
    manifest=json.loads(path.read_text());runtime.require_node(manifest)
    if manifest.get('ownership')!=ownership(command):raise ValueError('VM ownership mismatch')
    domain=runtime.owned_domain(name,timeout=timeout) if read_only else runtime.owned_domain(name)
    metadata=domain.find('metadata/{urn:cyber-range-academy:v1}ownership')
    if any(metadata.get(k)!=str(v) for k,v in ownership(command).items()):raise ValueError('Domain ownership mismatch')
    return True


def cleanup_vm(name,command):
    """Resume deletion after any crash, with durable ownership outside deleted trees."""
    runtime.validate_name(name)
    vm=runtime.STATE/'vms'/name; secret=runtime.SECRETS/name
    tombstones=STATE/'cleanup';tombstones.mkdir(mode=0o700,exist_ok=True)
    tombstone=tombstones/(name+'.json')
    expected=ownership(command)
    if any(path.is_symlink() for path in (vm,secret,tombstone)):
        raise ValueError('Redirected cleanup resource')
    authorized=False
    if tombstone.exists():
        saved=json.loads(tombstone.read_text());runtime.require_node(saved)
        if {k:v for k,v in saved.items() if k!='node_id'}!=expected:raise ValueError('Cleanup ownership mismatch')
        authorized=True
    manifest=vm/'manifest.json'
    if manifest.exists():
        data=json.loads(manifest.read_text());runtime.require_node(data)
        if data.get('name')!=name or data.get('ownership')!=expected:
            raise ValueError('VM ownership mismatch')
        authorized=True
    elif not authorized and (vm.exists() or secret.exists()):
        a=json.loads((vm/'pending.json').read_text())
        b=json.loads((secret/'pending.json').read_text());runtime.require_node(a);runtime.require_node(b)
        if a!=b or a.get('project')!='cra' or a.get('name')!=name or not a.get('nonce') or a.get('ownership')!=expected:
            raise ValueError('Pending ownership mismatch')
        authorized=True
    names=runtime.virsh('list','--all','--name').stdout.splitlines()
    if name in names:
        domain=runtime.owned_domain(name)
        metadata=domain.find('metadata/{urn:cyber-range-academy:v1}ownership')
        if metadata is None or any(metadata.get(k)!=str(v) for k,v in expected.items()):
            raise ValueError('Domain ownership mismatch')
        authorized=True
    if not authorized:return  # All three resource kinds are absent.
    # Atomic file plus fsync: survives process death between undefine/rmtree steps.
    temp=tombstone.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump({**expected,'node_id':runtime.node_id()},stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,tombstone)
    directory=os.open(tombstones,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
    if name in names:
        runtime.stop(name);runtime.virsh('undefine',name)
    for path in (vm,secret):
        if path.exists():shutil.rmtree(path)
    tombstone.unlink()


def boot(name):
    if runtime.virsh('domstate',name).stdout.strip()!='running':runtime.start(name)
    runtime.wait_ready(name)
    if runtime.kvm_status(name)!={'enabled':True,'present':True}:raise RuntimeError('KVM required')


def guest(name,command,timeout=60):
    result=runtime.ssh(name,command,timeout=timeout)
    if result.returncode:raise RuntimeError('Guest readiness failed')
    return result.stdout


def graceful_stop(name):
    if runtime.virsh('domstate',name).stdout.strip()=='shut off':return
    guest(name,'sync')
    runtime.virsh('shutdown',name)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if runtime.virsh('domstate',name).stdout.strip()=='shut off':return
        time.sleep(1)
    raise RuntimeError('Guest shutdown unconfirmed')


def objective_id(command):
    bridge = {'incident-http': 'verify-http-access', 'incident-evidence': 'reconstruct-incident',
              'incident-containment': 'contain-session-leak', 'incident-ownership': 'repair-report-ownership'}
    if command.mission_id in bridge:return bridge[command.mission_id]
    return {'first-files':'find-evidence','first-contact':'find-service','web-trail':'find-report','web-trail-dns':'find-dns-report','access-granted':'read-authorized-report','packet-hunter':'find-captured-report','vulnerable-app':'find-authorization-flaw','operation-shadow':'complete-investigation','cybercon-incident':'secure-and-restore','request-forge':'extract-database-proof'}[command.mission_id]


def flag_value(command):
    scoped={**ownership(command),'mission_id':command.mission_id,'mission_version':command.mission_version,'objective_id':objective_id(command)}
    key=(runtime.SECRETS/'flags.key').read_bytes()
    if len(key)!=32:raise ValueError('Invalid flag key')
    token=hmac.new(key,json.dumps(scoped,sort_keys=True).encode(),hashlib.sha256).hexdigest()[:32]
    return 'CRA{'+token+'}'


def cleanup_retired(command):
    with database() as db:
        retired=db.execute('SELECT * FROM retired_vms WHERE lab_id=?',(str(command.lab_id),)).fetchall()
    for row in retired:
        if row['organization_id']!=str(command.organization_id) or row['user_id']!=str(command.user_id) or row['generation']>=command.generation:
            raise ValueError('Retired ownership mismatch')
        cleanup_vm(row['name'],command.model_copy(update={'generation':row['generation']}))
        with database() as db:db.execute('DELETE FROM retired_vms WHERE name=?',(row['name'],))


def target_pages(command):
    if command.mission_id in ('incident-http', 'incident-evidence', 'incident-containment', 'incident-ownership'):
        import incident_bridge_recipe
        return incident_bridge_recipe.pages()
    if command.mission_id=='request-forge':
        import request_forge_recipe
        return request_forge_recipe.pages()
    if command.mission_id=='cybercon-incident':
        import cybercon_recipe
        return cybercon_recipe.pages()
    if command.mission_id in ('packet-hunter','vulnerable-app','operation-shadow'):
        import advanced_recipes
        return advanced_recipes.pages(command.mission_id,flag_value(command))
    if command.mission_id in ('web-trail-dns','access-granted'):
        import course_recipes
        return course_recipes.pages(command.mission_id,flag_value(command))
    if command.mission_id=='web-trail':
        paths=('index.html','robots.txt','drift/index.html','drift/journal.txt',
               'rapporter/rapport-206.txt','rapporter/rapport-207.txt')
        flag=flag_value(command)
        return {path:(ROOT/'content/labs/web-trail'/path).read_text().replace('{{FLAG}}',flag) for path in paths}
    if command.mission_id not in ('first-files','first-contact'):raise ValueError('Unknown mission recipe')
    flag=flag_value(command) if command.mission_id=='first-contact' else 'Dag 1: undersøg filerne i din egen operator.'
    return {'index.html':(ROOT/'content/labs/first-contact/index.html').read_text().replace('{{FLAG}}',flag)}


def target_service(name,command):
    # Trusted fixed recipe; no command or path comes from browser/mission content.
    service='''[Unit]
Description=CRA isolated training HTTP
After=network.target
[Service]
User=www-data
WorkingDirectory=/srv/cra-target
ExecStart=/usr/bin/python3 -m http.server 8080 --bind 0.0.0.0
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
'''
    lab_tools.upload(name,'/home/cra/cra-target.service',service.encode())
    # Fixed release paths only; neither filenames nor shell commands come from users.
    guest(name,'sudo install -d -m 0755 /srv/cra-target /srv/cra-target/drift /srv/cra-target/rapporter /srv/cra-target/service /srv/cra-target/evidence')
    for relative,page in target_pages(command).items():
        lab_tools.upload(name,'/home/cra/cra-page.txt',page.encode())
        guest(name,'sudo install -m 0644 /home/cra/cra-page.txt /srv/cra-target/'+relative)
    guest(name,'rm -- /home/cra/cra-page.txt')
    guest(name,"sudo install -m 0644 /home/cra/cra-target.service /etc/systemd/system/cra-target.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-target && sync")


def mark_desktop(manifest_path,manifest):
    # Preserve the complete ownership manifest through process/storage failure.
    fd,temporary=tempfile.mkstemp(prefix='.manifest-',dir=manifest_path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump({**manifest,'desktop_profile':'gui-v1'},stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,manifest_path)
        directory=os.open(manifest_path.parent,os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def ensure_capture(command,assigned,links):
    users=[item['user_id'] for item in assigned]
    running=all(runtime.virsh('domstate',item['name']).stdout.strip()=='running' for item in assigned)
    if running and CAPTURE.healthy(command.lab_id,command.generation,users):return
    # QEMU stream clients cannot reconnect in place. Preserve files through
    # confirmed guest shutdown, then attach fresh observers before booting.
    for item in assigned:
        if runtime.virsh('domstate',item['name']).stdout.strip()!='shut off':runtime.wait_ready(item['name'])
        graceful_stop(item['name'])
    CAPTURE.start(command.lab_id,command.generation,users,links)


def require_capture(command,assigned):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if CAPTURE.healthy(command.lab_id,command.generation,[item['user_id'] for item in assigned]):return
        time.sleep(.1)
    raise RuntimeError('Lab capture link unavailable')


def node_admit(command):
    available=snapshot()
    others=[item for item in available['labs'] if item['id']!=str(command.lab_id)]
    active=[item for item in others if item['state']=='PROVISIONING' or item['target_running'] or item['operator_running'] or any(op['running'] for op in item['operators'])]
    count=max(1,len(command.operator_user_ids))
    disk=12*(count+1)+sum(12*(1+max(1,len(item['operators']))) for item in others)
    required=command.resources.operator_memory_mib*count+command.resources.target_memory_mib
    own=next((item for item in available['labs'] if item['id']==str(command.lab_id)),None)
    credit=0
    credit_names=set()
    if own and own.get('ownership_valid') and own['organization_id']==str(command.organization_id) and own['user_id']==str(command.user_id) and own['generation']<=command.generation:
        credit=min(required,own.get('running_memory_mib',0))
        credit_names={name for name in [own.get('target'),own.get('operator'),*[op['name'] for op in own.get('operators',[])]] if isinstance(name,str)}
    if len(active)>=MAX_ACTIVE_LABS or available['available_memory_mib']<required-credit+4096 or available['free_disk_gib']<disk:
        raise ValueError('Insufficient node capacity')
    require_physical_capacity(command,required,credit_names)


def recover_completed(db,command,result):
    """Recover an ACK-lost current intent; never revive a superseded operation."""
    result['node_id']=runtime.node_id()
    users=result.get('operator_user_ids',[str(command.user_id)])
    result['capture_ready']=CAPTURE.healthy(command.lab_id,command.generation,users)
    if result['state']!='READY' or result['capture_ready']:return result
    lab=db.execute('SELECT * FROM labs WHERE id=?',(str(command.lab_id),)).fetchone()
    intent=db.execute('SELECT operation_id FROM lab_intents WHERE lab_id=?',(str(command.lab_id),)).fetchone()
    if not lab or not intent or intent['operation_id']!=str(command.job_id) or lab['state']!='READY':return result
    if any(str(lab[k])!=str(v) for k,v in ownership(command).items() if k!='lab_id') or lab['slot']!=command.slot:raise ValueError('Completed intent ownership mismatch')
    assigned=operators(db,lab)
    if [item['user_id'] for item in assigned]!=users:raise ValueError('Completed assignment mismatch')
    for name in [lab['target']]+[op['name'] for op in assigned]:
        if not owned_vm(name,command):raise ValueError('Completed VM missing')
    links=[lab_link(command.slot,i) for i in range(len(assigned))]
    manifest=json.loads((runtime.STATE/'vms'/lab['target']/'manifest.json').read_text())
    if manifest.get('lab_port')!=links[0]['port']+200:raise ValueError('Lab network profile requires reset')
    node_admit(command)
    boot(lab['target']);ensure_capture(command,assigned,links)
    for item,link in zip(assigned,links):
        boot(item['name'])
        guest(item['name'],f'curl --fail --max-time 5 http://192.168.{link["subnet"]}.10:8080/ >/dev/null')
    if command.mission_id in ('incident-http', 'incident-evidence', 'incident-containment', 'incident-ownership'):
        import incident_bridge_recipe
        try:
            incident_bridge_recipe.check(command,lab['target'],[item['name'] for item in assigned],links,guest)
        except Exception:
            CAPTURE.stop(command.lab_id)
            raise
    if command.mission_id=='request-forge':
        import request_forge_recipe
        try:
            request_forge_recipe.check(command,lab['target'],[item['name'] for item in assigned],links,guest)
        except Exception:
            CAPTURE.stop(command.lab_id)
            raise
    if command.mission_id=='cybercon-incident':
        import cybercon_recipe
        try:
            cybercon_recipe.check(command,lab['target'],[item['name'] for item in assigned],links,guest)
        except Exception:
            CAPTURE.stop(command.lab_id)
            raise
    if command.mission_id in ('packet-hunter','vulnerable-app','operation-shadow'):
        import advanced_recipes
        try:
            advanced_recipes.check(command,lab['target'],[item['name'] for item in assigned],links,guest)
        except Exception:
            CAPTURE.stop(command.lab_id)
            raise
    if command.mission_id in ('web-trail-dns','access-granted'):
        import course_recipes
        try:
            course_recipes.check(command,lab['target'],[item['name'] for item in assigned],links,guest)
        except Exception:
            CAPTURE.stop(command.lab_id)
            raise
    require_capture(command,assigned)
    result['capture_ready']=True
    return result


def execute(command):
    if command.node_id!=runtime.node_id():raise ValueError('Command node mismatch')
    # Preserve digests of historical node 1 operations across the protocol extension.
    encoded=command.model_dump_json(exclude={'node_id'} if command.node_id==1 else set())
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    lock=json.loads((ROOT/'infra/image.lock.json').read_text())
    if command.resources.image_sha256!=lock['sha256']:raise ValueError('Unapproved image digest')
    with PROCESS_LOCK, lab_guard(command.lab_id), (STATE/'lifecycle.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        with database() as db:
            old=db.execute('SELECT * FROM operations WHERE id=?',(str(command.job_id),)).fetchone()
            if old and old['digest']!=digest:raise ValueError('Operation ID conflict')
            if old and old['status']=='DONE':
                result=json.loads(old['result'])
                return recover_completed(db,command,result)
            lab=db.execute('SELECT * FROM labs WHERE id=?',(str(command.lab_id),)).fetchone()
            intent=db.execute('SELECT operation_id FROM lab_intents WHERE lab_id=?',(str(command.lab_id),)).fetchone()
            if old and (not intent or intent['operation_id']!=str(command.job_id)):raise ValueError('Superseded operation')
            if lab:
                for field,value in ownership(command).items():
                    if field=='lab_id':continue
                    if field=='generation' and command.operation in ('reset','destroy') and command.generation>lab['generation']:continue
                    if str(lab[field])!=str(value):raise ValueError('Lab ownership or generation mismatch')
                old_operators=operators(db,lab)
                requested=[str(user) for user in command.operator_user_ids] or [str(command.user_id)]
                if command.operation!='destroy' and requested!=[op['user_id'] for op in old_operators]:raise ValueError('Operator assignment mismatch')
                if command.operation=='create' and lab['state']=='DESTROYED':raise ValueError('Destroyed lab cannot be recreated')
                if lab['slot']!=command.slot and lab['state']!='DESTROYED':raise ValueError('Reservation mismatch')
                if command.operation=='reset' and lab['state']=='DESTROYED':raise ValueError('Destroyed lab cannot be reset')
                # A reset retry may have committed new names before deleting the old generation.
                # Retired journal ownership is verified before any deletion; reclaim it before admission.
                if command.operation=='reset' and command.generation==lab['generation']:cleanup_retired(command)
                if command.operation in ('create','reset','start'):node_admit(command)
                if command.operation=='reset' and command.generation>lab['generation']:
                    for name in [op['name'] for op in old_operators]+[lab['target']]:
                        db.execute('INSERT INTO retired_vms VALUES (?,?,?,?,?)',
                            (name,str(command.lab_id),str(command.organization_id),str(command.user_id),lab['generation']))
                    prefix='cra-'+command.lab_id.hex[:10]+'-'+format(command.generation,'x')
                    db.execute("UPDATE labs SET generation=?,operator=?,target=?,state='PROVISIONING' WHERE id=?",
                        (command.generation,prefix+'o',prefix+'t',str(command.lab_id)))
                    lab=db.execute('SELECT * FROM labs WHERE id=?',(str(command.lab_id),)).fetchone()
                    assign_operators(db,lab,[op['user_id'] for op in old_operators])
            elif command.operation not in ('create','reset','destroy'):raise ValueError('Unknown lab')
            else:
                if command.operation in ('create','reset','start'):node_admit(command)
                prefix='cra-'+command.lab_id.hex[:10]+'-'+format(command.generation,'x')
                db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?,?,?)',
                    (str(command.lab_id),str(command.organization_id),str(command.user_id),command.generation,
                     command.slot,'PROVISIONING',prefix+'o',prefix+'t',target_ip(command.slot)))
                lab=db.execute('SELECT * FROM labs WHERE id=?',(str(command.lab_id),)).fetchone()
                assign_operators(db,lab,command.operator_user_ids or [command.user_id])
            assigned=operators(db,lab)
            db.execute("INSERT OR IGNORE INTO operations(id,digest,status) VALUES (?,?,'RUNNING')",(str(command.job_id),digest))
            db.execute("INSERT INTO lab_intents VALUES (?,?) ON CONFLICT(lab_id) DO UPDATE SET operation_id=excluded.operation_id",(str(command.lab_id),str(command.job_id)))
        target=lab['target']
        operator_names=[item['name'] for item in assigned]
        links=[lab_link(command.slot,index) for index in range(len(assigned))]
        def check_targets():
            for item,link in zip(assigned,links):
                guest(item['name'],f'curl --fail --max-time 5 http://192.168.{link["subnet"]}.10:8080/ >/dev/null')
            if command.mission_id in ('incident-http', 'incident-evidence', 'incident-containment', 'incident-ownership'):
                import incident_bridge_recipe
                incident_bridge_recipe.check(command,target,operator_names,links,guest)
            if command.mission_id=='request-forge':
                import request_forge_recipe
                request_forge_recipe.check(command,target,operator_names,links,guest)
            if command.mission_id=='cybercon-incident':
                import cybercon_recipe
                cybercon_recipe.check(command,target,operator_names,links,guest)
            if command.mission_id in ('packet-hunter','vulnerable-app','operation-shadow'):
                import advanced_recipes
                advanced_recipes.check(command,target,operator_names,links,guest)
            if command.mission_id in ('web-trail-dns','access-granted'):
                import course_recipes
                course_recipes.check(command,target,operator_names,links,guest)
        if command.operation in ('create','reset'):
            if command.operation=='reset':CAPTURE.stop(command.lab_id)
            cleanup_retired(command)
            target_links=[{**link,'port':link['port']+200} for link in links]
            profiles=[(target,'target',command.resources.target_memory_mib,0,{**target_links[0],'role':'target','peers':target_links[1:]})]
            profiles += [(item['name'],'operator',command.resources.operator_memory_mib,index+1,{**links[index],'role':'operator'}) for index,item in enumerate(assigned)]
            for name,role,memory,offset,network in profiles:
                if not owned_vm(name,command):
                    runtime.prepare(name,24000+command.slot*10+offset,memory,lab=network,ownership=ownership(command))
                manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text())
                if manifest.get('lab')!=network:raise ValueError('Lab network profile requires reset')
            boot(target)
            ensure_capture(command,assigned,links)
            for name in operator_names:boot(name)
            target_service(target,command)
            for op in operator_names:
                lab_tools.install(op)
                if command.resources.operator_profile=='desktop':
                    import desktop_tools
                    manifest_path=runtime.STATE/'vms'/op/'manifest.json'
                    manifest=json.loads(manifest_path.read_text())
                    if manifest.get('desktop_profile')!='gui-v1':
                        desktop_tools.install(op)
                        mark_desktop(manifest_path,manifest)
                if command.mission_id=='first-files':
                    files={
                        'README.txt':'NORDLYS / autoriseret øvelse. Undersøg kun /home/cra/academy og dit tildelte lab. Se journalen og find den rapport, som hører til hændelse NL-104. Notér kilde og hvad flaget beviser.\n',
                        'journal/events.log':'08:10 NL-102 backup afsluttet\n08:15 NL-104 signal mistet; se arkiv/2026/rapport-104.txt\n08:20 NL-105 service genstartet\n',
                        'arkiv/2026/rapport-104.txt':'Hændelse NL-104: testkablet var frakoblet. Kilde: teknikerens øvelsesrapport.\n'+flag_value(command)+'\n',
                        'arkiv/2026/rapport-102.txt':'Hændelse NL-102: backup var vellykket. Ingen fejl konstateret.\n',
                    }
                    for relative,text in files.items():
                        lab_tools.upload(op,'/home/cra/academy/'+relative,text.encode())
                    guest(op,'sync')
            if command.mission_id in ('incident-http', 'incident-evidence', 'incident-containment', 'incident-ownership'):
                import incident_bridge_recipe
                incident_bridge_recipe.setup(command,target,operator_names,links,flag_value(command),guest)
            if command.mission_id=='request-forge':
                import request_forge_recipe
                request_forge_recipe.setup(command,target,operator_names,links,flag_value(command),guest)
            if command.mission_id=='cybercon-incident':
                import cybercon_recipe
                cybercon_recipe.setup(command,target,operator_names,links,flag_value(command),guest)
            if command.mission_id in ('packet-hunter','vulnerable-app','operation-shadow'):
                import advanced_recipes
                advanced_recipes.setup(command,target,operator_names,links,flag_value(command),guest)
            if command.mission_id in ('web-trail-dns','access-granted'):
                import course_recipes
                course_recipes.setup(command,target,operator_names,links,flag_value(command),guest)
            check_targets()
            require_capture(command,assigned)
            save_state(command.lab_id,'READY')
        elif command.operation=='start':
            for name in [target]+operator_names:
                if not owned_vm(name,command):raise ValueError('Missing VM')
            manifest=json.loads((runtime.STATE/'vms'/target/'manifest.json').read_text())
            if manifest.get('lab_port')!=links[0]['port']+200:raise ValueError('Lab network profile requires reset')
            boot(target)
            ensure_capture(command,assigned,links)
            for name in operator_names:boot(name)
            check_targets()
            require_capture(command,assigned)
            save_state(command.lab_id,'READY')
        elif command.operation=='stop':
            for name in operator_names+[target]:
                if owned_vm(name,command):graceful_stop(name)
            CAPTURE.stop(command.lab_id)
            save_state(command.lab_id,'STOPPED')
        elif command.operation=='destroy':
            CAPTURE.stop(command.lab_id)
            cleanup_retired(command)
            for name in operator_names+[target]:
                cleanup_vm(name,command.model_copy(update={'generation':lab['generation']}))
            with database() as db:db.execute("UPDATE labs SET state='DESTROYED',slot=NULL,generation=? WHERE id=?",(command.generation,str(command.lab_id)))
        with database() as db:
            result=dict(db.execute('SELECT * FROM labs WHERE id=?',(str(command.lab_id),)).fetchone())
            result['node_id']=runtime.node_id()
            result['operator_user_ids']=[op['user_id'] for op in operators(db,result)]
            result['capture_ready']=CAPTURE.healthy(command.lab_id,command.generation,result['operator_user_ids'])
            if command.operation in ('create','reset'):
                result['objective_verifiers']={objective_id(command):hashlib.sha256(flag_value(command).encode()).hexdigest()}
            db.execute("UPDATE operations SET status='DONE',result=? WHERE id=?",(json.dumps(result),str(command.job_id)))
        return result


def terminal_allocation(body):
    deadline=time.monotonic()+5
    signature=hmac.new((runtime.SECRETS/'terminal-signing.key').read_bytes(),body.payload.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,body.signature):raise ValueError('Invalid allocation signature')
    claims=TerminalClaims.model_validate_json(body.payload)
    if claims.node_id!=runtime.node_id():raise ValueError('Allocation node mismatch')
    with lab_guard(claims.lab_id,timeout=.5),database(timeout=2) as db:
        db.execute("BEGIN IMMEDIATE")
        now=time.time()
        if not now<claims.expires_at<=now+20:raise ValueError('Expired allocation')
        lab=db.execute('SELECT * FROM labs WHERE id=?',(str(claims.lab_id),)).fetchone()
        if not lab or lab['state']!='READY':raise ValueError('Lab not ready')
        if any(str(lab[k])!=str(v) for k,v in ownership(claims).items() if k!='lab_id'):
            raise ValueError('Allocation ownership mismatch')
        actor=str(claims.operator_user_id or claims.user_id)
        operator=next((item for item in operators(db,lab) if item['user_id']==actor),None)
        if operator is None:raise ValueError('Operator identity denied')
        if not CAPTURE.healthy(lab['id'],lab['generation'],[op['user_id'] for op in operators(db,lab)]):raise ValueError('Lab network unavailable')
        remaining=min(3,deadline-time.monotonic())
        if remaining<=0:raise ValueError('Allocation deadline')
        if not owned_vm(operator['name'],claims,read_only=True,timeout=remaining):raise ValueError('Missing operator')
        if time.monotonic()>=deadline:raise ValueError('Allocation deadline')
        db.execute('DELETE FROM terminal_uses WHERE expires_at<?',(int(time.time())-60,))
        db.execute('INSERT INTO terminal_uses VALUES (?,?)',(claims.grant_hash,claims.expires_at))
        name=operator['name'];manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text())
        port=24000+lab['slot']*10+operator['ordinal']+1
        if manifest.get('port')!=port:raise ValueError('Allocation destination mismatch')
        if claims.purpose=='desktop' and (manifest.get('desktop_profile')!='gui-v1' or manifest.get('memory_mib')!=2048):raise ValueError('Desktop profile unavailable')
        secret=runtime.SECRETS/name
        # Bound commit waiting by the remaining allocation budget as well.
        db.execute('PRAGMA busy_timeout='+str(max(1,int((deadline-time.monotonic())*1000))))
        # Management material goes only to the authenticated relay, never to browser/API.
        return {'node_id':runtime.node_id(),'lab_id':str(claims.lab_id),'organization_id':str(claims.organization_id),'user_id':str(claims.user_id),'operator_user_id':actor,'generation':claims.generation,'host':'10.0.2.2','port':port,'username':'cra','purpose':claims.purpose,
                'private_key':(secret/'client').read_text(),
                'host_key':(secret/'host.pub').read_text().strip(),
                'session':'cra-'+actor.replace('-','')+'-'+str(claims.generation)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def setup(self):
        super().setup()
        self.connection.settimeout(15)
    def respond(self,status,body):
        data=json.dumps(body).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def allowed(self,expected):
        subject=dict(pair for group in self.connection.getpeercert().get('subject',()) for pair in group)
        return subject.get('commonName')==expected
    def do_GET(self):
        if not self.allowed('cra-control'):self.respond(403,{'error':'role_denied'});return
        if self.path!='/v1/inventory':self.respond(404,{'error':'not_found'});return
        try:
            if runtime.node_id()==2 and not self.server.stream.thread.is_alive():raise ValueError('Terminal stream unavailable')
            self.respond(200,snapshot())
        except Exception:self.respond(503,{'error':'node_unavailable'})
    def do_POST(self):
        role={'/v1/operations':'cra-control','/v1/terminal-allocation':'cra-terminal'}.get(self.path)
        if not role:self.respond(404,{'error':'not_found'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:raise ValueError('Invalid request size')
            body=self.rfile.read(size)
            if not self.allowed(role):self.respond(403,{'error':'role_denied'});return
            result=execute(NodeCommand.model_validate_json(body)) if self.path=='/v1/operations' else terminal_allocation(TerminalAllocation.model_validate_json(body))
            self.respond(200,result)
        except (ValueError,sqlite3.IntegrityError):self.respond(409,{'error':'node_rejected'})
        except Exception:self.respond(503,{'error':'node_unavailable'})


class NodeServer(ThreadingHTTPServer):
    slots=threading.BoundedSemaphore(8)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            request.close();return
        try:super().process_request(request,address)
        except BaseException:
            self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def finish_request(self,request,address):
        request.settimeout(5)
        try:
            with self.tls_context.wrap_socket(request,server_side=True) as secured:
                self.RequestHandlerClass(secured,address,self)
        except (ssl.SSLError,OSError):pass


def main():
    initialize();runtime.start_daemons()
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(CERTS/'server.crt',CERTS/'server.key')
    context.load_verify_locations(CERTS/'ca.crt');context.verify_mode=ssl.CERT_REQUIRED
    bind=os.environ.get('CRA_NODE_BIND','127.0.0.1')
    if runtime.node_id()==1 and bind!='127.0.0.1':raise ValueError('Invalid local node binding')
    if runtime.node_id()==2:
        import ipaddress
        if ipaddress.IPv4Address(bind) not in ipaddress.IPv4Network('100.64.0.0/10'):raise ValueError('Invalid remote node binding')
    server=NodeServer((bind,22290),Handler)
    server.tls_context=context
    stream=None
    try:
        if runtime.node_id()==2:
            from terminal_stream import TerminalStreamServer
            stream=TerminalStreamServer((bind,22291),context,terminal_allocation)
            stream.start();server.stream=stream
        server.serve_forever()
    finally:
        server.server_close()
        if stream:stream.close()

if __name__=='__main__':main()
