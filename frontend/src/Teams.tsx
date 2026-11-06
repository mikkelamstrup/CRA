import {useEffect,useRef,useState} from 'react';
import type {Mission,TeamView,UserView} from './contracts';
import {Dashboard} from './Dashboard';
import {SoloPause,SharedNotebook,TeamFindings,TeamRoles} from './TeamWork';
import {mayNavigate} from './Academy';

type MemberRole='coordinator'|'analyst'|'operator'|'reporter';
const roles:Record<MemberRole,string>={coordinator:'Koordinator',analyst:'Analytiker',operator:'Operatør',reporter:'Dokumentation'};
type Roster={user_id:string;username:string;role:string};
export function Teams({user}:{user:UserView}){
 const [teams,setTeams]=useState<TeamView[]>([]),[roster,setRoster]=useState<Roster[]>([]),[missions,setMissions]=useState<Mission[]>([]);
 const [selected,setSelected]=useState(''),[mission,setMission]=useState('first-contact'),[name,setName]=useState(''),[members,setMembers]=useState<Record<string,MemberRole>>({});
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
 const intent=useRef<{body:string;key:string}|null>(null),pending=useRef(false);
 const teacher=user.role==='teacher',team=teams.find(t=>t.id===selected);
 async function refresh(signal?:AbortSignal){
  const paths=['/api/v1/teams','/api/v1/missions',...(teacher?['/api/v1/teams/roster']:[])];
  const responses=await Promise.all(paths.map(path=>fetch(path,{signal})));
  if(responses.some(r=>!r.ok))throw new Error('Holdoversigten kunne ikke hentes.');
  const data=await Promise.all(responses.map(r=>r.json()));setTeams(data[0]);setMissions(data[1].filter((item:Mission)=>!['cybercon-incident','request-forge'].includes(item.id)));if(teacher)setRoster(data[2]);
  setSelected(current=>current||data[0][0]?.id||'');
 }
 useEffect(()=>{const controller=new AbortController();void refresh(controller.signal).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>setLoading(false));return()=>controller.abort();},[]);
 async function create(e:React.FormEvent){
  e.preventDefault();if(pending.current)return;pending.current=true;setBusy(true);setError('');
  const body=JSON.stringify({name,members:Object.entries(members).map(([user_id,role])=>({user_id,role}))});
  if(intent.current?.body!==body)intent.current={body,key:crypto.randomUUID()};
  try{
   const response=await fetch('/api/v1/teams',{method:'POST',headers:{'Content-Type':'application/json','X-CRA-CSRF':user.csrf_token},body:JSON.stringify({...JSON.parse(body),request_key:intent.current.key})});
   if(!response.ok)throw new Error('Holdet kunne ikke oprettes. Kontrollér medlemsvalget og prøv igen.');
   const created:TeamView=await response.json();setName('');setMembers({});intent.current=null;await refresh();setSelected(created.id);
  }catch(e){setError(e instanceof Error?e.message:'Forbindelsen blev afbrudt.');}finally{pending.current=false;setBusy(false);}
 }
 return <section className="team-workspace"><div className="page-heading"><div><p className="eyebrow">FÆLLES MÅL · EGNE OPERATØRER</p><h1>Stærkere som hold.</h1><p>Del fund, fordel roller, og arbejd mod samme målmaskine.</p></div></div>{error&&<p role="alert" className="notice error">{error}</p>}{loading?<p role="status">Henter hold…</p>:<><SoloPause user={user}/>{teams.length>0?<><label>Vælg hold<select aria-label="Vælg hold" value={selected} onChange={e=>{if(mayNavigate())setSelected(e.target.value);}}>{teams.map(t=><option value={t.id} key={t.id}>{t.name}</option>)}</select></label>{team&&<><div className="team-members">{team.members.map(m=><article key={m.user_id}><strong>{m.username}{m.user_id===user.user_id?' · dig':''}</strong><span>{roles[m.role]}</span><small>{m.active?'Egen operator-VM':'Adgang deaktiveret'}</small></article>)}</div><p className="muted">Hver deltager har sin egen terminal og egne filer. Målmaskinen og holdpoint er fælles. Læreren styrer start, stop og nulstilling.</p>{team.created_by===user.user_id&&<label>Holdets mission<select aria-label="Holdets mission" value={mission} onChange={e=>setMission(e.target.value)}>{missions.map(m=><option value={m.id} key={m.id}>{m.title}</option>)}</select></label>}<details className="team-collaboration"><summary>Noter og fælles fund</summary><div className="team-work-grid"><SharedNotebook key={team.id+':notes'} teamId={team.id} user={user}/><TeamFindings key={team.id+':findings'} teamId={team.id} user={user}/></div></details>{teacher&&team.created_by===user.user_id&&<TeamRoles key={team.id} team={team} user={user} onChange={changed=>setTeams(previous=>previous.map(t=>t.id===changed.id?changed:t))}/>}<Dashboard key={team.id} user={user} teamId={team.id} missionId={mission} canCreate={teacher&&team.created_by===user.user_id}/></>}</>:<p className="notice">Du har endnu ikke et hold. Læreren kan tildele op til fire deltagere.</p>}{teacher&&<details className="team-create"><summary>Opret et hold</summary><form onSubmit={create} data-unsaved={Boolean(name||Object.keys(members).length)}><fieldset disabled={busy}><legend>Navn og roller</legend><label>Holdnavn<input value={name} onChange={e=>setName(e.target.value)} maxLength={80} required/></label><p>Vælg 1–4 deltagere. Du kan vælge dig selv for at afprøve holdmissionen.</p>{roster.map(person=><div className="member-choice" key={person.user_id}><label><input type="checkbox" checked={person.user_id in members} disabled={!(person.user_id in members)&&Object.keys(members).length>=4} onChange={e=>setMembers(previous=>{const next={...previous};if(e.target.checked)next[person.user_id]='operator';else delete next[person.user_id];return next;})}/>{person.username}</label>{person.user_id in members&&<select aria-label={'Rolle for '+person.username} value={members[person.user_id]} onChange={e=>setMembers(previous=>({...previous,[person.user_id]:e.target.value as MemberRole}))}>{Object.entries(roles).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>}</div>)}<button disabled={!name||Object.keys(members).length===0}>{busy?'Opretter…':'Opret hold'}</button></fieldset></form></details>}</>}</section>;
}
