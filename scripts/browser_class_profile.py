"""Fifteen synthetic team operators through local HTTP; credentials exclusively on stdin."""
import asyncio
import json
import sys
import time
import traceback
from playwright.async_api import async_playwright, expect


async def main():
    data = json.load(sys.stdin)
    students = [a for a in data['accounts'] if a['role'] == 'student' and a['username'] != 'testelev']
    assert len(students) == 15
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        check = await browser.new_page()
        await check.goto('chrome://sandbox')
        assert 'You are adequately sandboxed.' in await check.locator('body').inner_text()
        await check.close()
        sessions = []
        for account in students + [next(a for a in data['accounts'] if a['role'] == 'teacher')]:
            context = await browser.new_context(viewport={'width':1440,'height':1000})
            page = await context.new_page()
            output = bytearray()
            def watch(ws, output=output):
                def received(frame):
                    if isinstance(frame,bytes):
                        output.extend(frame)
                        if len(output)>262144:del output[:-131072]
                ws.on('framereceived',received)
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
            await page.get_by_role('button',name='◇ Mit hold',exact=True).click()
            await expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
            sessions.append((page,output,labs[0],account['expected_operator']))
            print('Synthetic browser shell connected:',len(sessions),flush=True)
        async def command(session, text, marker, timeout=30):
            page,output,lab,name=session
            output.clear()
            await page.locator('.xterm-helper-textarea').focus()
            await page.keyboard.insert_text(text)
            start=time.monotonic()
            await page.keyboard.press('Enter')
            while marker not in output:
                if time.monotonic()-start>timeout:raise TimeoutError('Shell evidence absent for '+name)
                await asyncio.sleep(.05)
            return round((time.monotonic()-start)*1000,2)
        timings=await asyncio.gather(*[command(s,
            "test \"$(hostname)\" = "+s[3]+" && "+("test -s /home/cra/academy/README.txt && grep -q NL-104 /home/cra/academy/journal/events.log && test -s /home/cra/academy/arkiv/2026/rapport-104.txt && " if s[2]["mission_id"]=="first-files" else "")+"curl --fail --max-time 5 -s http://"+s[2]['target_ip']+":8080/ >/dev/null && printf 'CAPACITY_%s\\n' VERIFIED",b'CAPACITY_VERIFIED') for s in sessions])
        print(json.dumps({'fifteen_simultaneous_real_commands':'PASS','latency_ms':timings}),flush=True)
        # Touch each allocated memory page, then exercise guest CPU for ten seconds.
        import shlex
        code="import time; b=bytearray(256*1024*1024); exec('for i in range(0,len(b),4096): b[i]=1'); end=time.monotonic()+10; exec('while time.monotonic()<end: sum(range(10000))')"
        await asyncio.gather(*[command(s,'python3 -c '+shlex.quote(code)+" && printf 'LOAD_%s\\n' COMPLETE",b'LOAD_COMPLETE',timeout=45) for s in sessions])
        print('PASS: fifteen guests touched 256MiB each and ran ten seconds of CPU work',flush=True)
        # New simultaneous handshakes, not merely idle sockets retained.
        await asyncio.gather(*[s[0].get_by_role('button',name='Forbind igen',exact=True).click() for s in sessions])
        await asyncio.gather(*[expect(s[0].get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000) for s in sessions])
        await asyncio.gather(*[command(s,"printf 'RECONNECT_%s\\n' VERIFIED",b'RECONNECT_VERIFIED') for s in sessions])
        print('PASS: 15 simultaneous reconnects and shell commands',flush=True)
        api_times=[]
        for _ in range(50):
            started=time.monotonic();response=await teacher.request.get(data['url']+'/api/v1/command-center/overview');assert response.ok;api_times.append((time.monotonic()-started)*1000)
        print(json.dumps({'api_samples':50,'api_p95_ms':round(sorted(api_times)[47],2),'terminal_samples':len(timings),'terminal_p95_ms':round(sorted(timings)[-1],2)}),flush=True)
        first=sessions[0]
        denied=await first[0].evaluate("""async other=>{const me=await(await fetch('/api/v1/auth/me')).json();return [(await fetch('/api/v1/labs/'+other)).status,(await fetch('/api/v1/labs/'+other+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status]}""",sessions[3][2]['id'])
        assert denied==[404,404]
        await asyncio.sleep(6)
        overview=await teacher.evaluate("async()=> (await fetch('/api/v1/command-center/overview')).json()")
        assert overview['lab_count']==5 and len(overview['labs'])==5
        assert all(lab['state']=='READY' and not lab['stale'] for lab in overview['labs'])
        assert overview['capacity']['active_labs']==5 and overview['capacity']['active_limit']==15 and not overview['capacity']['stale']
        print(json.dumps({'teacher_all_fifteen_ready':'PASS','student_foreign_lab_denied':'PASS','capacity':overview['capacity']}),flush=True)
        await browser.close()
    print('PASS: classroom browser capacity acceptance',flush=True)


if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as error:
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True)
        sys.exit(1)
