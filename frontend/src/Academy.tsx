import {useEffect,useState} from 'react';
import type {UserView} from './contracts';
import {Competition,CompetitionBoard} from './Competition';
import {Teams} from './Teams';
import {CommandCenter} from './CommandCenter';
import {Course} from './Course';
import {Dashboard} from './Dashboard';
import {Help,SessionBanner,Teaching} from './Teaching';
import {IncidentDispatch} from './IncidentDispatch';

export function mayNavigate(){return !document.querySelector('[data-unsaved="true"]')||window.confirm('Du har noter, der ikke er gemt. Vil du forlade dem?');}
export function Academy({user,onLogout}:{user:UserView;onLogout:()=>void}){
 const [boardId,setBoardId]=useState('');
 const [page,setPage]=useState<'course'|'lab'|'help'|'teaching'|'teams'|'center'|'competition'>('course'),[mission,setMission]=useState('first-files');
 useEffect(()=>{document.title='Cyber Range Academy · Dit forløb';const warn=(e:BeforeUnloadEvent)=>{if(document.querySelector('[data-unsaved="true"]')){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[]);
 function navigate(next:'course'|'lab'|'help'|'teaching'|'teams'|'center'|'competition',id?:string){if(!mayNavigate())return;if(id)setMission(id);setPage(next);}
 const dispatch=<IncidentDispatch user={user} openCase={()=>{if(!mayNavigate())return false;setBoardId('');setMission('cybercon-incident');setPage('lab');return true;}}/>;
 if(boardId)return <>{dispatch}<CompetitionBoard id={boardId} onClose={()=>setBoardId('')}/></>;
 return <div className="workspace">{dispatch}<a className="skip-link" href="#workspace-main">Spring til indhold</a><header className="workspace-header"><a className="brand" href="#" onClick={e=>{e.preventDefault();navigate('course');}}>CRA <span>CYBER RANGE ACADEMY</span></a><div className="account"><span className="avatar">{user.username.slice(0,1).toUpperCase()}</span><span>{user.username}</span><button className="quiet" onClick={()=>{if(mayNavigate())onLogout();}}>Log ud</button></div></header><div className="workspace-body"><aside><div className="nav-label">ARBEJDSRUM</div><nav aria-label="Hovednavigation"><button className={page==='course'?'nav-active':'quiet'} aria-current={page==='course'?'page':undefined} onClick={()=>navigate('course')}>▦ Mit forløb</button><button className={page==='lab'?'nav-active':'quiet'} aria-current={page==='lab'?'page':undefined} onClick={()=>navigate('lab')}>⌘ Mit lab</button><button className={page==='teams'?'nav-active':'quiet'} aria-current={page==='teams'?'page':undefined} onClick={()=>navigate('teams')}>◇ Mit hold</button><button className={page==='competition'?'nav-active':'quiet'} aria-current={page==='competition'?'page':undefined} onClick={()=>navigate('competition')}>⚑ Konkurrence</button><button className={page==='help'?'nav-active':'quiet'} onClick={()=>navigate('help')}>? Bed om hjælp</button>{user.role==='teacher'&&<button className={page==='center'?'nav-active':'quiet'} aria-current={page==='center'?'page':undefined} onClick={()=>navigate('center')}>◎ Command Center</button>}{user.role==='teacher'&&<button className={page==='teaching'?'nav-active':'quiet'} onClick={()=>navigate('teaching')}>◫ Lærerrum</button>}</nav><div className="aside-note"><span className="dot ready"/><strong>Lokalt akademi</strong><p>Rigtige værktøjer. Afgrænset scope.</p></div></aside><main id="workspace-main" className="academy-main" tabIndex={-1}><SessionBanner/>{page==='center'&&user.role==='teacher'?<CommandCenter user={user} onTeaching={()=>navigate('teaching')}/>:page==='competition'?<Competition user={user} onProject={id=>setBoardId(id)}/>:page==='teams'?<Teams user={user}/>:page==='teaching'&&user.role==='teacher'?<Teaching user={user}/>:page==='help'?<Help user={user}/>:page==='course'?<Course user={user} openLab={id=>navigate('lab',id)}/>:<Dashboard user={user} missionId={mission} openLab={id=>navigate('lab',id)}/>}</main></div></div>;
}
