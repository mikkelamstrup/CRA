"""Synthetic DOM clock probe only; no VM/performance acceptance claim."""
import asyncio,json,sys,time,traceback
from uuid import uuid4
from playwright.async_api import async_playwright
async def echo(session):
    page, output, _, _, state = session
    await page.locator('.xterm-helper-textarea').focus()
    output.clear()
    marker = 'echo_' + uuid4().hex[:12]
    await page.evaluate("marker=>{\n                const input=document.querySelector('.xterm-helper-textarea');\n                const rows=document.querySelector('.xterm-rows');\n                if(!input||!rows)throw Error('Missing real terminal DOM');\n                const sample={started:null,visibleMs:null};window.__craEcho=sample;\n                let scheduled=false;\n                const check=()=>{\n                    if(sample.started===null||scheduled||!rows.textContent.includes(marker))return;\n                    const r=rows.getBoundingClientRect();\n                    if(r.width<=0||r.height<=0||getComputedStyle(rows).visibility==='hidden')return;\n                    scheduled=true;\n                    requestAnimationFrame(()=>requestAnimationFrame(()=>{\n                        sample.visibleMs=performance.now()-sample.started;\n                        observer.disconnect();input.removeEventListener('beforeinput',onInput,true);\n                    }));\n                };\n                const onInput=e=>{if(e.data===marker){sample.started=performance.now();check();}};\n                const observer=new MutationObserver(check);\n                input.addEventListener('beforeinput',onInput,true);\n                observer.observe(rows,{subtree:true,childList:true,characterData:true});\n            }", marker)
    began = time.monotonic()
    await page.keyboard.insert_text(marker)
    elapsed = None
    while elapsed is None:
        elapsed = await page.evaluate('()=>window.__craEcho.visibleMs')
        if time.monotonic() - began > 5:
            raise TimeoutError('Visible input clock absent')
        if elapsed is None:
            await asyncio.sleep(0.01)
    driver_elapsed = (time.monotonic() - began) * 1000
    await page.keyboard.press('Control+u')
    return (elapsed, driver_elapsed)

async def main():
 b=json.load(sys.stdin)
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
  sessions=[]
  for i in range(15):
   context=await browser.new_context();page=await context.new_page();await page.goto(b['url'])
   await page.evaluate("""()=>{
    document.body.innerHTML='<textarea class="xterm-helper-textarea"></textarea><div class="xterm-rows" style="width:500px;height:50px;position:relative"></div>';
    const input=document.querySelector('textarea'),rows=document.querySelector('.xterm-rows');
    input.addEventListener('input',e=>setTimeout(()=>rows.textContent=e.data,50));
    input.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='u'){e.preventDefault();input.value='';rows.textContent='';}});
   }""")
   sessions.append((page,bytearray(),None,None,{}))
  try:
   for i in range(2):
    values=await asyncio.gather(*[echo(s) for s in sessions]);print(json.dumps({'round':i,'synthetic_clock_values':values}),flush=True)
  except Exception as error:
   print(type(error).__name__+': '+str(error).splitlines()[0],flush=True)
   for s in sessions[:2]:print(json.dumps(await s[0].evaluate('window.__craEcho')),flush=True)
   raise
  finally:await browser.close()

if __name__=='__main__':asyncio.run(main())
