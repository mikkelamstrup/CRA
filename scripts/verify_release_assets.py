"""Read-only verification of local locked lab assets and installed CRA application.

No download, deployment, credential reads, guest boot or database write. Output
contains only code/asset hashes and counts; it is not a classroom release gate.
"""
import hashlib
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def digest(path):
    if path.is_symlink() or not path.is_file():raise ValueError('Missing or redirected release asset')
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    out=ROOT/'reports/local/release-assets.json';out.parent.mkdir(parents=True,exist_ok=True)
    run={'run_id':str(uuid4()),'started_at':datetime.now(timezone.utc).isoformat()}
    if out.exists():out.rename(out.with_name('release-assets-previous-'+run['run_id']+'.json'))
    out.write_text(json.dumps({**run,'status':'NOT COMPLETED'})+'\n')
    if platform.machine()!='x86_64':raise ValueError('Unvalidated release architecture')
    locks={}
    for name in ['image','browser','lab-tools','desktop-packages','course-packages']:
        path=ROOT/'infra'/(name+'.lock.json');locks[name]=json.loads(path.read_text())
    image=locks['image'];assert image['architecture']=='x86_64'
    assert digest(runtime.NODE/'.local/images'/image['filename'])==image['sha256']
    browser=locks['browser']
    assert digest(runtime.NODE/'.local/browser'/browser['archive'])==browser['sha256']
    count=0
    for lock_name,cache in [('lab-tools','lab-tools'),('desktop-packages','desktop-tools'),('course-packages','course-tools')]:
        for package in locks[lock_name]['packages']:
            assert digest(runtime.NODE/'.local'/cache/(package['sha256']+'.deb'))==package['sha256']
            count+=1
    source={};deployed={}
    essentials=['backend/cra/main.py','content/contracts.v1.json','node/agent.py','infra/image.lock.json','frontend/src/Academy.tsx','frontend/dist/index.html']
    for name in essentials:digest(ROOT/name)
    for folder in ['backend/cra','content','node','infra','frontend/src','frontend/dist']:
        assert (ROOT/folder).is_dir() and not (ROOT/folder).is_symlink()
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                relative=str(path.relative_to(ROOT));source[relative]=digest(path)
                if folder in ['backend/cra','content','frontend/dist'] and relative!='frontend/dist/nordlys.json':deployed[relative]=source[relative]
    for name in ['backend/uv.lock','backend/pyproject.toml','frontend/pnpm-lock.yaml','frontend/package.json','.node-version']:
        source[name]=digest(ROOT/name)
    code='''import hashlib,json,sys
from pathlib import Path
expected=json.load(sys.stdin);base=Path('/opt/cra');mismatches=[]
for name,value in expected.items():
 assert name.startswith(('backend/cra/','content/','frontend/dist/')) and '..' not in Path(name).parts
 path=base/name
 if path.is_symlink() or not path.is_file():mismatches.append(name);continue
 with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
 if actual!=value:mismatches.append(name)
actual=set()
for folder in ['backend/cra','content','frontend/dist']:
 root=base/folder
 assert root.is_dir() and not root.is_symlink()
 for path in root.rglob('*'):
  if path.is_file() and '__pycache__' not in path.parts:
   name=str(path.relative_to(base))
   if name!='frontend/dist/nordlys.json':actual.add(name)
print(json.dumps({'checked':len(expected),'mismatches':mismatches,'extra_files':sorted(actual-set(expected))}))
'''
    result=runtime.ssh('cra-control','python3 -c '+shlex.quote(code),input=json.dumps(deployed),timeout=90)
    if result.returncode:raise RuntimeError('Installed release comparison unavailable')
    comparison=json.loads(result.stdout)
    if comparison['mismatches'] or comparison['extra_files']:
        out.write_text(json.dumps({**run,'status':'FAIL','installed':comparison},indent=2)+'\n')
        print(json.dumps({'installed_mismatches':comparison['mismatches'],'extra_files':comparison['extra_files']}))
        raise ValueError('Installed application differs from prepared release')
    report={**run,'finished_at':datetime.now(timezone.utc).isoformat(),'status':'PASS','scope':'locked asset hashes and installed application; not a classroom release',
        'architecture':platform.machine(),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'working_tree_has_changes':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        'image_sha256':image['sha256'],'browser_sha256':browser['sha256'],'verified_package_archives':count,
        'installed_files_checked':comparison['checked'],'excluded_dynamic_asset':'frontend/dist/nordlys.json',
        'source_sha256':source}
    out.write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: locked image/browser,',count,'package archives and',comparison['checked'],'installed code/content/assets')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL:',type(error).__name__);sys.exit(1)
