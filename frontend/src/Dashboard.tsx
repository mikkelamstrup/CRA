import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import {ScorePanel} from './ScorePanel';
import {Assignments} from './Assignments';
import {MissionSwitch} from './MissionSwitch';
const Terminal=lazy(()=>import('./Terminal'));
const Desktop=lazy(()=>import('./Desktop'));
import type { LabView, Mission, UserView } from './contracts';

const labels: Record<string,string> = {QUEUED:'I kø',PROVISIONING:'Klargør miljø',READY:'Klar',STOPPING:'Stopper',STOPPED:'Stoppet',STARTING:'Starter',RESETTING:'Nulstiller',DESTROYING:'Fjerner',DESTROYED:'Fjernet',ERROR:'Kræver handling'};
const errors: Record<string,string> = {stop_solo_first:'Deltagernes sololabs skal være stoppet og bekræftet, før holdmiljøet kan starte. Filer og gemte noter bevares.',stop_team_first:'Holdmiljøet skal være stoppet og bekræftet, før du kan fortsætte solo.',mission_not_released:'Læreren har endnu ikke frigivet denne mission.',individual_mission_required:'Opgaven er individuel — vælg den uden hold.',observation_stale:'Miljøets tilstand er ikke bekræftet for nylig. Terminalen afventer en ny kontrol.',resource_missing:'En af miljøets maskiner mangler. Nulstil eller fjern miljøet.',runtime_drift:'En maskine er stoppet uventet. Prøv at genstarte miljøet; dine filer bevares.',ownership_mismatch:'Miljøets ejerskab kunne ikke bekræftes. Kontakt den lokale administrator.',lifecycle_incomplete:'Den forrige handling blev ikke færdiggjort. Nulstil eller fjern miljøet.',retry_exhausted:'Handlingen kunne ikke bekræftes efter flere forsøg. Nulstil eller fjern miljøet.',active_lab_exists:'Du har allerede et aktivt lab.',capacity_unavailable:'Der er ikke plads til et nyt lab lige nu.',node_unavailable:'Labserveren svarer ikke. Prøv igen om lidt.',invalid_transition:'Labbet arbejder allerede. Vent på den aktuelle handling.',login_required:'Din session er udløbet. Log ind igen.',node_rejected:'Labserveren afviste handlingen. Du kan fjerne miljøet og starte på ny.'};

