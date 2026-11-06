import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { Academy } from './Academy';
import { Projector } from './Projector';

import type { UserView as User } from './contracts';
function App() {
  const [user, setUser] = useState<User|null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function loadUser() {
    const response = await fetch('/api/v1/auth/me');
    if (response.status === 401) {setUser(null); return;}
    if (!response.ok) throw new Error('Serveren svarer ikke. Prøv igen.');
    setUser(await response.json());
  }
  useEffect(() => {loadUser().catch(e => setError(e.message)).finally(() => setChecking(false));}, []);
  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch('/api/v1/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:data.get('username'),password:data.get('password')})});
      if (!response.ok) throw new Error(response.status === 429 ? 'For mange forsøg. Vent et minut.' : 'Login mislykkedes. Kontrollér brugernavn og adgangskode.');
      await loadUser();
    } catch(e) {setError(e instanceof Error ? e.message : 'Forbindelsen blev afbrudt.');}
    finally {setBusy(false);}
  }
  async function logout() {
    if (!user) return;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/v1/auth/logout',{method:'POST',headers:{'X-CRA-CSRF':user.csrf_token}});
      if (!response.ok && response.status !== 401) throw new Error('Kunne ikke logge ud. Prøv igen.');
      setUser(null);
    } catch(e) {setError(e instanceof Error ? e.message : 'Forbindelsen blev afbrudt.');}
    finally {setBusy(false);}
  }
  if (user) return <Academy user={user} onLogout={logout}/>;
  return <main><header className="brand">CRA <span>CYBER RANGE ACADEMY</span></header><div className="login-layout"><section><p className="eyebrow">DIT DIGITALE TRÆNINGSMILJØ</p><h1>Lær ved at<br/><em>undersøge.</em></h1><p className="intro">Rigtige Linux-miljøer. Afgrænsede missioner. Plads til at prøve igen.</p><div className="principles"><span>01 / Undersøg</span><span>02 / Forstå</span><span>03 / Dokumentér</span></div></section><section className="login-card" aria-label="Adgang"><p className="eyebrow">LOKALT AKADEMI</p>{checking ? <p role="status">Kontrollerer session…</p> : <><h2>Log ind</h2><p>Fortsæt til dit øvelsesmiljø.</p><form onSubmit={login}><label htmlFor="username">Brugernavn</label><input id="username" name="username" autoComplete="username" required maxLength={48}/><label htmlFor="password">Adgangskode</label><input id="password" name="password" type="password" autoComplete="current-password" required maxLength={256}/><button disabled={busy}>{busy ? 'Logger ind…' : 'Fortsæt →'}</button></form></>}{error && <p className="error" role="alert">{error}</p>}</section></div><footer>CYBER RANGE ACADEMY · AUTORISEREDE ØVELSESMILJØER</footer></main>;
}
createRoot(document.getElementById('root')!).render(<StrictMode>{new URLSearchParams(location.search).get('view')==='projector'?<Projector/>:<App/>}</StrictMode>);
