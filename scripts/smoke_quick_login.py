"""Verify the owner's new temporary public URL; existing users are untouched."""
import json,os,sys,urllib.error,urllib.request
from pathlib import Path
from acceptance_client import Client,ORIGIN
from acceptance_class import provision,deactivate
from bounded_browser import run_browser,stop_all
ROOT=Path(__file__).resolve().parents[1]


def main():
    assert ORIGIN.startswith('https://') and ORIGIN.endswith('.trycloudflare.com')
    paths=[];report={'status':'FAIL','url':ORIGIN}
    try:
        with urllib.request.urlopen(ORIGIN+'/api/v1/health',timeout=20) as response:assert response.status==200 and response.headers.get('cf-ray')
        path,bundle=provision(on_owned=paths.append);client=Client(bundle['users']['student'])
        assert all(cookie.secure and cookie.has_nonstandard_attr('HttpOnly') for cookie in client.cookies if cookie.name=='cra_session')
        request=urllib.request.Request(ORIGIN+'/api/v1/auth/login',data=json.dumps({key:bundle['users']['student'][key] for key in ('username','password')}).encode(),headers={'Content-Type':'application/json','Origin':'https://foreign.invalid'})
        try:
            with urllib.request.urlopen(request,timeout=20):raise AssertionError('Foreign origin accepted')
        except urllib.error.HTTPError as error:assert error.code==403
        run_browser('browser_quick_login.py',{**bundle,'url':ORIGIN},timeout=120)
        report.update(status='PASS',public_browser_login='PASS',secure_cookie='PASS',foreign_origin_denied='PASS')
    finally:
        stop_all()
        for path in paths:deactivate(path)
        (ROOT/'reports/local/m8-03/quick-login.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: new public temporary tunnel login verified; synthetic accounts deactivated')


if __name__=='__main__':main()
