"""Explicit malicious-RFB fixtures against the shipped browser decoder; NOT VM evidence."""
import json
import struct
import sys
import time
import traceback
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright,expect
from teaching import login

MARKER='SYNTHETIC_RFB_PRIVATE_MARKER'


def fixture(route,mode):
    state={'phase':'auth','sent':0,'acked':0,'queued':False,'attack_sent':False,'errors':[]}
    def binary(data):
        for start in range(0,len(data),16384):
            part=data[start:start+16384];route.send(part);state['sent']+=len(part)
    def pump():
        if not state['queued']:return
        while state['sent']-state['acked']<65536 and state['sent']<10*1024*1024:
            size=min(16384,65536-(state['sent']-state['acked']))
            binary(b'\0'*size)
    def message(data):
        try:
            if isinstance(data,str):
                msg=json.loads(data)
                if msg['type']=='auth':
                    state['phase']='protocol';route.send(json.dumps({'type':'ready','window':65536}));binary(b'RFB ');binary(b'003.008\n')
                elif msg['type']=='ack':state['acked']=msg['bytes'];pump()
                return
            phase=state['phase']
            if phase=='protocol':
                assert data==b'RFB 003.008\n';state['phase']='security';binary(b'\x01\x01')
            elif phase=='security':
                assert data==b'\x01'
                if mode=='reason':
                    state['phase']='done';state['attack_sent']=True;value=MARKER.encode();binary(struct.pack('!II',1,len(value))+value)
                else:state['phase']='shared';binary(b'\0'*4)
            elif phase=='shared':
                assert data==b'\x01';state['phase']='done';state['attack_sent']=True;w,h=(65535,65535) if mode=='huge_screen' else (64,64)
                name=b'Synthetic fixture';binary(struct.pack('!HH',w,h)+struct.pack('!BBBBHHHBBBxxx',32,24,0,1,255,255,255,16,8,0)+struct.pack('!I',len(name))+name)
                if mode=='desktop_name':binary(b'\0\0\0\x01'+struct.pack('!HHHHiI',0,0,0,0,-307,5000))
                elif mode=='huge_rectangle':binary(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,65535,65535,0))
                elif mode=='compressed_image':binary(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,64,64,7))
                elif mode=='clipboard':
                    value=MARKER.encode();binary(b'\x03\0\0\0'+struct.pack('!I',len(value))+value)
                    # Extended compressed clipboard is discarded too; it is deliberately invalid compressed data.
                    value=struct.pack('!I',0x10000001)+b'not-valid-zlib-'+MARKER.encode();binary(b'\x03\0\0\0'+struct.pack('!i',-len(value))+value)
                elif mode=='queue':
                    binary(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,1920,1080,-239));state['queued']=True;pump()
        except AssertionError:
            state['errors'].append('fixture_handshake');route.close()
        except Exception as error:
            if not state['attack_sent']:state['errors'].append('fixture_before_attack_'+type(error).__name__)
            route.close()
    route.on_message(message)
    return state


def main():
    b=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.add_init_script("window.craClipboardCalls=0;Object.defineProperty(navigator,'clipboard',{value:{readText:async()=>{window.craClipboardCalls++;return 'synthetic'},writeText:async()=>{window.craClipboardCalls++}}});")
        if b.get('fixture_only'):
            # Explicit UI fixtures for rapid decoder tests; no database, VM or access proof.
            def api_fixture(route):
                path=route.request.url.split('/api/v1',1)[1]
                value=[]
                if path=='/auth/me':value={'user_id':'11111111-1111-4111-8111-111111111111','organization_id':'22222222-2222-4222-8222-222222222222','username':'synthetic-rfb','role':'teacher','csrf_token':'f'*64}
                elif path=='/teaching/session':value=None
                elif path=='/missions':value=b['missions']
                elif path=='/labs':value=[{'id':'33333333-3333-4333-8333-333333333333','mission_id':'first-contact','mission_version':1,'generation':1,'state':'READY','desired_state':'READY','target_ip':'192.0.2.10','last_observed_at':datetime.now(timezone.utc).isoformat(),'error_code':None,'team_id':None,'desktop_enabled':True,'can_manage':False,'can_operate':True}]
                elif path.endswith('/desktop-grants'):value={'grant':'fixture-only-not-a-real-grant'}
                elif path.endswith('/terminal-grants'):route.fulfill(status=403,json={'detail':'fixture_terminal_disabled'});return
                elif path.endswith('/progress'):value={'total_points':0,'completed_objectives':[],'earned_objectives':[]}
                route.fulfill(json=value)
            context.route('**/api/v1/**',api_fixture)
        else:
            first=context.new_page();login(first,b['users']['teacher'],b.get('url','http://127.0.0.1:8000'));first.close()
        for mode in ['huge_screen','huge_rectangle','desktop_name','compressed_image','reason','queue','clipboard']:
            page=context.new_page();messages=[];states=[]
            page.on('console',lambda message:messages.append(message.text))
            page.on('pageerror',lambda error:messages.append(str(error)))
            page.route_web_socket('**/desktop/*',lambda route,mode=mode:states.append(fixture(route,mode)))
            page.goto(b.get('url','http://127.0.0.1:8000')+'/')
            # Playwright's WebSocket subclass puts native-like accessors two prototypes up.
            # noVNC checks own/first-prototype properties; expose the same mock accessors locally.
            page.evaluate("""() => {window.WebSocket=new Proxy(window.WebSocket,{construct(Type,args){
              const socket=new Type(...args);
              if(String(args[0]).includes('/desktop/')){
                for(const name of ['send','close','binaryType','onerror','onmessage','onopen','protocol','readyState']){
                  if(Object.hasOwn(socket,name))continue;
                  let owner=Object.getPrototypeOf(socket);while(owner&&!Object.hasOwn(owner,name))owner=Object.getPrototypeOf(owner);
                  const descriptor=owner&&Object.getOwnPropertyDescriptor(owner,name);
                  if(!descriptor)throw new Error('Incomplete fixture socket');
                  Object.defineProperty(socket,name,descriptor.value?{value:descriptor.value.bind(socket),writable:true,configurable:true,enumerable:true}:{...descriptor,configurable:true,enumerable:true});
                }
              }
              return socket;
            }});}""")
            page.get_by_role('button',name='⌘ Mit lab' if b.get('fixture_only') else '◇ Mit hold',exact=True).click();page.get_by_role('button',name='Åbn skrivebord',exact=True).click()
            if mode=='clipboard':
                expect(page.get_by_text('Forbundet · grafisk Linux-skrivebord',exact=True)).to_be_visible(timeout=20000);page.wait_for_timeout(500)
                assert page.evaluate('window.craClipboardCalls===0')
            else:expect(page.get_by_text('Skrivebordets forbindelse er lukket.',exact=True)).to_be_visible(timeout=30000)
            if not (len(states)==1 and states[0]['attack_sent'] and not states[0]['errors']):
                print('FAIL: fixture diagnostics '+mode+' '+json.dumps(states),flush=True)
                raise AssertionError('Fixture did not reach attack')
            if mode=='queue':assert 8*1024*1024<=states[0]['sent']<9*1024*1024
            assert not any(MARKER in message for message in messages)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('.desktop-screen canvas').evaluate_all('(items)=>items.every(c=>c.width<=1920 && c.height<=1080)')
            page.get_by_role('button',name='Luk skrivebord',exact=True).click()
            print('PASS: explicit RFB fixture '+mode+'; no private console/clipboard output, page remains responsive',flush=True)
            page.close()
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
