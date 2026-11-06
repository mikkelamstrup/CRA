"""Real UI CTF scoring, pause/freeze and an account-free leaderboard; synthetic only."""
import json,sys,traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        tc=browser.new_context(viewport={'width':1280,'height':950});teacher=tc.new_page();login(teacher,b['users']['teacher'])
        sc=browser.new_context(viewport={'width':1280,'height':950});student=sc.new_page();login(student,b['users']['student'])
        for page in [teacher,student]:page.get_by_role('button',name='⚑ Konkurrence',exact=True).click()
        expect(student.get_by_text('Hold 02',exact=True).first).to_be_visible()
        teacher.get_by_role('button',name='Frys ranglisten',exact=True).click()
        expect(teacher.get_by_role('button',name='Vis alle point',exact=True)).to_be_visible()
        student.get_by_label('Flag',exact=True).fill(b['student_flag'])
        student.get_by_role('button',name='Aflevér konkurrenceflag',exact=True).click()
        expect(student.get_by_text('Godkendt · 150 konkurrencepoint til holdet.',exact=True)).to_be_visible()
        # Only private own progress changes while frozen; public rows remain at zero.
        expect(student.locator('tbody tr').filter(has_text='Hold 02').locator('td').nth(1)).to_have_text('0')
        teacher.get_by_label('Flag',exact=True).fill(b['teacher_flag'])
        teacher.get_by_role('button',name='Aflevér konkurrenceflag',exact=True).click()
        expect(teacher.get_by_text('Godkendt · 150 konkurrencepoint til holdet.',exact=True)).to_be_visible()
        delayed=[]
        def hold_old_list(route):
            if not delayed:delayed.append((route,route.fetch()))
            else:route.continue_()
        student.route('**/api/v1/competitions',hold_old_list)
        end=__import__('time').monotonic()+10
        while not delayed:
            if __import__('time').monotonic()>end:raise TimeoutError('Poll not observed')
            student.wait_for_timeout(100)
        student.get_by_label('Flag',exact=True).fill(b['student_flag'])
        expect(student.get_by_role('button',name='Aflevér konkurrenceflag',exact=True)).to_be_enabled()
        teacher.get_by_role('button',name='Sæt på pause',exact=True).click()
        expect(student.locator('.competition-status')).to_contain_text('På pause',timeout=10000)
        expect(student.get_by_label('Flag',exact=True)).to_be_disabled()
        delayed[0][0].fulfill(response=delayed[0][1])
        student.wait_for_timeout(300)
        expect(student.locator('.competition-status')).to_contain_text('På pause')
        student.unroute('**/api/v1/competitions',hold_old_list)
        teacher.get_by_role('button',name='Fortsæt konkurrencen',exact=True).click()
        expect(student.locator('.competition-status')).to_contain_text('I gang',timeout=10000)
        expect(student.get_by_label('Flag',exact=True)).to_be_enabled()
        expect(student.get_by_role('button',name='Aflevér konkurrenceflag',exact=True)).to_be_enabled()
        teacher.get_by_role('button',name='Vis alle point',exact=True).click()
        expect(student.locator('tbody tr').filter(has_text='Hold 02').locator('td').nth(1)).to_have_text('150',timeout=10000)
        teacher.get_by_role('button',name='Vis rangliste på denne skærm',exact=True).click()
        expect(teacher.locator('.competition-projection')).to_be_visible()
        old_board=[]
        def hold_old_board(route):
            if not old_board:old_board.append((route,route.fetch()))
            else:route.continue_()
        teacher.route('**/api/v1/competitions/*/board',hold_old_board)
        end=__import__('time').monotonic()+10
        while not old_board:
            if __import__('time').monotonic()>end:raise TimeoutError('Board poll not observed')
            teacher.wait_for_timeout(100)
        base='http://127.0.0.1:8000'
        current=teacher.request.get(base+'/api/v1/competitions').json()[0]
        csrf=teacher.request.get(base+'/api/v1/auth/me').json()['csrf_token']
        headers={'Origin':base,'X-CRA-CSRF':csrf}
        paused=teacher.request.post(base+'/api/v1/competitions/'+current['id']+'/actions',headers=headers,data={'revision':current['revision'],'action':'pause'})
        assert paused.status==200
        expect(teacher.locator('.competition-status')).to_contain_text('På pause',timeout=10000)
        old_board[0][0].fulfill(response=old_board[0][1]);teacher.wait_for_timeout(300)
        expect(teacher.locator('.competition-status')).to_contain_text('På pause')
        teacher.unroute('**/api/v1/competitions/*/board',hold_old_board)
        resumed=teacher.request.post(base+'/api/v1/competitions/'+current['id']+'/actions',headers=headers,data={'revision':paused.json()['revision'],'action':'resume'})
        assert resumed.status==200
        expect(teacher.locator('.competition-status')).to_contain_text('I gang',timeout=10000)
        assert not teacher.locator('.account').count()
        html=teacher.locator('body').inner_text()
        assert all(u['username'] not in html for u in b['users'].values())
        assert b['student_flag'] not in html and b['teacher_flag'] not in html
        assert teacher.locator('tbody tr').count()==2
        teacher.screenshot(path='/var/lib/cra-browser/acceptance/competition-board.png',full_page=True)
        student.set_viewport_size({'width':390,'height':844})
        assert student.evaluate('document.documentElement.scrollWidth<=innerWidth')
        student.screenshot(path='/var/lib/cra-browser/acceptance/competition-mobile.png',full_page=True)
        teacher.get_by_role('button',name='Luk ranglisten',exact=True).click()
        teacher.on('dialog',lambda dialog:dialog.accept())
        teacher.get_by_role('button',name='Afslut konkurrencen',exact=True).click()
        expect(student.locator('.competition-status')).to_contain_text('Afsluttet',timeout=10000)
        expect(student.get_by_label('Flag',exact=True)).to_be_disabled()
        browser.close()
    print('PASS: real CTF browser submissions, freeze, pause, resume, end, anonymous board and mobile',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