export function Dashboard({user,missionId,teamId,canCreate=true,openLab}:{user:UserView;missionId:string;teamId?:string;canCreate?:boolean;openLab?:(id:string)=>void}) {
  const [missions,setMissions]=useState<Mission[]>([]);
  const [labs,setLabs]=useState<LabView[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [refreshError,setRefreshError]=useState('');
  const [lastRefresh,setLastRefresh]=useState(0);
  const [clock,setClock]=useState(Date.now());
  const [busy,setBusy]=useState(false);
  const [desktopEnabled,setDesktopEnabled]=useState(false);
  const [confirmDestroy,setConfirmDestroy]=useState(false);
  const [confirmReset,setConfirmReset]=useState(false);
  const keys=useRef(new Map<string,string>());
  const pending=useRef(false);
  const observedLab=labs.find(l=>l.state!=='DESTROYED'&&(teamId?l.team_id===teamId:l.team_id===null));
  const lab=observedLab&&['READY','STOPPED'].includes(observedLab.state)&&(clock-lastRefresh>20000||!observedLab.last_observed_at)?{...observedLab,state:'ERROR' as const,error_code:'observation_stale'}:observedLab;
  const mission=missions.find(m=>m.id===(lab?.mission_id??missionId));
  const selectedMission=missions.find(m=>m.id===missionId);
  async function refresh(signal?:AbortSignal) {
    const [missionResponse,labResponse]=await Promise.all([fetch('/api/v1/missions',{signal}),fetch('/api/v1/labs',{signal})]);
    if (missionResponse.status===401 || labResponse.status===401) throw new Error(errors.login_required);
    if (!missionResponse.ok || !labResponse.ok) throw new Error('Status kunne ikke hentes. Forbindelsen forsøges igen.');
    setMissions(await missionResponse.json()); setLabs(await labResponse.json());setLastRefresh(Date.now());
  }
  useEffect(()=>{
    const controller=new AbortController();
    const update=()=>refresh(controller.signal).then(()=>setRefreshError('')).catch(e=>{if(e.name!=='AbortError')setRefreshError(e instanceof TypeError?'Forbindelsen til serveren er afbrudt. Forsøger igen.':e.message);}).finally(()=>setLoading(false));
    void update();const timer=setInterval(update,3000);const clockTimer=setInterval(()=>setClock(Date.now()),1000);
    return ()=>{controller.abort();clearInterval(timer);clearInterval(clockTimer);};
  },[]);
  async function action(kind:'create'|'start'|'stop'|'reset'|'destroy') {
    if(pending.current || !mission)return;
    pending.current=true;setBusy(true);setError('');setConfirmDestroy(false);setConfirmReset(false);
    const intent=kind+':'+(lab?.id??mission.id+':'+desktopEnabled);
    const key=keys.current.get(intent)??crypto.randomUUID();keys.current.set(intent,key);
    try {
      const response=await fetch(kind==='create'?'/api/v1/labs':`/api/v1/labs/${lab?.id}/${kind}`,{method:'POST',headers:{'Content-Type':'application/json','X-CRA-CSRF':user.csrf_token,'Idempotency-Key':key},body:kind==='create'?JSON.stringify({mission_id:mission.id,mission_version:mission.version,team_id:teamId??null,desktop_enabled:desktopEnabled}):undefined});
      if(!response.ok){const body=await response.json();throw new Error(errors[body.detail]??'Handlingen kunne ikke gennemføres. Prøv igen.');}
      keys.current.delete(intent);await refresh();
    }catch(e){setError(e instanceof Error?e.message:'Forbindelsen blev afbrudt.');}
    finally{pending.current=false;setBusy(false);}
  }
  return <section className="workspace-content">{!teamId&&openLab&&<Assignments openLab={openLab}/>}<div className="page-heading"><div><p className="eyebrow">UNDERSØG · FORSTÅ · DOKUMENTÉR</p><h1>Dit næste fund<br/>starter <em>her.</em></h1><p>Åbn en mission og arbejd dig frem til svaret.</p></div><span className="edition">{teamId?'HOLDMISSION':'SOLO'}</span></div>{!teamId&&lab&&mission&&selectedMission&&lab.mission_id!==missionId&&<MissionSwitch key={lab.id+':'+lab.generation+':'+missionId} lab={lab} current={mission} selected={selectedMission} busy={busy||!!refreshError} onRemove={()=>action('destroy')}/>}{(error||refreshError)&&<div className="notice error" role="alert">{error||refreshError}</div>}{loading?<p role="status">Henter dit arbejdsrum…</p>:mission&&<><div className="section-heading"><h2>Din mission</h2><span>DIT TILDELTE ØVELSESMILJØ</span></div><div className="mission-grid"><article className="mission-card"><div className="mission-art" aria-hidden="true"><span className="node-symbol">⌘</span><i/><span className="target-symbol">◎</span><div className="grid-lines"/></div><div className="mission-copy"><div className="mission-meta"><span>MISSION</span><span>◷ {mission.estimated_minutes} MIN</span></div><h2>{mission.title}</h2><p>{mission.summary}</p><div className="mission-tags"><span>Linux</span><span>Netværk</span><span>HTTP</span></div>{!lab&&canCreate&&<><label className="check-row"><input type="checkbox" checked={desktopEnabled} disabled={busy} onChange={e=>setDesktopEnabled(e.target.checked)}/>Grafisk labdesktop · browser og Wireshark</label><p className="muted">Grafik bruger 2 GiB RAM pr. operator. Terminalen er altid tilgængelig.</p><button disabled={busy} onClick={()=>action('create')}>{busy?'Bestiller miljø…':'Start mission →'}</button></>}{lab&&<div className="lab-status"><span className={'dot '+(lab.state==='READY'?'ready':lab.state==='ERROR'?'error':'checking')}/><strong>{labels[lab.state]??lab.state}</strong><span>Generation {lab.generation}</span></div>}</div></article><article className="briefing-card"><p className="eyebrow">DIN OPGAVE</p><h2>Hvad kan du finde?</h2><p>{mission.briefing}</p><ol className="objectives">{mission.objectives.map(objective=><li key={objective.id}><span className="objective-check">○</span><div><strong>{objective.title}</strong><p>{objective.description}</p></div><b>{objective.points} pt</b></li>)}</ol></article></div>{!lab&&!canCreate&&<p className="notice">Læreren klargør holdets miljø. Det vises her, når det er bestilt.</p>}{lab&&<section className="lab-panel" aria-label="Dit lab"><div className="section-heading"><h2>Dit lab</h2><span aria-live="polite">{labels[lab.state]}</span></div><div className="lab-topology"><div><span>DIN OPERATØR</span><strong>Linux · x86_64</strong></div><div className="network-line">ISOLERET NET</div><div><span>TILDELT MÅL</span><strong>{lab.target_ip??'Afventer klargøring'}</strong></div></div>{lab.last_observed_at&&<p className="muted">Senest kontrolleret: {new Date(lab.last_observed_at).toLocaleTimeString('da-DK')}</p>}{lab.error_code&&<p className="error">{errors[lab.error_code]??'Miljøet kræver en ny handling.'}</p>}{['QUEUED','PROVISIONING','STARTING','STOPPING','RESETTING','DESTROYING'].includes(lab.state)&&<p className="muted" role="status">Handlingen kører på labserveren. Du kan beholde siden åben eller vende tilbage.</p>}{lab.state==='READY'&&lab.can_operate&&<Suspense fallback={<p className="muted">Åbner terminalen…</p>}><Terminal labId={lab.id} generation={lab.generation} csrf={user.csrf_token}/></Suspense>}{lab.state==='READY'&&lab.can_operate&&lab.desktop_enabled&&<Suspense fallback={<p>Henter skrivebordet…</p>}><Desktop labId={lab.id} generation={lab.generation} csrf={user.csrf_token}/></Suspense>}{lab.state==='READY'&&<ScorePanel key={lab.id+':'+lab.generation} lab={lab} mission={mission} user={user}/>}{lab.can_manage&&<div className="lab-actions">{lab.state==='READY'&&<button className="secondary" disabled={busy} onClick={()=>action('stop')}>Stop miljø</button>}{lab.state==='ERROR'&&lab.error_code==='runtime_drift'&&<button disabled={busy} onClick={()=>action('start')}>Genstart miljø</button>}{lab.state==='STOPPED'&&<button disabled={busy} onClick={()=>action('start')}>Start miljø</button>}{['READY','STOPPED','ERROR'].includes(lab.state)&&<button className="secondary" disabled={busy} onClick={()=>{setConfirmReset(true);setConfirmDestroy(false);}}>Nulstil miljø</button>}{['READY','STOPPED','ERROR'].includes(lab.state)&&<button className="quiet danger" disabled={busy} onClick={()=>{setConfirmDestroy(true);setConfirmReset(false);}}>Fjern miljø</button>}</div>}{confirmReset&&<div className="notice"><p>Nulstil alle maskiner i miljøet? Alle filer i miljøet fjernes, og terminalen lukkes. Dine optjente point bevares.</p><button className="danger-button" disabled={busy} onClick={()=>action('reset')}>Nulstil alle maskiner</button> <button className="quiet" onClick={()=>setConfirmReset(false)}>Behold miljøet</button></div>}{confirmDestroy&&<div className="notice"><p>Fjern miljøet og dets filer? Du kan starte en ny mission bagefter.</p><button className="danger-button" disabled={busy} onClick={()=>action('destroy')}>Fjern miljøet</button> <button className="quiet" onClick={()=>setConfirmDestroy(false)}>Behold miljøet</button></div>}</section>}</>}<footer>{teamId?'EGEN OPERATØR · FÆLLES HOLDMÅL':'DIT EGET LAB'} · UNDERSØG KUN DET TILDELTE MÅL</footer></section>;
}
