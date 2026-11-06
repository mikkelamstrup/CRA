import {useEffect,useState} from 'react';
import {NordlysWebsite} from './NordlysWebsite';
import type {AssignmentView} from './contracts';

export function Assignments({openLab}:{openLab:(id:string)=>void}) {
 const [items,setItems]=useState<AssignmentView[]>([]),[error,setError]=useState('');
 useEffect(()=>{
  let live=true,pending=false;const abort=new AbortController();
  async function update(){if(pending)return;pending=true;try{const r=await fetch('/api/v1/assignments',{signal:abort.signal});if(!r.ok)throw Error('Opgaverne kunne ikke opdateres. Prøv at logge ind igen, hvis forbindelsen er udløbet.');const data=await r.json();if(live){setItems(data);setError('');}}catch(e){if(live)setError((e as Error).message);}finally{pending=false;}}
  void update();const timer=setInterval(()=>void update(),5000);return()=>{live=false;abort.abort();clearInterval(timer);};
 },[]);
 return <section className="assignment-list learning-card" aria-label="Dine opgaver"><div className="section-heading"><h2>Dine opgaver</h2><span>Opdateres automatisk</span></div><p>Læreren kan frigive din næste opgave, når du er klar. Dine gemte noter og point følger dig.</p>{error&&<p role="alert" className="error">{error}</p>}<div className="assignment-grid">{items.map((item,index)=><article key={item.mission_id+':'+item.mission_version} className="assignment-card"><p className="eyebrow">Opgave {index+1} · {item.estimated_minutes} min</p><h3>{item.title}</h3><p>{item.summary}</p><strong>{item.completed?'Gennemført · '+item.earned_points+' point':item.available?'Frigivet til dig':'Afventer lærerens frigivelse'}</strong><button className="secondary" disabled={!item.available||!!error} onClick={()=>openLab(item.mission_id)}>{item.completed?'Åbn opgaven igen':'Åbn opgave '+(index+1)}</button></article>)}</div><NordlysWebsite/></section>;
}
