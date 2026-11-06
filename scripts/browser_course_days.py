"""Actual course navigation, private teacher packs and exports in sandboxed Chromium."""
import json
import sys
from playwright.sync_api import sync_playwright, expect
from teaching import login


def main():
    b=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        for role in ['student','teacher']:
            context=browser.new_context(viewport={'width':1280,'height':900});page=context.new_page();login(page,b['users'][role])
            for day in b['course_days']:
                page.get_by_role('button',name='Dag '+str(day),exact=True).click()
                expect(page.locator('.lesson-header')).to_contain_text('DAG '+str(day))
                response=page.request.get('http://127.0.0.1:8000/api/v1/course/days/'+str(day)+'/teacher-notes')
                assert response.status==(200 if role=='teacher' else 403)
                export=page.request.get('http://127.0.0.1:8000/api/v1/course/days/'+str(day)+'/export')
                assert export.ok and 'Kommandooversigt' in export.text() and 'Refleksionsspørgsmål' in export.text()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path='/var/lib/cra-browser/acceptance/course-days-'+role+'.png',full_page=True)
            context.close()
        tc=browser.new_context();teacher=tc.new_page();login(teacher,b['users']['teacher'])
        sc=browser.new_context();student=sc.new_page();login(student,b['users']['student'])
        student.get_by_role('button',name='Dag 4',exact=True).click()
        student.get_by_label('Mine observationer',exact=True).fill('Syntetisk udkast som skal bevares')
        base='http://127.0.0.1:8000'
        csrf=teacher.request.get(base+'/api/v1/auth/me').json()['csrf_token']
        headers={'Origin':base,'X-CRA-CSRF':csrf}
        created=teacher.request.post(base+'/api/v1/teaching/sessions',headers=headers,data={'request_key':str(__import__('uuid').uuid4()),'title':'Synthetic release regression','day':1})
        assert created.status==201
        expect(student.get_by_text('Denne dag er ikke længere frigivet.',exact=False)).to_be_visible(timeout=20000)
        expect(student.get_by_label('Mine observationer',exact=True)).to_have_value('Syntetisk udkast som skal bevares')
        student.get_by_role('button',name='Gem notesbog',exact=True).click()
        expect(student.get_by_text('Dine noter er gemt.',exact=True)).to_be_visible()
        assert student.request.get(base+'/api/v1/course/days/4/export').status==403
        grant=teacher.request.post(base+'/api/v1/command-center/students/'+b['users']['student']['user_id']+'/releases',headers=headers,data={'mission_id':'access-granted','mission_version':1})
        assert grant.status==200
        expect(student.get_by_role('button',name='Dag 4',exact=True)).to_be_visible(timeout=20000)
        expect(student.get_by_label('Mine observationer',exact=True)).to_have_value('Syntetisk udkast som skal bevares')
        browser.close()
    print('PASS: real course navigation, teacher boundary, complete export and mobile layout',flush=True)


if __name__=='__main__':main()
