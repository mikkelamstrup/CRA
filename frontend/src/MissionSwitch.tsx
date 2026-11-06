import {useEffect,useRef,useState} from 'react';
import type {LabView, Mission} from './contracts';

// The parent keys this confirmation by source lab/generation and destination.
export function MissionSwitch({lab,current,selected,busy,onRemove}:{lab:LabView;current:Mission;selected:Mission;busy:boolean;onRemove:()=>void}) {
  const [confirm,setConfirm]=useState(false);
  const panel=useRef<HTMLElement>(null);
  useEffect(()=>{panel.current?.focus();panel.current?.scrollIntoView({block:'start'});},[]);
  const canRemove=lab.can_manage&&['READY','STOPPED','ERROR'].includes(lab.state);
  return <section ref={panel} tabIndex={-1} className="notice" aria-label="Skift opgave">
    <h2>Åbn {selected.title}</h2>
    <p>Dit nuværende miljø hører til {current.title}. Du kan have ét solomiljø ad gangen.</p>
    {lab.state==='DESTROYING'?<p role="status">Det gamle miljø fjernes. Når det er færdigt, kan du vælge Start mission for at åbne {selected.title}.</p>:<>
      <p>Gem først dine fund i notesbogen. Fjern derefter det gamle miljø her, og vælg Start mission, når knappen vises.</p>
      {canRemove&&!confirm&&<button disabled={busy} onClick={()=>setConfirm(true)}>Skift til {selected.title}</button>}
      {canRemove&&confirm&&<div>
        <p><strong>Fjern miljøet til {current.title}?</strong> Alle filer i det gamle miljø slettes. Dine gemte noter og optjente point bevares. Derefter kan du starte {selected.title}.</p>
        <button className="danger-button" disabled={busy} onClick={()=>{setConfirm(false);onRemove();}}>Fjern gammelt miljø</button>{' '}
        <button className="quiet" disabled={busy} onClick={()=>setConfirm(false)}>Behold miljøet</button>
      </div>}
      {!canRemove&&<p role="status">{lab.can_manage?'Vent på, at miljøets igangværende handling er færdig.':'Bed læreren om hjælp til at skifte miljø.'}</p>}
    </>}
  </section>;
}
