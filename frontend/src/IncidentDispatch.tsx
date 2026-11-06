import {useEffect,useRef,useState} from 'react';
import type {AssignmentAlert,UserView} from './contracts';
import {api} from './api';

const alertKey=(value:AssignmentAlert)=>value.guidance_id??`${value.mission_id}:${value.mission_version}`;

/** Mounted at workspace level: dispatch also reaches students using the terminal. */
export function IncidentDispatch({user,openCase}:{user:UserView;openCase:()=>boolean}) {
 const [alert,setAlert]=useState<AssignmentAlert|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const dialog=useRef<HTMLDialogElement>(null),heading=useRef<HTMLHeadingElement>(null),acknowledged=useRef(new Set<string>()),deferred=useRef(false),accepting=useRef(false);
 const key=alert?alertKey(alert):null;
 useEffect(()=>{
  if(user.role!=='student')return;
  let live=true,pending=false;const abort=new AbortController();
  async function poll(){
   if(pending||accepting.current||deferred.current)return;pending=true;
   try{const response=await fetch('/api/v1/assignments/alert',{signal:abort.signal});
    if(response.ok){const value=await response.json() as AssignmentAlert|null;if(live&&!accepting.current&&!deferred.current)setAlert(value&&!acknowledged.current.has(alertKey(value))?value:null);}
   }catch{/* Reconnect automatically; no acknowledgement is lost. */}finally{pending=false;}
  }
  void poll();const timer=setInterval(()=>void poll(),3000);
  const visible=()=>{if(document.visibilityState==='visible')void poll();};document.addEventListener('visibilitychange',visible);
  return()=>{live=false;abort.abort();clearInterval(timer);document.removeEventListener('visibilitychange',visible);};
 },[user.user_id,user.role]);
 useEffect(()=>{
  if(!alert)return;
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const node=dialog.current;node?.showModal();
  heading.current?.focus({preventScroll:true});
  if(dialog.current)dialog.current.scrollTop=0;
  setError('');
  return()=>{node?.close();previous?.focus();};
 },[key]);
 async function accept(open:boolean){
  if(busy||!alert)return;
  if(open&&!openCase())return;
  accepting.current=true;setBusy(true);setError('');
  const endpoint=alert.guidance_id?`/assignments/guidance/${alert.guidance_id}/acknowledge`:'/assignments/cybercon-incident/acknowledge';
  try{await api(endpoint,user,{});acknowledged.current.add(alertKey(alert));setAlert(null);}
  catch(e){setError((e as Error).message);}finally{accepting.current=false;setBusy(false);}
 }
 if(!alert)return null;
 return <dialog ref={dialog} className="incident-dispatch" aria-labelledby="incident-title" aria-describedby="incident-message" onCancel={e=>e.preventDefault()}>
  <div className="incident-kicker"><span>CYBERCON / SECURITY OPERATIONS</span><strong>ØVELSE</strong></div>
  <p className="incident-priority">{alert.guidance_id?'OPGAVE 13 · HJÆLP FRA DIN LÆRER':'PRIORITET 1 · INDIVIDUEL INDSATS'}</p>
  <h1 id="incident-title" ref={heading} tabIndex={-1}>{alert.title}</h1>
  <p className="incident-sender">{alert.guidance_id?'Til dig, der arbejder med Cybercon · Alarm hos Nordtele':'Fra: Cybercons indsatsleder · Til: Vagthavende sikkerhedsanalytiker'}</p>
  <div id="incident-message">{alert.message.split(/(```[\s\S]*?```)/g).map((part,i)=>part.startsWith('```')?<pre key={i}><code>{part.replace(/^```[^\n]*\n/,'').replace(/```$/,'').trimEnd()}</code></pre>:part.split('\n\n').filter(Boolean).map((p,j)=><p key={`${i}-${j}`}>{p}</p>))}</div>
  <div className="incident-actions">{alert.guidance_id?<button disabled={busy} onClick={()=>void accept(false)}>Forstået · tilbage til opgaven</button>:<><button disabled={busy} onClick={()=>void accept(true)}>Modtag sagen og åbn opgaven</button><button className="secondary" disabled={busy} onClick={()=>void accept(false)}>Modtaget · bliv på denne side</button></>}</div>
  {error&&<div><p role="alert" className="error">{error} Beskeden er ikke kvitteret endnu.</p><button className="secondary" onClick={()=>{deferred.current=true;setAlert(null);}}>Luk beskeden · kvittér ved næste besøg</button></div>}
 </dialog>;
}
