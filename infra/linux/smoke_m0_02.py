"""Real KVM acceptance. Creates only cra-smoke and cra-control locally."""
import json
import time
import uuid
import subprocess
from runtime import ROOT, STATE, prepare, start_daemons, start, stop, ssh, wait_ready, virsh, kvm_status


def main():
    report={'task':'M0-02','status':'FAIL','tests':[]}
    out=ROOT/'reports/local/m0-02';out.mkdir(parents=True,exist_ok=True)
    def check(label, condition):
        report['tests'].append({'test':label,'status':'PASS' if condition else 'FAIL'})
        if not condition: raise AssertionError(label)
    try:
        start_daemons()
        name='cra-smoke'
        if not (STATE/'vms'/name/'manifest.json').exists(): prepare(name,22281)
        stop(name)
        before=time.monotonic();start(name);wait_ready(name)
        report['boot_seconds']=round(time.monotonic()-before,2)
        check('KVM enabled in live QMP', kvm_status(name)=={'enabled':True,'present':True})
        r=ssh(name,'uname -m && uname -r')
        check('real AMD64 guest command',r.returncode==0 and r.stdout.startswith('x86_64\n'))
        report['guest_kernel']=r.stdout.strip().splitlines()[-1]
        marker='cra-'+uuid.uuid4().hex
        r=ssh(name,f'printf %s {marker} > ~/cra-smoke-marker && cat ~/cra-smoke-marker')
        check('guest file write',r.returncode==0 and r.stdout==marker)
        old_boot=ssh(name,'cat /proc/sys/kernel/random/boot_id').stdout.strip()
        virsh('reboot',name)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            time.sleep(2)
            try: r=ssh(name,'cat /proc/sys/kernel/random/boot_id',timeout=5)
            except subprocess.TimeoutExpired: continue
            if r.returncode==0 and r.stdout.strip()!=old_boot: break
        else: raise TimeoutError('Guest reboot not observed')
        wait_ready(name)
        check('file survives actual guest reboot',ssh(name,'cat ~/cra-smoke-marker').stdout==marker)
        check('KVM after reboot',kvm_status(name)['enabled'])
        stop(name)
        check('controlled destroy',virsh('domstate',name).stdout.strip()=='shut off')
        name='cra-control'
        if not (STATE/'vms'/name/'manifest.json').exists(): prepare(name,22280,4096)
        if virsh('domstate',name).stdout.strip()=='shut off': start(name)
        wait_ready(name)
        check('control VM KVM',kvm_status(name)['enabled'])
        check('control management SSH',ssh(name,'printf CRA_CONTROL_READY').stdout=='CRA_CONTROL_READY')
        report['control_memory_stats']=dict(line.split() for line in virsh('dommemstat',name).stdout.splitlines() if line.strip())
        xml=virsh('dumpxml',name).stdout
        check('no host filesystem sharing','<filesystem' not in xml and '<hostdev' not in xml)
        report['status']='PASS'
    finally:
        cleanup_error=False
        if (STATE/'vms/cra-smoke/manifest.json').exists():
            try: stop('cra-smoke')
            except Exception:
                cleanup_error=True
                report['status']='FAIL'
                report['tests'].append({'test':'smoke cleanup','status':'FAIL'})
        (out/'smoke.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        if cleanup_error: raise RuntimeError('Smoke cleanup failed; inspect project domain state')


if __name__=='__main__': main()
