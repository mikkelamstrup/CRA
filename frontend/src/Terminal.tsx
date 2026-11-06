import {useEffect,useRef,useState} from 'react';
import {Terminal as Xterm} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

export default function Terminal({labId,generation,csrf}:{labId:string;generation:number;csrf:string}) {
  const container=useRef<HTMLDivElement>(null);
  const [attempt,setAttempt]=useState(0);
  const [status,setStatus]=useState('Forbinder…');
  const [connected,setConnected]=useState(false);
  useEffect(()=>{
    const element=container.current;if(!element)return;
    let disposed=false;let socket:WebSocket|undefined;let ready=false;let received=0;
    const terminal=new Xterm({cursorBlink:true,disableStdin:true,fontSize:14,fontFamily:'ui-monospace, SFMono-Regular, Menlo, monospace',scrollback:3000,convertEol:false,allowProposedApi:false,theme:{background:'#081115',foreground:'#d8e7e6',cursor:'#68e0ba',selectionBackground:'#315a53'}});
    const fit=new FitAddon();terminal.loadAddon(fit);terminal.open(element);fit.fit();
    setStatus('Forbinder…');setConnected(false);
    const dimensions=()=>({cols:Math.max(10,Math.min(240,terminal.cols)),rows:Math.max(2,Math.min(100,terminal.rows))});
    const resize=new ResizeObserver(()=>{fit.fit();if(ready&&socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'resize',...dimensions()}));});resize.observe(element);
    function sendInput(bytes:Uint8Array){
      if(!ready||socket?.readyState!==WebSocket.OPEN)return;
      if(socket.bufferedAmount+bytes.length>65536){setStatus('Inputkøen er fuld. Forbind igen.');socket.close();return;}
      for(let offset=0;offset<bytes.length;offset+=16384)socket.send(bytes.slice(offset,offset+16384));
    }
    const input=terminal.onData(data=>sendInput(new TextEncoder().encode(data)));
    const binary=terminal.onBinary(data=>sendInput(Uint8Array.from(data,c=>c.charCodeAt(0)&255)));
    const abort=new AbortController();
    async function connect(){
      try{
        const response=await fetch(`/api/v1/labs/${labId}/terminal-grants`,{method:'POST',headers:{'X-CRA-CSRF':csrf},signal:abort.signal});
        if(!response.ok)throw new Error('Terminalen er ikke tilgængelig. Kontrollér labbet eller log ind igen.');
        const grant=await response.json();if(disposed)return;
        socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/terminal/${labId}`);socket.binaryType='arraybuffer';
        socket.onopen=()=>socket?.send(JSON.stringify({type:'auth',grant:grant.grant,csrf,...dimensions()}));
        socket.onmessage=event=>{
          if(disposed)return;
          if(event.data instanceof ArrayBuffer){
            const bytes=new Uint8Array(event.data);received+=bytes.length;const acknowledged=received;
            terminal.write(bytes,()=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'ack',bytes:acknowledged}));});
          }else{
            try{const message=JSON.parse(event.data);if(message.type==='ready'){ready=true;terminal.options.disableStdin=false;setConnected(true);setStatus('Forbundet · rigtig Linux-shell');terminal.focus();}}
            catch{socket?.close();}
          }
        };
        socket.onclose=()=>{ready=false;if(!disposed){setConnected(false);setStatus('Forbindelsen er lukket. Shellens session bevares, mens labbet kører.');}};
        socket.onerror=()=>{if(!disposed)setStatus('Terminalforbindelsen kunne ikke oprettes.');};
      }catch(error){if(!disposed&&!(error instanceof DOMException&&error.name==='AbortError'))setStatus(error instanceof Error?error.message:'Terminalen svarer ikke.');}
    }
    const startup=setTimeout(()=>void connect(),0);
    return()=>{clearTimeout(startup);disposed=true;abort.abort();socket?.close();resize.disconnect();input.dispose();binary.dispose();terminal.dispose();};
  },[labId,generation,csrf,attempt]);
  return <section className="terminal-panel" aria-label="Operatørterminal"><div className="terminal-heading"><div><h2>Operatørterminal</h2><span className="terminal-status" role="status">{status}</span></div><button className="quiet" onClick={()=>{setConnected(false);setStatus('Forbinder…');setAttempt(x=>x+1);}}>{connected?'Forbind igen':'Genopret forbindelse'}</button></div><div ref={container} className="terminal-screen"/><div className="terminal-help">Skriv direkte i terminalen · Ctrl+C afbryder en kommando · Undersøg kun dit tildelte mål</div></section>;
}
