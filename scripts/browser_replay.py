"""Actual historical UI and single-operator latency with live teacher/projector."""
import json
import math
from pathlib import Path
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login

INSTRUMENT=r"""(() => {
 window.craTail='';window.craSamples=[];window.craMeasure=null;
 const Native=window.WebSocket;
 window.WebSocket=class extends Native {
  constructor(...args){super(...args);this.addEventListener('message',e=>{if(e.data instanceof ArrayBuffer){const text=new TextDecoder().decode(e.data);window.craTail=(window.craTail+text).slice(-65536);const m=window.craMeasure;if(m&&!m.matched&&text.includes(m.key))m.matched=true;}});}
  send(data){super.send(data);const m=window.craMeasure;if(m&&m.matched&&!m.done&&typeof data==='string'){let body;try{body=JSON.parse(data);}catch{return;}if(body.type==='ack'){m.done=true;requestAnimationFrame(()=>requestAnimationFrame(()=>window.craSamples.push(performance.now()-m.start)));}}}
 };
})();"""


def stats(values):
    ordered=sorted(values)
    return {'count':len(values),'p50_ms':round(ordered[math.ceil(len(values)*.5)-1],3),'p95_ms':round(ordered[math.ceil(len(values)*.95)-1],3),'max_ms':round(max(values),3)}


