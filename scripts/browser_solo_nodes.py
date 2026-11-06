"""Synthetic solo operators through the selected HTTP/HTTPS origin; credentials exclusively on stdin."""
import asyncio
import json
import sys
import time
import traceback
import math
import re
from uuid import uuid4

METRICS={}
SESSIONS=[]
def marker_diagnostics():
    return [{'armed':b'LOAD_ARMED' in s[1],'started':b'LOAD_STARTED' in s[1],'complete':b'LOAD_COMPLETE' in s[1],'started_exact_line':b'LOAD_STARTED' in s[1].replace(b'\r',b'').splitlines(),'terminal_closed':s[4].get('closed',False)} for s in SESSIONS]

def p95(values):return sorted(values)[math.ceil(len(values)*.95)-1]
from playwright.async_api import async_playwright, expect


async def main():
    data = json.load(sys.stdin)
    students = [a for a in data['accounts'] if a['role'] == 'student' and a['username'] != 'testelev']
    count=data.get('students',15);assert type(count) is int and count in (15,18) and len(students)==count
    METRICS['students']=count
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        check = await browser.new_page()
        await check.goto('chrome://sandbox')
        assert 'You are adequately sandboxed.' in await check.locator('body').inner_text()
        await check.close()
        sessions = SESSIONS
        for account in students + [next(a for a in data['accounts'] if a['role'] == 'teacher')]:
            context = await browser.new_context(viewport={'width':1440,'height':1000})
            page = await context.new_page()
            output = bytearray();state={'received':0,'acked':0,'ack_time':0}
            def watch(ws, output=output,state=state):
                if '/terminal/' not in ws.url:return
                state.update(received=0,acked=0,ack_time=0,connection=ws,closed=False)
                def closed():
                    if state.get('connection') is ws:state['closed']=True
                def received(frame):
                    if state.get('connection') is ws and isinstance(frame,bytes):
                        state['received']+=len(frame)
                        output.extend(frame)
                        if len(output)>262144:del output[:-131072]
                def sent(frame):
                    if state.get('connection') is not ws or not isinstance(frame,str):return
                    value=json.loads(frame)
                    if value.get('type')=='ack':state.update(acked=value['bytes'],ack_time=time.monotonic())
                ws.on('framereceived',received);ws.on('framesent',sent)
                ws.on('close',closed)
            page.on('websocket',watch)
            await page.goto(data['url'])
            await page.get_by_label('Brugernavn').fill(account['username'])
            await page.get_by_label('Adgangskode').fill(account['password'])
            await page.get_by_role('button',name='Fortsæt →').click()
            await expect(page.get_by_role('heading',name='Dit forløb',exact=True)).to_be_visible()
            if account['role']=='teacher':
                teacher = page
                continue
            labs = await page.evaluate("async()=> (await fetch('/api/v1/labs')).json()")
            labs = [lab for lab in labs if lab['state']!='DESTROYED']
            assert len(labs)==1 and labs[0]['state']=='READY'
            await page.get_by_role('button',name='⌘ Mit lab',exact=True).click()
            await expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
            if data.get('browser_attempt',1)>1:
                await page.locator('.xterm-helper-textarea').focus();await page.keyboard.press('Control+c');await page.wait_for_timeout(500)
            sessions.append((page,output,labs[0],account['expected_operator'],state))
            print('Synthetic browser shell connected:',len(sessions),flush=True)
        await teacher.get_by_role('button',name='◎ Command Center',exact=True).click()
        for identity in (1,2):await expect(teacher.get_by_text('SERVER '+str(identity),exact=True)).to_be_visible()
        async def command(session, text, marker, timeout=30):
            page,output,lab,name,*_=session
            completion=None
            if marker in (b'CAPACITY_VERIFIED',b'RECONNECT_VERIFIED'):
                nonce=uuid4().hex
                completion=('CMD_'+nonce+':').encode()
                text+="; _cra_exit=$?; printf 'CMD_%s:%s:END\\n' "+nonce+' "$_cra_exit"'
                assert completion.decode() not in text and marker.decode() not in text
            output.clear()
            await page.locator('.xterm-helper-textarea').focus()
            await page.keyboard.insert_text(text)
            start=time.monotonic()
            await page.keyboard.press('Enter')
            while (completion is None and marker not in output) or (completion is not None and not re.search(re.escape(completion)+rb'([0-9]{1,3}):END',output)):
                if time.monotonic()-start>timeout:
                    METRICS.setdefault('command_failures',[]).append({'student_index':sessions.index(session)+1,'bytes':len(output),'success_marker':marker in output,'terminal_closed':session[4].get('closed',False),'completion_received':completion in output if completion else None})
                    raise TimeoutError('Shell evidence absent')
                await asyncio.sleep(.05)
            if completion is not None:
                code=int(re.search(re.escape(completion)+rb'([0-9]{1,3}):END',output)[1])
                if code!=0 or marker not in output:
                    METRICS.setdefault('command_failures',[]).append({'student_index':sessions.index(session)+1,'bytes':len(output),'success_marker':marker in output,'terminal_closed':session[4].get('closed',False),'exit_status':code})
                    raise AssertionError('Actual shell command failed')
            return round((time.monotonic()-start)*1000,2)
        timings=await asyncio.gather(*[command(s,
            "test \"$(hostname)\" = "+s[3]+" && "+("test -s /home/cra/academy/README.txt && grep -q NL-104 /home/cra/academy/journal/events.log && test -s /home/cra/academy/arkiv/2026/rapport-104.txt && " if s[2]["mission_id"]=="first-files" else "")+"curl --fail --max-time 5 -s http://"+s[2]['target_ip']+":8080/ >/dev/null && printf 'CAPACITY_%s\\n' VERIFIED",b'CAPACITY_VERIFIED') for s in sessions])
        print(json.dumps({'simultaneous_real_commands':'PASS','latency_ms':timings}),flush=True)
        METRICS['command_completion_ms']=timings
        async def wait_marker(session,marker,timeout=45):
            began=time.monotonic()
            # Launch code splits marker strings, so contiguous markers can only
            # come from actual guest output. tmux may add ANSI around the line.
            while marker not in session[1]:
                if time.monotonic()-began>timeout:
                    METRICS.setdefault('load_timeout_snapshot',marker_diagnostics())
                    raise TimeoutError('Guest load marker absent')
                await asyncio.sleep(.005)
            return time.time()
        async def echo(session):
            page,output,_,_,state=session
            await page.locator('.xterm-helper-textarea').focus();output.clear()
            marker='echo_'+uuid4().hex[:12]
            # Arm before input. Measure inside the page, so initial CDP/script
            # compilation and polling delays cannot masquerade as terminal lag.
            await page.evaluate("""marker=>{
                const input=document.querySelector('.xterm-helper-textarea');
                const rows=document.querySelector('.xterm-rows');
                if(!input||!rows)throw Error('Missing real terminal DOM');
                const sample={started:null,visibleMs:null};window.__craEcho=sample;
                let scheduled=false;
                const check=()=>{
                    if(sample.started===null||scheduled||!rows.textContent.includes(marker))return;
                    const r=rows.getBoundingClientRect();
                    if(r.width<=0||r.height<=0||getComputedStyle(rows).visibility==='hidden')return;
                    scheduled=true;
                    requestAnimationFrame(()=>requestAnimationFrame(()=>{
                        sample.visibleMs=performance.now()-sample.started;
                        observer.disconnect();input.removeEventListener('beforeinput',onInput,true);
                    }));
                };
                const onInput=e=>{if(e.data===marker){sample.started=performance.now();check();}};
                const observer=new MutationObserver(check);
                input.addEventListener('beforeinput',onInput,true);
                observer.observe(rows,{subtree:true,childList:true,characterData:true});
            }""",marker)
            began=time.monotonic()
            await page.keyboard.insert_text(marker)
            elapsed=None
            while elapsed is None:
                # Poll outside the page: Playwright's repeated function poll can
                # require unsafe-eval, which CRA deliberately does not allow.
                elapsed=await page.evaluate('()=>window.__craEcho.visibleMs')
                if time.monotonic()-began>5:raise TimeoutError('Visible input clock absent')
                if elapsed is None:await asyncio.sleep(.01)
            driver_elapsed=(time.monotonic()-began)*1000
            await page.keyboard.press('Control+u')
            return elapsed,driver_elapsed
        normal_echo=[];driver_echo=[]
        for _ in range(5):
            round_values=await asyncio.gather(*[echo(session) for session in sessions])
            normal_echo.extend(value[0] for value in round_values);driver_echo.extend(value[1] for value in round_values)
        METRICS.update(normal_visible_echo_ms=normal_echo,normal_visible_echo_p95_ms=p95(normal_echo),driver_elapsed_ms=driver_echo,echo_threshold_ms=150,api_threshold_ms=300,echo_clock='browser beforeinput to DOM marker plus two animation frames; all five rounds retained')
        # Each real guest waits on stdin before touching 256MiB and starting a 30s load.
        import shlex
        code="import time,sys; print('LOAD_'+'ARMED',flush=True); sys.stdin.readline(); b=bytearray(256*1024*1024); exec('for i in range(0,len(b),4096): b[i]=1'); print('LOAD_'+'STARTED',flush=True); end=time.monotonic()+30; exec('while time.monotonic()<end: sum(range(10000))')"
        load_command='python3 -c '+shlex.quote(code)+" && printf 'LOAD_%s\\n' COMPLETE"
        assert all(marker not in load_command for marker in ('LOAD_ARMED','LOAD_STARTED','LOAD_COMPLETE'))
        await asyncio.gather(*[command(session,load_command,b'LOAD_ARMED') for session in sessions])
        for session in sessions:session[1].clear()
        METRICS['load_release_epoch']=time.time()
        await asyncio.gather(*[session[0].keyboard.press('Enter') for session in sessions])
        starts=await asyncio.gather(*[wait_marker(session,b'LOAD_STARTED') for session in sessions])
        METRICS['all_started_epoch']=max(starts)
        busy_times=[]
        for _ in range(50):
            await asyncio.sleep(.1)
            began=time.monotonic();response=await teacher.request.get(data['url']+'/api/v1/command-center/overview');assert response.ok
            busy_times.append((time.monotonic()-began)*1000)
            assert not any(b'LOAD_COMPLETE' in session[1] for session in sessions)
        METRICS['api_load_end_epoch']=time.time()
        METRICS.update(api_during_load_ms=busy_times,api_during_load_p95_ms=p95(busy_times))
        finishes=await asyncio.gather(*[wait_marker(session,b'LOAD_COMPLETE') for session in sessions])
        METRICS['load_finished_epoch']=max(finishes)
        assert METRICS['api_load_end_epoch']<min(finishes)
        print(f'PASS: {count} guests held 256MiB each with overlapping CPU load; API sampled after all started',flush=True)
        # New simultaneous handshakes, not merely idle sockets retained.
        await asyncio.gather(*[s[0].get_by_role('button',name='Forbind igen',exact=True).click() for s in sessions])
        await asyncio.gather(*[expect(s[0].get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000) for s in sessions])
        await asyncio.gather(*[command(s,"printf 'RECONNECT_%s\\n' VERIFIED",b'RECONNECT_VERIFIED') for s in sessions])
        print(f'PASS: {count} simultaneous reconnects and shell commands',flush=True)
        api_times=[]
        for _ in range(50):
            started=time.monotonic();response=await teacher.request.get(data['url']+'/api/v1/command-center/overview');assert response.ok;api_times.append((time.monotonic()-started)*1000)
        METRICS.update(api_ms=api_times,api_p95_ms=p95(api_times))
        first=sessions[0]
        denied=await first[0].evaluate("""async other=>{const me=await(await fetch('/api/v1/auth/me')).json();return [(await fetch('/api/v1/labs/'+other)).status,(await fetch('/api/v1/labs/'+other+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status]}""",sessions[3][2]['id'])
        assert denied==[404,404]
        await asyncio.sleep(6)
        overview=await teacher.evaluate("async()=> (await fetch('/api/v1/command-center/overview')).json()")
        assert overview['lab_count']==count and len(overview['labs'])==count
        assert all(lab['state']=='READY' and not lab['stale'] for lab in overview['labs'])
        assert len(overview['nodes'])==2 and sum(n['active_labs'] for n in overview['nodes'])==count and all(not n['stale'] for n in overview['nodes'])
        assert {lab['node_id'] for lab in overview['labs']}=={1,2}
        print(json.dumps({'teacher_all_students_ready':'PASS','student_foreign_lab_denied':'PASS','nodes':overview['nodes']}),flush=True)
        METRICS['functional_checks']='PASS'
        METRICS['students']=count
        METRICS['performance_pass']=p95(normal_echo)<=150 and p95(busy_times)<=300 and p95(api_times)<=300
        await browser.close()
    print('PASS: classroom browser functional checks; performance reported separately',flush=True)


if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as error:
        METRICS['guest_marker_diagnostics_after_close']=marker_diagnostics()
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True)
        sys.exit(1)
    finally:
        print('BROWSER_METRICS '+json.dumps(METRICS),flush=True)
