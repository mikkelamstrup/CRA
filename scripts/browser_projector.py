"""Actual sandboxed Chromium projector boundary; network hang is a fault fixture."""
import json
import re
from pathlib import Path
import sys
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def new_screen_id(page):
    return page.evaluate("new URLSearchParams(location.search).get('screen')")

def main():
    b=json.load(sys.stdin);base='http://127.0.0.1:8000';out=Path('/var/lib/cra-browser/acceptance')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        teacher_context=browser.new_context(viewport={'width':1512,'height':1080});teacher=teacher_context.new_page();login(teacher,b['users']['teacher'])
        teacher.get_by_role('button',name='◎ Command Center',exact=True).click()
        teacher.get_by_text('Projektor · vælg miljøer og administrér adgang',exact=True).click()
        teacher.get_by_label(b['users']['student']['username'],exact=True).check()
        teacher.route('**/api/v1/command-center/projectors',lambda route:route.fulfill(status=503,body='{}',content_type='application/json') if route.request.method=='GET' else route.continue_())
        with teacher.expect_popup() as opened:teacher.get_by_role('button',name='Åbn sikker projektor',exact=True).click()
        popup=opened.value
        expect(popup.get_by_role('heading',name='Hold 01',exact=True)).to_be_visible(timeout=15000)
        expect(teacher.get_by_text(re.compile(r'^(Adgangslisten kunne ikke opdateres|Projektoradgange kunne ikke hentes)'))).to_be_visible()
        teacher.unroute('**/api/v1/command-center/projectors')
        assert popup.evaluate('location.hash')=='' and popup.evaluate('window.opener===null')
        # Grants in the SAME context must keep their distinct selected scopes.
        csrf=teacher.request.get(base+'/api/v1/auth/me').json()['csrf_token']
        headers={'Origin':base,'X-CRA-CSRF':csrf}
        other=teacher.request.post(base+'/api/v1/command-center/projectors',headers=headers,data={'lab_ids':[b['other_lab_id']]}).json()
        other_page=teacher.context.new_page();other_page.goto(base+'/?view=projector&screen='+other['grant']['id']+'#'+other['token'])
        expect(other_page.locator('.projector-score')).to_contain_text('0/1')
        expect(popup.locator('.projector-score')).to_contain_text('100')
        page_a=popup.request.get(base+'/api/v1/projector/'+new_screen_id(popup)+'/feed').json()
        page_b=other_page.request.get(base+'/api/v1/projector/'+other['grant']['id']+'/feed').json()
        assert page_a['labs'][0]['points']==100 and page_b['labs'][0]['points']==0
        assert teacher.request.post(base+'/api/v1/command-center/projectors/'+other['grant']['id']+'/revoke',headers=headers,data={}).status==200
        expect(other_page.locator('.projector-card')).to_have_count(0,timeout=5000)
        expect(popup.locator('.projector-score')).to_contain_text('100')
        # Independent context has the projector cookie only.
        response=teacher.request.post(base+'/api/v1/command-center/projectors',headers=headers,data={'lab_ids':[b['lab_id']]})
        assert response.status==201;grant=response.json()
        context=browser.new_context(viewport={'width':1920,'height':1080});page=context.new_page()
        requests=[];page.on('request',lambda r:requests.append(r.url.split('#')[0]))
        page.goto(base+'/?view=projector&screen='+grant['grant']['id']+'#'+grant['token'])
        expect(page.get_by_role('heading',name='Hold 01',exact=True)).to_be_visible(timeout=15000)
        expect(page.locator('.projector-score')).to_contain_text('100')
        expect(page.locator('.projector-activity')).to_contain_text('Trafik observeret',timeout=15000)
        assert not any('/auth/me' in url or '/command-center/' in url for url in requests)
        assert context.request.get(base+'/api/v1/auth/me').status==401
        assert context.request.get(base+'/api/v1/command-center/overview').status==401
        feed=context.request.get(base+'/api/v1/projector/'+grant['grant']['id']+'/feed');assert feed.status==200
        private=[b['lab_id'],b['users']['teacher']['username'],b['users']['student']['username'],b['users']['student']['user_id'],b['target_ip'],'CRA{','operator_user_id','owner_name']
        assert not any(value in feed.text() for value in private)
        assert page.evaluate('location.hash')==''
        page.reload();expect(page.get_by_role('heading',name='Hold 01',exact=True)).to_be_visible()
        page.get_by_role('button',name='Fuldskærm',exact=True).click()
        page.wait_for_function('document.fullscreenElement!==null')
        page.screenshot(path=str(out/'projector-1080p.png'))
        page.evaluate('document.exitFullscreen()');page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(out/'projector-mobile.png'));page.set_viewport_size({'width':1920,'height':1080})
        held=[];page.route('**/api/v1/projector/*/feed',lambda route:held.append(route))
        expect(page.locator('.projector-card')).to_have_count(0,timeout=7000)
        for route in held:
            try:route.abort()
            except Exception:pass  # Browser may already have timed out this request.
        page.unroute('**/api/v1/projector/*/feed')
        expect(page.get_by_role('heading',name='Hold 01',exact=True)).to_be_visible(timeout=10000)
        response=teacher.request.post(base+'/api/v1/command-center/projectors/'+grant['grant']['id']+'/revoke',headers=headers,data={})
        assert response.status==200
        expect(page.locator('.projector-card')).to_have_count(0,timeout=5000)
        assert context.request.get(base+'/api/v1/projector/'+grant['grant']['id']+'/feed').status==401
        # The visible teacher control also revokes the separately opened popup.
        expect(teacher.get_by_role('button',name='Tilbagekald adgang',exact=True)).to_have_count(1,timeout=10000)
        teacher.get_by_role('button',name='Tilbagekald adgang',exact=True).click()
        expect(popup.locator('.projector-card')).to_have_count(0,timeout=10000)
        print('PASS: actual projector UI, separate cookie/context, no private fields, real observed traffic and validated points, reload, fullscreen, 1080p/mobile, hanging feed clears/reconnects, teacher revocation',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' class '+','.join(k for k in ('ERR_ABORTED','interrupted','Timeout','closed','new_context') if k in str(error))+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
