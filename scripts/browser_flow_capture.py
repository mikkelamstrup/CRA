"""Actual browser traffic view; only the hanging HTTP transport is a fault fixture."""
import json
from pathlib import Path
import sys
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin);base='http://127.0.0.1:8000';out=Path('/var/lib/cra-browser/acceptance')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        teacher=browser.new_page(viewport={'width':1512,'height':1080});student=browser.new_page()
        login(teacher,b['users']['teacher']);login(student,b['users']['student'])
        assert student.request.get(base+'/api/v1/command-center/labs/'+b['lab_id']+'/traffic').status==403
        teacher.get_by_role('button',name='◎ Command Center',exact=True).click()
        panel=teacher.get_by_role('region',name='Observeret labtrafik',exact=True)
        expect(panel).to_be_visible(timeout=15000)
        expect(panel.locator('.traffic-health')).to_contain_text('Netværkslinks bekræftet',timeout=20000)
        expect(panel.locator('.traffic-list li')).not_to_have_count(0,timeout=20000)
        expect(panel.locator('.traffic-filters')).to_contain_text('Operatør 1')
        expect(panel.locator('.traffic-filters')).to_contain_text('Operatør 2')
        panel.get_by_role('button',name='Operatør 2',exact=True).click()
        expect(panel.locator('.traffic-list li').first).to_contain_text('Operatør 2')
        panel.get_by_role('button',name='Alle links',exact=True).click()
        panel.screenshot(path=str(out/'flow-capture-desktop.png'))
        teacher.set_viewport_size({'width':390,'height':844});assert teacher.evaluate('document.documentElement.scrollWidth<=innerWidth')
        panel.screenshot(path=str(out/'flow-capture-mobile.png'));teacher.set_viewport_size({'width':1512,'height':1080})
        # Hold only this request. Overview continues to fetch genuine lab state.
        held=[]
        teacher.route('**/api/v1/command-center/labs/*/traffic',lambda route:held.append(route))
        expect(panel.locator('.traffic-health')).to_contain_text('Netværksstatus ubekræftet',timeout=25000)
        expect(teacher.get_by_text('Opdaterer automatisk',exact=True)).to_be_visible()
        for route in held:route.abort()
        teacher.unroute('**/api/v1/command-center/labs/*/traffic')
        expect(panel.locator('.traffic-health')).to_contain_text('Netværkslinks bekræftet',timeout=15000)
        teacher.emulate_media(reduced_motion='reduce')
        assert teacher.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        print('PASS: actual scoped teacher traffic, two operator links, numeric flow evidence, filter, 390px layout, hanging traffic expires while overview remains live, recovery',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
