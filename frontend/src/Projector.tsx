import {useEffect,useRef,useState} from 'react';
import type {CenterLab,ProjectorFeed,ProjectorGrant,ProjectorLaunch,UserView} from './contracts';
import {api} from './api';
import {phases} from './Teaching';

// A fragment never reaches HTTP/access logs. Consume it once even under React's
// development remount; no token is kept in storage or rendered into the page.
const screen=new URLSearchParams(location.search).get('screen')??'';
const projectorPath='/api/v1/projector/'+encodeURIComponent(screen);
let exchange:Promise<void>|undefined;
async function boundedJSON<T>(path:string,init?:RequestInit):Promise<T>{const abort=new AbortController();const timer=setTimeout(()=>abort.abort(),4500);try{const r=await fetch(path,{...init,signal:abort.signal});if(!r.ok)throw new Error('Projektoradgangen er lukket eller udløbet. Åbn en ny visning fra Command Center.');return await r.json() as T;}catch(e){if(e instanceof DOMException)throw new Error('Forbindelsen kan ikke bekræftes. Venter på et nyt svar…');throw e;}finally{clearTimeout(timer);}}
function connect(){
 if(exchange)return exchange;
 const token=location.hash.slice(1);history.replaceState(null,'',location.pathname+'?view=projector&screen='+encodeURIComponent(screen));
 exchange=token?boundedJSON(projectorPath+'/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})}).then(()=>{}):Promise.resolve();
 return exchange;
}
const states:Record<string,string>={READY:'Klar',STOPPED:'Stoppet',ERROR:'Teknisk fejl',QUEUED:'I kø',PROVISIONING:'Klargøres',RESETTING:'Nulstilles',STARTING:'Starter',STOPPING:'Stopper',DESTROYING:'Fjernes'};
export function Projector(){
 const [data,setData]=useState<ProjectorFeed|null>(null),[error,setError]=useState(''),[fullscreenError,setFullscreenError]=useState('');
 const last=useRef(0);
 useEffect(()=>{
  let live=true,pending=false;
  async function poll(){if(pending||!live)return;pending=true;
   try{try{await connect();}catch{/* A lost exchange response may already have set the cookie. Feed authorization decides; never repeat the exchange. */}const value=await boundedJSON<ProjectorFeed>(projectorPath+'/feed');if(live){last.current=performance.now();setData(value);setError('');}}
   catch(e){if(live){setData(null);setError((e as Error).message);}}
   finally{pending=false;}
  }
  void poll();const timer=setInterval(()=>{if(last.current&&performance.now()-last.current>5000){setData(null);setError('Forbindelsen kan ikke bekræftes. Venter på et nyt svar…');}void poll();},1000);
  return()=>{live=false;clearInterval(timer);};
 },[]);
 async function fullscreen(){try{await document.documentElement.requestFullscreen();setFullscreenError('');}catch{setFullscreenError('Fuldskærm kunne ikke åbnes. Brug browserens fuldskærmsmenu.');}}
 return <main className="projector"><header className="projector-header"><div className="brand">CRA <span>CYBER RANGE ACADEMY</span></div><button className="quiet" onClick={()=>void fullscreen()}>Fuldskærm</button></header><div className="projector-heading"><div><p className="eyebrow">NORDLYS / FÆLLES SITUATIONSBILLEDE</p><h1>Undersøg. <em>Forstå.</em></h1><p>{data?.day?`Dag ${data.day} · ${data.phase?phases[data.phase]:''}`:'Klar til fælles mission'}</p></div><div className="projector-live"><span className={'dot '+(data?'ready':'checking')}/>{data?'Direkte observationer':'Afventer forbindelse'}</div></div>
 {fullscreenError&&<p role="status">{fullscreenError}</p>}{error&&<p role="alert" className="notice">{error}</p>}
 {!data&&!error&&<p role="status">Forbinder projektor…</p>}
 {data&&<><section className="projector-grid" aria-label="Anonymiserede hold">{data.labs.map(l=><article className="projector-card" key={l.ordinal}><div className="section-heading"><h2>{l.alias}</h2><span className="status-chip">{states[l.state]??'Afventer'}{l.stale?' · ubekræftet':''}</span></div><svg viewBox="0 0 400 120" role="img" aria-label={l.activity==='observed'?'Trafik observeret i kendt scope':l.activity==='quiet'?'Ingen nylig IP-trafik observeret':'Netværksobservation ubekræftet'}><line x1="75" y1="50" x2="325" y2="50" stroke={l.activity==='observed'?'#6de8bc':'#46606d'} strokeWidth="3" strokeDasharray={l.activity==='unconfirmed'?'7 7':undefined}/><circle cx="75" cy="50" r="24" fill="#132934" stroke="#77a5ba"/><rect x="301" y="26" width="48" height="48" rx="8" fill="#132934" stroke="#77a5ba"/><text x="75" y="102" textAnchor="middle">Operatører</text><text x="325" y="102" textAnchor="middle">Kendt scope</text></svg><p className="projector-activity">{l.activity==='observed'?'Trafik observeret · seneste 10 sekunder':l.activity==='quiet'?'Ingen nylig IP-trafik observeret':'Netværksobservation ubekræftet'}</p>{l.observation_incomplete&&<small>Observationen er begrænset; enkelte vinduer mangler.</small>}<div className="objective-progress" aria-label={`${l.completed} af ${l.objectives} opgavemål valideret`}><i style={{width:`${l.completed/l.objectives*100}%`}}/></div><div className="projector-score"><span><strong>{l.completed}/{l.objectives}</strong> validerede mål</span><span><strong>{l.points}</strong> point</span></div><small>Generation {l.generation} · point bevares ved øvelsesreset</small></article>)}</section>{!data.labs.length&&<p className="notice">Ingen af de valgte miljøer er aktive eller bevaret længere.</p>}<footer>Trafik er en observation, ikke bevis for forståelse eller et løst opgavemål. Point kommer fra svarvalidering.</footer></>}
 </main>;
}

export function ProjectorControl({user,labs}:{user:UserView;labs:CenterLab[]}){
 const [selected,setSelected]=useState<string[]>([]),[grants,setGrants]=useState<ProjectorGrant[]>([]),[listError,setListError]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const pending=useRef(false);
 async function refresh(){try{setGrants(await api<ProjectorGrant[]>('/command-center/projectors'));setListError('');}catch{setListError('Adgangslisten kunne ikke opdateres. Den hentes igen automatisk.');}}
 useEffect(()=>{let live=true;async function load(){try{const value=await api<ProjectorGrant[]>('/command-center/projectors');if(live){setGrants(value);setListError('');}}catch{if(live)setListError('Projektoradgange kunne ikke hentes.');}}void load();const timer=setInterval(()=>void load(),5000);return()=>{live=false;clearInterval(timer);};},[]);
 async function launch(){if(pending.current)return;const ids=selected.filter(id=>labs.some(l=>l.id===id));if(!ids.length)return;
  const popup=window.open('about:blank','_blank');if(!popup){setError('Tillad et nyt vindue for denne side, og prøv igen.');return;}popup.opener=null;pending.current=true;setBusy(true);setError('');setNotice('');
  try{const value=await api<ProjectorLaunch>('/command-center/projectors',user,{lab_ids:ids});if(popup.closed){await api('/command-center/projectors/'+value.grant.id+'/revoke',user,{});throw new Error('Vinduet blev lukket. Adgangen er tilbagekaldt.');}popup.location.replace('/?view=projector&screen='+value.grant.id+'#'+value.token);setNotice('Separat projektor åbnet. Adgangen gælder i højst otte timer.');await refresh();}
  catch(e){popup.close();setError((e as Error).message);}finally{pending.current=false;setBusy(false);}
 }
 async function revoke(id:string){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{await api('/command-center/projectors/'+id+'/revoke',user,{});await refresh();setNotice('Adgangen er tilbagekaldt. Projektoren rydder billedet ved næste svar.');}catch(e){setError((e as Error).message);}finally{pending.current=false;setBusy(false);}}
 return <details className="projector-control learning-card"><summary>Projektor · vælg miljøer og administrér adgang</summary><p>Vis anonymiserede hold, valideret fremdrift og aktivitet i kendt scope. Vælg de miljøer, klassen skal se.</p><div className="projector-select">{labs.map(l=><label key={l.id}><input type="checkbox" disabled={busy} checked={selected.includes(l.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,l.id].slice(0,20):old.filter(id=>id!==l.id))}/>{l.team_name??l.owner_display_name}</label>)}</div><button disabled={busy||!selected.some(id=>labs.some(l=>l.id===id))} onClick={()=>void launch()}>Åbn sikker projektor</button>{notice&&<p role="status">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}{listError&&<p className="error" role="alert">{listError}</p>}<ul className="projector-grants">{grants.map(g=><li key={g.id}><span>{g.lab_count} miljøer · åbnet {new Date(g.created_at).toLocaleTimeString('da-DK')} · {g.revoked?'Tilbagekaldt':g.exchanged?'Projektor tilsluttet':'Afventer åbning'} · udløber {new Date(g.expires_at).toLocaleTimeString('da-DK')}</span>{!g.revoked&&<button className="quiet" disabled={busy} onClick={()=>void revoke(g.id)}>Tilbagekald adgang</button>}</li>)}</ul></details>;
}
