import {useEffect,useRef,useState} from 'react';
import type {TrafficView,CenterLab,HeaderFlow} from './contracts';

const protocol=(value:number)=>({6:'TCP',17:'UDP',1:'ICMP',58:'ICMPv6'}[value]??`IP ${value}`);
const endpoint=(address:string,port:number|null)=>port===null?address:`${address.includes(':')?'['+address+']':address}:${port}`;
export function Traffic({lab}:{lab:CenterLab}){
 const panel=useRef<HTMLElement>(null),[narrow,setNarrow]=useState(false);
 useEffect(()=>{if(!panel.current)return;const observer=new ResizeObserver(entries=>setNarrow(entries[0].contentRect.width<560));observer.observe(panel.current);return()=>observer.disconnect();},[]);
 const [data,setData]=useState<TrafficView|null>(null),[error,setError]=useState(''),[selected,setSelected]=useState(''),[clock,setClock]=useState(Date.now()),[last,setLast]=useState<number|null>(null);
 useEffect(()=>{let live=true,pending=false;const abort=new AbortController();setData(null);setError('');setSelected('');setLast(null);
  async function poll(){if(pending||!live)return;pending=true;try{const r=await fetch(`/api/v1/command-center/labs/${lab.id}/traffic`,{signal:abort.signal});if(!r.ok)throw new Error('Trafikvisningen kan ikke opdateres.');const value=await r.json() as TrafficView;if(live){setData(value);setError('');setLast(performance.now());}}catch(e){if(live)setError((e as Error).message);}finally{pending=false;}}
  void poll();const timer=setInterval(()=>{setClock(Date.now());void poll();},2000);return()=>{live=false;abort.abort();clearInterval(timer);};
 },[lab.id,lab.generation]);
 const captures=data?.captures??[],unconfirmed=last===null||performance.now()-last>20000||!!error||!data||data.stale||lab.stale||lab.state!=='READY'||!data.received_at||clock-new Date(data.received_at).getTime()>20000||data.generation!==lab.generation;
 const recent=(data?.events??[]).filter(event=>clock-new Date(event.observed_at).getTime()<10000);
 const actorName=(id:string)=>{const index=captures.findIndex(c=>c.operator_user_id===id);return index<0?'Operatør':`Operatør ${index+1}`;};
 const rows=(data?.events??[]).filter(event=>!selected||event.operator_user_id===selected).flatMap(event=>event.window.flows.map((flow,index)=>({flow,event,id:event.event_id+':'+index}))).reverse().slice(0,40);
 const lost=captures.reduce((sum,c)=>sum+c.metadata_dropped,0),missed=captures.reduce((sum,c)=>sum+(c.poll_missed_windows??0),0),partial=captures.reduce((sum,c)=>sum+c.partial_windows,0);
 const limited=(data?.events??[]).reduce((sum,e)=>sum+e.window.omitted_packets,0);
 const connected=!unconfirmed&&captures.length===lab.operator_count&&captures.every(c=>c.connected);
 function activity(id:string){return recent.filter(e=>e.operator_user_id===id).reduce((sum,e)=>sum+e.window.flows.reduce((n,f)=>n+f.packets,0),0);}
 const operators=Array.from({length:lab.operator_count},(_,i)=>captures[i]??null);
 const height=Math.max(190,operators.length*90+40),targetY=height/2;
 const geometry=narrow?{width:360,left:8,opWidth:120,opCenter:68,link:128,target:240,targetWidth:112,targetCenter:296,count:180}:{width:620,left:24,opWidth:158,opCenter:103,link:182,target:433,targetWidth:163,targetCenter:514,count:260};
 return <section ref={panel} className="traffic-panel" aria-label="Observeret labtrafik"><div className="section-heading"><h2>Netværk i labbet</h2><span>OBSERVERET · INGEN PAYLOAD</span></div>
 <p className="muted">Hver operatør har sit eget link til målet. En forbindelse til SSH viser trafik; den beviser ikke et vellykket login.</p>
 <div className={'traffic-health '+(connected?'connected':'unconfirmed')} role="status"><span className="dot"/>{connected?'Netværkslinks bekræftet':unconfirmed?'Netværksstatus ubekræftet':'Et eller flere links er ikke bekræftet'}{data?.received_at&&<small> · modtaget {new Date(data.received_at).toLocaleTimeString('da-DK')}</small>}</div>
 {error&&<p className="error" role="alert">{error}</p>}
 <svg className="traffic-map" viewBox={`0 0 ${geometry.width} ${height}`} role="img" aria-label={`${lab.operator_count} separate operatørlinks til et fælles mål. ${connected?'Links bekræftet.':'Status ubekræftet.'}`}>
  {operators.map((capture,index)=>{const y=operators.length===1?targetY:55+index*90,count=capture?activity(capture.operator_user_id):0;return <g key={index}><path className={count&&!unconfirmed?'flow-active':'flow-idle'} d={`M ${geometry.link} ${y} C ${geometry.count} ${y}, ${geometry.count} ${targetY}, ${geometry.target} ${targetY}`}/><rect x={geometry.left} y={y-28} width={geometry.opWidth} height="56" rx="12"/><text x={geometry.opCenter} y={y-3} textAnchor="middle">Operatør {index+1}</text><text className="map-detail" x={geometry.opCenter} y={y+15} textAnchor="middle">{capture?.connected&&!unconfirmed?'Link bekræftet':'Afventer'}</text>{count>0&&!unconfirmed&&<text className="map-count" x={geometry.count} y={y-10} textAnchor="middle">{count.toLocaleString('da-DK')} pakker</text>}</g>;})}
  <rect className="target-node" x={geometry.target} y={targetY-35} width={geometry.targetWidth} height="70" rx="14"/><text x={geometry.targetCenter} y={targetY-4} textAnchor="middle">{lab.operator_count>1?'Fælles mål':'Målmaskine'}</text><text className="map-detail" x={geometry.targetCenter} y={targetY+17} textAnchor="middle">Generation {lab.generation}</text>
 </svg>
 <p className="muted map-caption">Tal viser de seneste 10 sekunders modtagne observationer. Netkortet viser labbets tildelte links; det afslører ikke, hvad eleverne har forstået.</p>
 {(lost>0||missed>0||partial>0||limited>0)&&<p className="notice traffic-limits">Visningen er ufuldstændig: {lost.toLocaleString('da-DK')} sensorbeskeder udeladt, {missed.toLocaleString('da-DK')} tidsvinduer ikke modtaget og {partial.toLocaleString('da-DK')} delvise vinduer. Detaljegrænsen udelader {limited.toLocaleString('da-DK')} pakker fra de viste flowgrupper. Dette er observationstab, ikke et mål for tab i elevens forbindelse.</p>}
 <div className="traffic-filters" role="group" aria-label="Filtrer observeret trafik"><button className="quiet" aria-pressed={!selected} onClick={()=>setSelected('')}>Alle links</button>{captures.map(c=><button className="quiet" key={c.operator_user_id} aria-pressed={selected===c.operator_user_id} onClick={()=>setSelected(c.operator_user_id)}>{actorName(c.operator_user_id)}</button>)}</div>
 {!rows.length?<p className="traffic-empty">Ingen IP-flowobservationer i den indlæste historik. Prøv missionens netværkskommandoer i dit lab.</p>:<ol className="traffic-list">{rows.map(({flow,event,id})=><li key={id}><div><strong>{actorName(event.operator_user_id)} · {protocol(flow.protocol)}</strong><small>{new Date(event.observed_at).toLocaleTimeString('da-DK')} · {flow.packets.toLocaleString('da-DK')} pakker · {flow.bytes.toLocaleString('da-DK')} byte</small></div><FlowEndpoints flow={flow}/><span className="traffic-evidence">Observeret på {flow.direction==='operator_to_target'?'operatørens':'målets'} side · gen {event.generation}{event.window.partial?' · delvist vindue':''}</span></li>)}</ol>}
 <p className="muted">Op til 40 flowgrupper vises. Detaljehistorik bevares højst 24 timer og 20.000 vinduer pr. lab på tværs af nulstillinger; det samlede lager har også en grænse. Point og opgavehændelser har deres egen historik.</p>
 </section>;
}
function FlowEndpoints({flow}:{flow:HeaderFlow}){return <p className="flow-endpoints"><code>{endpoint(flow.src,flow.source_port)}</code><span aria-label="til"> → </span><code>{endpoint(flow.dst,flow.destination_port)}</code></p>;}
