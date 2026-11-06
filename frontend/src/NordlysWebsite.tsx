import {useEffect,useState} from 'react';

export function NordlysWebsite(){
 const [url,setUrl]=useState(''),[failed,setFailed]=useState(false);
 useEffect(()=>{
  let live=true,pending=false;const abort=new AbortController();
  async function update(){
   if(pending)return;pending=true;
   try{
    const r=await fetch('/nordlys.json',{cache:'no-store',signal:abort.signal});
    if(!r.ok)throw Error('Link unavailable');
    const data=await r.json();const parsed=new URL(data.url);
    if(parsed.protocol!=='https:'||!/^[-a-z0-9]+\.trycloudflare\.com$/.test(parsed.hostname)||parsed.username||parsed.password||parsed.port||parsed.pathname!=='/'||parsed.search||parsed.hash)throw Error('Invalid website address');
    if(live){setUrl(parsed.href);setFailed(false);}
   }catch{if(live){setUrl('');setFailed(true);}}
   finally{pending=false;}
  }
  void update();const timer=setInterval(update,30000);
  return()=>{live=false;abort.abort();clearInterval(timer);};
 },[]);
 return <div role="note" className="nordlys-website" aria-label="Nordlys-hjemmesiden"><div><strong>Opgave 3 · Besøg Nordlys</strong><p>Se organisationens hjemmeside i en ny fane. Brug din egen mål-IP i CRA til curl — dér findes dit personlige flag.</p></div>{url?<a href={url} target="_blank" rel="noopener noreferrer">Åbn Nordlys-hjemmesiden ↗</a>:<span className="muted">{failed?'Linket kunne ikke hentes. Prøv igen om lidt.':'Henter Nordlys-link…'}</span>}</div>;
}