def main():
    b=json.load(sys.stdin);base='http://127.0.0.1:8000';out=Path('/var/lib/cra-browser/acceptance')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        tc=browser.new_context(viewport={'width':1512,'height':1080});teacher=tc.new_page();login(teacher,b['users']['teacher'])
        sc=browser.new_context(viewport={'width':1440,'height':1000});sc.add_init_script(INSTRUMENT);student=sc.new_page();login(student,b['users']['student'])
        assert student.request.get(base+'/api/v1/command-center/replay/labs/'+b['lab_id']).status==403
        teacher.get_by_role('button',name='◎ Command Center',exact=True).click()
        teacher.get_by_role('button',name='Åbn replay',exact=True).click()
        replay=teacher.get_by_role('region',name='Historisk replay',exact=True)
        expect(replay.locator('.replay-badge')).to_contain_text('GENERATION 2',timeout=15000)
        replay.get_by_label('Generation',exact=True).select_option('1')
        expect(replay.locator('.replay-badge')).to_contain_text('GENERATION 1')
        replay.get_by_label('Filtrer evidens',exact=True).select_option('validated')
        expect(replay.get_by_role('article',name='Valgt historisk hændelse')).to_contain_text('100 nye point')
        replay.get_by_label('Filtrer evidens',exact=True).select_option('observed')
        expect(replay.locator('.replay-current')).to_contain_text('IP-trafik observeret')
        replay.get_by_label('Filtrer evidens',exact=True).select_option('all')
        replay.get_by_role('button',name='Afspil replay',exact=True).click()
        teacher.wait_for_timeout(850)
        replay.get_by_role('button',name='Pause replay',exact=True).click()
        position=replay.get_by_role('slider',name='Position i replay').input_value();teacher.wait_for_timeout(900)
        assert replay.get_by_role('slider',name='Position i replay').input_value()==position
        replay.screenshot(path=str(out/'replay-desktop.png'))
        teacher.set_viewport_size({'width':390,'height':844});assert teacher.evaluate('document.documentElement.scrollWidth<=innerWidth')
        panel=teacher.get_by_role('region',name='Observeret labtrafik',exact=True)
        teacher.wait_for_function("document.querySelector('.traffic-map').viewBox.baseVal.width===360")
        panel.screenshot(path=str(out/'replay-mobile-map.png'));replay.screenshot(path=str(out/'replay-mobile.png'))
        teacher.set_viewport_size({'width':1512,'height':1080})
        csrf=teacher.request.get(base+'/api/v1/auth/me').json()['csrf_token']
        grant=teacher.request.post(base+'/api/v1/command-center/projectors',headers={'Origin':base,'X-CRA-CSRF':csrf},data={'lab_ids':[b['lab_id']]}).json()
        pc=browser.new_context(viewport={'width':1920,'height':1080});projector=pc.new_page();projector.goto(base+'/?view=projector&screen='+grant['grant']['id']+'#'+grant['token'])
        expect(projector.get_by_role('heading',name='Hold 01',exact=True)).to_be_visible()
        assert projector.request.get(base+'/api/v1/command-center/replay/labs/'+b['lab_id']).status==401
        # Frame intervals measure foreground-capable headless rendering cadence,
        # not a physical classroom projector or worst-case school laptop GPU.
        for page in (teacher,projector):
            page.evaluate("window.craFrames=[];window.craFrameLast=performance.now();window.craFramesOn=true;function frame(t){if(!window.craFramesOn)return;window.craFrames.push(t-window.craFrameLast);window.craFrameLast=t;requestAnimationFrame(frame);}requestAnimationFrame(frame)")
        student.get_by_role('button',name='⌘ Mit lab',exact=True).click()
        student_csrf=student.request.get(base+'/api/v1/auth/me').json()['csrf_token']
        began=time.monotonic()
        result=student.request.post(base+'/api/v1/labs/'+b['lab_id']+'/objectives/find-service/attempts',headers={'Origin':base,'X-CRA-CSRF':student_csrf,'Idempotency-Key':__import__('uuid').uuid4().hex},data={'generation':2,'flag':b['flag']})
        assert result.status==200 and result.json()['accepted'] and result.json()['points_awarded']==0
        expect(teacher.locator('.center-lab .objective-progress')).to_have_attribute('aria-label','1 af 1 mål valideret i denne generation',timeout=5000)
        objective_ms=round((time.monotonic()-began)*1000,3)
        expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=15000)
        def command(text,marker):
            student.evaluate("window.craTail=''");student.locator('.xterm-helper-textarea').focus();student.keyboard.type(text);student.keyboard.press('Enter')
            student.wait_for_function('(marker)=>window.craTail.includes(marker)',arg=marker,timeout=20000)
        # Actual raw Linux process echoes exactly one input character at a time.
        command("python3 -u -c \"import sys,tty,termios; old=termios.tcgetattr(0); tty.setraw(0); print('ECHO_'+ 'READY',flush=True); [(sys.stdout.write(sys.stdin.read(1)),sys.stdout.flush()) for _ in range(60)]; termios.tcsetattr(0,termios.TCSADRAIN,old)\"",'ECHO_READY')
        # Keep the real player active for every sample. A small recorded history
        # reaches its end quickly, so restart via the same visible UI controls.
        replay_restarts=0
        def keep_replay_active():
            nonlocal replay_restarts
            slider=replay.get_by_role('slider',name='Position i replay')
            maximum=int(slider.get_attribute('max'))
            assert maximum>=5, 'Insufficient recorded history for concurrent playback measurement'
            if maximum-int(slider.input_value())<5:
                replay.get_by_role('button',name='Til begyndelsen',exact=True).click()
            play=replay.get_by_role('button',name='Afspil replay',exact=True)
            if play.count():
                play.click();replay_restarts+=1
            expect(replay.get_by_role('button',name='Pause replay',exact=True)).to_be_visible()
        keep_replay_active()
        student.evaluate('window.craSamples=[]')
        for i in range(60):
            keep_replay_active()
            key=chr(65+i%26);student.evaluate('(key)=>window.craMeasure={key,start:performance.now(),matched:false,done:false}',key)
            student.keyboard.type(key)
            student.wait_for_function('(n)=>window.craSamples.length===n',arg=i+1,timeout=3000)
            expect(replay.get_by_role('button',name='Pause replay',exact=True)).to_be_visible()
        terminal=stats(student.evaluate('window.craSamples'))
        api_samples=[]
        for _ in range(50):
            keep_replay_active()
            api_samples.append(teacher.evaluate("async()=>{const t=performance.now();const r=await fetch('/api/v1/command-center/overview');if(!r.ok)throw Error('API probe failed');await r.json();return performance.now()-t;}"))
            expect(replay.get_by_role('button',name='Pause replay',exact=True)).to_be_visible()
        api=stats(api_samples)
        frames={}
        for name,page in [('teacher',teacher),('projector',projector)]:
            values=page.evaluate('window.craFramesOn=false;window.craFrames.slice(1)');frames[name]=stats(values)
        report={'profile':'one real operator, one target, live Command Center + projector + historical replay, control-VM Chromium','terminal_send_to_echo_parse_ack_plus_two_frames':terminal,'api_overview':api,'objective_to_center_ms_single_sample':objective_ms,'frame_intervals':frames,'browser':browser.version,'replay_restarts':replay_restarts,'replay_checked_before_each_sample':True}
        (out/'replay-performance.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report),flush=True)
        assert terminal['p95_ms']<=150 and api['p95_ms']<=300
        assert all(value['p95_ms']<=34 for value in frames.values())
        print('PASS: real historical generation/validated vs observed evidence, replay pause, 390px legible 2D, projector denied replay, measured actual terminal and graphics/API budgets',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
