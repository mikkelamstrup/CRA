import {useEffect,useRef,useState} from 'react';
import type {AttemptView,LabView,Mission,ProgressView,UserView} from './contracts';

export function ScorePanel({lab,mission,user}:{lab:LabView;mission:Mission;user:UserView}) {
  const [progress,setProgress]=useState<ProgressView|null>(null);
  const [flag,setFlag]=useState('');
  const [result,setResult]=useState<AttemptView|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const pending=useRef(false);
  const intent=useRef<{flag:string;key:string}|null>(null);
  const objective=mission.objectives[0];
  async function refresh(signal?:AbortSignal){
    const response=await fetch(`/api/v1/labs/${lab.id}/progress`,{signal});
    if(!response.ok)throw new Error('Resultatet kunne ikke hentes. Prøv igen.');
    setProgress(await response.json());
  }
  useEffect(()=>{const controller=new AbortController();void refresh(controller.signal).catch(error=>{if(error.name!=='AbortError')setError(error.message);});const timer=setInterval(()=>{void refresh(controller.signal).catch(error=>{if(error.name!=='AbortError')setError(error.message);});},5000);return()=>{controller.abort();clearInterval(timer);};},[lab.id,lab.generation]);
  async function submit(event:React.FormEvent){
    event.preventDefault();if(pending.current)return;pending.current=true;setBusy(true);setError('');setResult(null);
    const key=intent.current?.flag===flag?intent.current.key:crypto.randomUUID();intent.current={flag,key};
    try{
      const response=await fetch(`/api/v1/labs/${lab.id}/objectives/${objective.id}/attempts`,{method:'POST',headers:{'Content-Type':'application/json','X-CRA-CSRF':user.csrf_token,'Idempotency-Key':key},body:JSON.stringify({generation:lab.generation,flag})});
      if(!response.ok){const body=await response.json();const messages:Record<string,string>={validator_unavailable:'Valideringen er midlertidigt utilgængelig. Dit svar er ikke bedømt.',attempt_limit:'Vent et minut, før du prøver igen.',generation_changed:'Miljøet er blevet nulstillet. Find det nye flag.',lab_not_ready:'Vent, til miljøet er klar igen.'};throw new Error(messages[body.detail]??'Svaret kunne ikke afleveres. Prøv igen.');}
      const answer:AttemptView=await response.json();setResult(answer);setFlag('');intent.current=null;await refresh();
    }catch(error){setError(error instanceof Error?error.message:'Forbindelsen blev afbrudt.');}
    finally{pending.current=false;setBusy(false);}
  }
  const solved=progress?.completed_objectives.includes(objective.id);
  return <section className="score-panel" aria-label="Dit fund"><div className="score-heading"><div><p className="eyebrow">FRA UNDERSØGELSE TIL FUND</p><h2>{solved?'Fundet er valideret':objective.title}</h2></div><div className="points"><strong>{progress?.total_points??'—'}</strong><span>{lab.team_id?'HOLDPOINT':'POINT OPTJENT'}</span></div></div><p className="muted">Indsend flaget fra det sted, missionen beskriver. Point gives én gang per mission og bevares ved nulstilling.</p>{lab.can_operate&&<form onSubmit={submit}><label htmlFor="flag">Dit flag</label><div className="flag-row"><input id="flag" type="password" autoComplete="off" spellCheck={false} placeholder="CRA{…}" value={flag} onChange={e=>setFlag(e.target.value)} maxLength={256} required/><button disabled={busy||!flag}>{busy?'Validerer…':'Validér flag'}</button></div></form>}{result&&<p role="status" className={result.accepted?'answer correct':'answer incorrect'}>{result.accepted?(result.points_awarded>0?`Flaget er korrekt. Du har optjent ${result.points_awarded} point.`:'Flaget er korrekt. Dine point er allerede registreret.'):'Flaget passer ikke til dette miljø. Undersøg svaret fra servicen og prøv igen.'}</p>}{error&&<p className="error" role="alert">{error}</p>}{solved&&<p className="evidence">✓ VALIDERET FLAG · GENERATION {lab.generation}</p>}<div className="hints"><h3>Brug for et spor?</h3>{mission.hints.map((hint,index)=><details key={index}><summary>Hint {index+1}</summary><p>{hint}</p></details>)}</div></section>;
}
