import {useEffect,useRef,useState} from 'react';
import RFB from '@novnc/novnc';

type Props={labId:string;generation:number;csrf:string};
export default function Desktop({labId,generation,csrf}:Props){
 const [opened,setOpened]=useState(false),[status,setStatus]=useState('Lukket'),[retry,setRetry]=useState(0);
 const host=useRef<HTMLDivElement>(null),client=useRef<RFB|null>(null);
 useEffect(()=>{
  if(!opened||!host.current)return;let live=true,ws:WebSocket|null=null,rfb:RFB|null=null;let timer:ReturnType<typeof setTimeout>|undefined;
  const abort=new AbortController();setStatus('Forbinder til din operator…');
  function fail(){if(!live)return;setStatus('Skrivebordets forbindelse er lukket.');ws?.close();}
  async function connect(){
   try{
    const response=await fetch(`/api/v1/labs/${labId}/desktop-grants`,{method:'POST',headers:{'X-CRA-CSRF':csrf},signal:abort.signal});
    if(!response.ok)throw new Error('Desktop access denied');const {grant}=await response.json();if(!live)return;
    const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/desktop/${labId}`);ws=socket;socket.binaryType='arraybuffer';
    const rawSend=socket.send.bind(socket);
    let pending:Uint8Array[]=[];let pendingBytes=0;let scheduled=false;
    // noVNC input and control writes cannot accumulate without a bound in the browser.
    // A shortcut emits several tiny key frames synchronously. Coalesce that burst without
    // increasing the relay's four-frame queue or losing ordering within the RFB byte stream.
    socket.send=(data)=>{
     const size=typeof data==='string'?data.length:data instanceof Blob?data.size:data.byteLength;
     if(size>16384||pendingBytes+socket.bufferedAmount+size>65536){fail();return;}
     if(typeof data==='string'){rawSend(data);return;}
     if(data instanceof Blob){fail();return;}
     const bytes=data instanceof ArrayBuffer?new Uint8Array(data):new Uint8Array(data.buffer,data.byteOffset,data.byteLength);
     pending.push(bytes.slice());pendingBytes+=size;
     if(!scheduled){scheduled=true;queueMicrotask(()=>{
      scheduled=false;const merged=new Uint8Array(pendingBytes);let offset=0;for(const part of pending){merged.set(part,offset);offset+=part.length;}pending=[];pendingBytes=0;
      if(!live||socket.readyState!==WebSocket.OPEN)return;
      for(let start=0;start<merged.length;start+=16384)rawSend(merged.subarray(start,start+16384));
     });}
    };
    socket.onopen=()=>socket.send(JSON.stringify({type:'auth',grant,csrf,cols:100,rows:30}));
    socket.onerror=fail;socket.onclose=fail;timer=setTimeout(fail,20000);
    socket.onmessage=(event)=>{
     if(!live)return;
     try{
      if(typeof event.data!=='string'||event.data.length>256)throw new Error('Expected readiness');
      const ready=JSON.parse(event.data);if(ready.type!=='ready'||ready.window!==65536)throw new Error('Unexpected readiness');
      rfb=new RFB(host.current!,socket,{shared:true});client.current=rfb;
      rfb.scaleViewport=true;rfb.resizeSession=false;rfb.focusOnClick=true;
      rfb.addEventListener('connect',()=>{if(timer)clearTimeout(timer);if(live)setStatus('Forbundet · grafisk Linux-skrivebord');});
      rfb.addEventListener('disconnect',fail);rfb.addEventListener('securityfailure',fail);
      // RFB attaches synchronously. Wrap its handler before the next event: no lost initial bytes,
      // ACK only after bounded parsing; no clipboard API and no RFB bytes in React state.
      const receive=socket.onmessage;let received=0;
      if(!receive)throw new Error('RFB receiver unavailable');
      socket.onmessage=(frame)=>{try{if(!(frame.data instanceof ArrayBuffer)||frame.data.byteLength>16384)throw new Error('Invalid desktop frame');receive.call(socket,frame);received+=frame.data.byteLength;if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'ack',bytes:received}));}catch{fail();}};
     }catch{fail();}
    };
   }catch{if(!abort.signal.aborted)fail();}
  }
  void connect();return()=>{live=false;abort.abort();if(timer)clearTimeout(timer);client.current=null;rfb?.disconnect();ws?.close();host.current?.replaceChildren();};
 },[opened,labId,generation,csrf,retry]);
 function launch(key:number,code:string){const rfb=client.current;if(!rfb)return;rfb.sendKey(0xffe3,'ControlLeft',true);rfb.sendKey(0xffe9,'AltLeft',true);rfb.sendKey(key,code,true);rfb.sendKey(key,code,false);rfb.sendKey(0xffe9,'AltLeft',false);rfb.sendKey(0xffe3,'ControlLeft',false);rfb.focus();}
 return <section className="desktop-panel" aria-label="Grafisk labdesktop"><div className="section-heading"><div><h3>Grafisk labdesktop</h3><p role="status">{status}</p></div><button className="secondary" onClick={()=>setOpened(v=>!v)}>{opened?'Luk skrivebord':'Åbn skrivebord'}</button></div>{opened&&<><p>Browser og Wireshark kører i din egen operator. Brug kun det tildelte mål. Udklipsholderen deles ikke med din computer.</p><div className="desktop-actions"><button className="secondary" onClick={()=>launch(0x62,'KeyB')}>Åbn labbrowser</button><button className="secondary" onClick={()=>launch(0x77,'KeyW')}>Åbn Wireshark</button><button className="secondary" onClick={()=>launch(0x74,'KeyT')}>Åbn grafisk terminal</button><button className="quiet" onClick={()=>setRetry(v=>v+1)}>Forbind skrivebord igen</button></div><div ref={host} className="desktop-screen" aria-label="Din operators skærm"/><p className="muted">Skærmen skaleres til vinduet. Stop/start bevarer filer og browserprofil; åbne programmer starter på ny.</p></>}</section>;
}
