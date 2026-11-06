# Fra opgave 8 til 13 · Lærervejledning og facit

**Målgruppe:** elever, der kan bruge terminal, filer, DNS, HTTP og den udleverede
SSH-konfiguration fra Operation Shadow. De skal nu lære at håndtere en hændelse,
før de får ansvaret for hele Cybercon-sagen.

De fire opgaver er selvstændige, fungerende CRA-missioner med hver sit isolerede
miljø. Alle virksomheder, konti, logs og hændelser er fiktive. HTTP-svar og logs
kommer fra virkelige processer i VM'en. Eleverne arbejder individuelt.

| Opgave | Titel | Ny færdighed | Tid | Point |
|---|---|---|---|---|
| 8 | Operation Shadow | Samle filfund, DNS, HTTP og SSH | Eksisterende | 300 |
| 9 | HTTP · Hvem må læse rapporten? | Request, cookie, identitet og adgang | 40 min | 150 |
| 10 | Spor · Fra loglinje til tidslinje | Bevisbevaring og afgrænset konklusion | 45 min | 200 |
| 11 | Inddæmning · Stop lækket, genåbn driften | Begrænsning, tilbagekaldelse og kundepåvirkning | 50 min | 250 |
| 12 | Ejerskab · Ret fejlen og bevis normal drift | Autorisation og positive/negative tests | 55 min | 300 |
| 13 | Cybercon · Alarm hos Nordtele | Selvstændig prioritering af hele hændelsen | Eksisterende | 500 |
| 14 | Request Forge | Efterfølgende offensiv HTTP-/SQL-undersøgelse | Eksisterende | 700 |

Den tidligere opgave 9 bliver 13, og den tidligere opgave 10 bliver 14. Deres
missions-ID'er bevares, så tidligere frigivelser, point og gemte miljøer fortsat
hører til samme mission. Undervisningsdagene 1–8 er uændrede; opgavenumre 9–14 er
ikke nye undervisningsdage. Der er ingen automatisk VM- eller opgaveovergang.

## Tilrettelæggelse og fælles arbejdsgang

Frigiv de nye missioner individuelt i **Command Center → Elever og opgaver**.
Hver mission er låst, indtil læreren frigiver den, også uden en klassesession.
Frigivelse opretter eller sletter ingen VM'er. Eleven skal eksportere rapport og
beviser før almindeligt opgaveskift, fordi fjernelse/reset sletter VM-filer.
CRA-noter og optjente point bevares gennem den sædvanlige arbejdsgang.

Sæt gerne en kort mundtlig kontrol mellem trinnene. Hjælp først med metode og
kilder; udlever de konkrete facitværdier nedenfor, når eleven kan forklare dem.
De cirka 190 minutter er arbejdstid til træningsopgaverne; afsæt også tid til
fælles gennemgang og feedback. Grafisk desktop er valgfrit. Alle løsninger
fungerer med terminal, curl, Python og den allerede installerede SSH-adgang.

I alle fire opgaver ligger materialet på **operatoren**:

```bash
cd /home/cra/academy/incident-bridge
cat README.txt
cat mandat.txt
mkdir -p ~/beviser
export BASE=http://MÅL-IP:8081
export CFG=/home/cra/academy/incident-bridge/ssh_config
```

Erstat `MÅL-IP` med IP'en vist i elevens aktuelle CRA-lab. `$BASE` er en almindelig
shellvariabel og skal sættes i hver ny terminal. Koderne `nordtele-lab-a` og
`nordtele-lab-b` er offentlige **øvelseskoder**, ikke elevernes CRA-login.

Kontrolfilen er en lille maskinlæsbar aflevering. Den erstatter ikke elevens
faglige rapport. Når den er udfyldt på operatoren, afleveres den således:

```bash
cat kontrol.json | ssh -F "$CFG" nordtele 'cat > /srv/incident-bridge/kontrol.json'
ssh -F "$CFG" nordtele bridge-check
```

`bridge-check` udleverer elevens personlige flag ved bestået kontrol. Flaget
afleveres i CRA. Eleven har ingen generel administratoradgang. Scripts og
oprindelige beviser er skrivebeskyttede; kontrolfilens sti er fast. Opgave 9–10
har også skrivebeskyttet politik. Alle praktiske HTTP-tests skal foretages fra
operatoren mod mål-IP'en: localhost-tests fra kontrollen tæller ikke som elevens
egen praktiske handling.

## Opgave 9 · HTTP: Hvem må læse rapporten?

**Læringsværdi.** Eleven adskiller transport, login og rettigheder. En cookie
repræsenterer en session; et rapportnummer er en objektidentifikator, ikke en
adgangstilladelse. Eleven lærer at sammenholde status, body og identitet i stedet
for at tolke enhver 200 som succes eller enhver 403 som samme fejl.

**Starttilstand.** Portalen håndhæver allerede ejerskab korrekt. Eleven skal måle
og forstå sikker adgang. Der skal ikke repareres noget i denne opgave.

**Facit, trin for trin på operatoren:**

```bash
curl -sS -D ~/beviser/anon.headers -o ~/beviser/anon.json \
  -w '%{http_code}\n' "$BASE/api/reports?id=8201"

curl -sS -c a.cookies \
  -d 'username=kunde-a&password=nordtele-lab-a' "$BASE/login"
curl -sS -b a.cookies "$BASE/api/me"

curl -sS -b a.cookies -D ~/beviser/egen.headers -o ~/beviser/egen.json \
  -w '%{http_code}\n' "$BASE/api/reports?id=8201"
curl -sS -b a.cookies -D ~/beviser/fremmed.headers -o ~/beviser/fremmed.json \
  -w '%{http_code}\n' "$BASE/api/reports?id=8202"
```

Forventede svar: anonym rapportadgang **401**; login **200** med `Set-Cookie`;
`/api/me` viser `kunde-a`, kunde `A` og tilladt rapport `8201`; egen rapport
**200** med det rigtige rapport-ID i body; den anden kundes rapport **403**.

Afprøv også en forkert adgangskode på `/login`. Den aktuelle øvelsesportal svarer
**403** på det endpoint. Det er en loginfejl, mens 403 ved den fremmede rapport
her er en ejerskabsafvisning. Lær eleven at bruge endpoint, request og body som
kontekst. Det er ikke en universel regel, at alle 403-svar betyder gyldigt login.

Kontrolfil:

```json
{
  "anonymous_status": 401,
  "own_status": 200,
  "foreign_status": 403,
  "identity": "kunde-a",
  "own_report_id": "8201"
}
```

Kontrollen sammenholder svarene med portalens faktiske adfærd og elevens faktiske
anonyme, egne og fremmede rapportforsøg. En udfyldt facitfil alene giver ikke flag.

**Spørg til forståelse:** Hvilken request oprettede sessionen? Hvilken fil gemte
den? Hvad ændrede sig mellem egen og fremmed rapport? Hvorfor er det ikke nok, at
eleven kan se et rapportnummer i et link? Eleven bør kunne vise sine gemte
headers og body og forklare alle tre adgangsforsøg.

**Bro til 13:** Eleven kan nu efterprøve, om normal kundeadgang virker, og om en
adgangsafvisning skyldes login, ejerskab eller et andet problem.

## Opgave 10 · Spor: Fra loglinje til tidslinje

**Læringsværdi.** Eleven arbejder kildebaseret: bevarer originale spor, læser JSONL,
finder en sammenhæng og efterprøver den. Eleven afgrænser konklusioner om
udleverede svar, omfang og attribution.

**Starttilstand.** Supporteksporten og ejerskabsfejlen er åbne. En tidlig
supportforespørgsel og rapportlæsning er gennemført mod den faktiske portal og
kopieret til et skrivebeskyttet bevis. Politikken er læsbar, men ikke redigerbar.

**Facit, trin for trin:**

```bash
ssh -F "$CFG" nordtele 'cat /srv/cybercon/evidence/initial-access.jsonl' \
  > ~/beviser/initial-access.jsonl
sha256sum ~/beviser/initial-access.jsonl > ~/beviser/initial-access.sha256
cat ~/beviser/initial-access.jsonl

curl -sS "$BASE/support/diagnostics.json" > ~/beviser/support.json
python3 -m json.tool ~/beviser/support.json
curl -sS -H 'Cookie: nt_session=nt-support-legacy' \
  -D ~/beviser/laek.headers -o ~/beviser/laek.json \
  -w '%{http_code}\n' "$BASE/api/reports?id=8202"
```

Supportsvaret indeholder øvelsessessionen `nt-support-legacy`, som repræsenterer
`kunde-a`. Den efterfølgende request udleverer alligevel kunde B's rapport 8202
med **200**. Eleven skal knytte eksponeret session og manglende ejerskabskontrol
sammen. Portalens log markerer denne session som `legacy-support`; den logger
ikke almindelige kunders fulde sessionscookies.

De konkrete tidsstempler varierer mellem labs. Generér eventuelt facitfilen fra
det indsamlede bevis, efter eleven selv har udpeget de to relevante linjer:

```bash
python3 - <<'PY'
import json
from pathlib import Path
rows = [json.loads(line) for line in (Path.home()/'beviser/initial-access.jsonl').read_text().splitlines()]
support = next(r for r in rows if r['path'] == '/support/diagnostics.json' and r['status'] == 200)
report = next(r for r in rows if r['path'] == '/api/reports?id=8202' and r['status'] == 200 and r['session'] == 'legacy-support')
answer = {
    'log_source': 'initial-access.jsonl',
    'support_time': support['time'], 'report_time': report['time'],
    'report_id': '8202', 'session_label': 'legacy-support',
    'conclusion': 'confirmed_report_response', 'attribution': 'unknown'
}
Path('kontrol.json').write_text(json.dumps(answer, indent=2))
PY
```

Tidslinjen skal vise kilde, præcis UTC-tid, sti, identitet, status, svarbytes og en
afgrænset konklusion. Hashen hjælper med at efterprøve, at den bevarede kopi ikke
er ændret; den er ikke i sig selv bevis for kildens troværdighed.

**Spørg til forståelse:** Hvad viser 200 og svarbytes her? Viser det, at samtlige
kunder er ramt? Hvem sidder bag kildeadressen? Det korrekte svar er, at labbet
beviser bestemte udleverede HTTP-svar og en reproducerbar adgangsvej. Det beviser
ikke senere brug af oplysningerne eller identiteten på en person.

**Bro til 13:** Eleven kan bygge en kildebaseret tidslinje og formulere, hvad
Cybercon ved, og hvad der stadig er usikkert, før en driftsbeslutning træffes.

## Opgave 11 · Inddæmning: Stop lækket, genåbn driften

**Læringsværdi.** Eleven skelner midlertidig begrænsning fra permanent reparation,
vurderer konsekvenser for legitime kunder og lærer, at fjernelse af en lækket
kilde ikke tilbagekalder allerede kopierede sessionsoplysninger.

**Starttilstand.** Supporteksporten og den lækkede session virker. Ejerskabsfejlen
er også til stede. Politikken er redigerbar via den snævre analyst-rolle.

**Facit, trin for trin på operatoren:** Gem oprindelig politik og aktuelle beviser.

```bash
ssh -F "$CFG" nordtele 'cat /etc/cybercon/policy.json' > ~/beviser/policy-foer.json
curl -sS -H 'Cookie: nt_session=nt-support-legacy' \
  -o ~/beviser/laek-foer.json -w '%{http_code}\n' "$BASE/api/reports?id=8202"

ssh -F "$CFG" nordtele python3 - <<'PY'
import json
p = '/etc/cybercon/policy.json'
v = json.load(open(p))
v['emergency_lockdown'] = True
open(p, 'w').write(json.dumps(v, indent=2))
PY

curl -sS -o ~/beviser/lockdown.json -w '%{http_code}\n' "$BASE/api/reports?id=8201"
```

Det sidste svar skal være **503**. Her kommer det fra bevidst lockdown; et 503
kan også skyldes ugyldig JSON, så eleven skal kontrollere politikken og body.
Legitime kunder er også blokeret. Gem målingen, mens lockdown faktisk er aktiv.

Luk derefter supporteksporten, tilbagekald sessionen og genåbn driften:

```bash
ssh -F "$CFG" nordtele python3 - <<'PY'
import json
p = '/etc/cybercon/policy.json'
v = json.load(open(p))
v['expose_support_bundle'] = False
v['revoked_sessions'] = sorted(set(v['revoked_sessions'] + ['nt-support-legacy']))
v['emergency_lockdown'] = False
open(p, 'w').write(json.dumps(v, indent=2))
PY
ssh -F "$CFG" nordtele 'python3 -m json.tool /etc/cybercon/policy.json'

curl -sS -H 'Cookie: nt_session=nt-support-legacy' \
  -o ~/beviser/tilbagekaldt.json -w '%{http_code}\n' "$BASE/api/reports?id=8201"
curl -sS -o ~/beviser/support-lukket.json -w '%{http_code}\n' "$BASE/support/diagnostics.json"
curl -sS -c a.cookies -d 'username=kunde-a&password=nordtele-lab-a' "$BASE/login"
curl -sS -b a.cookies -o ~/beviser/genaabnet.json -w '%{http_code}\n' "$BASE/api/reports?id=8201"
curl -sS -b a.cookies -o ~/beviser/restfejl.json -w '%{http_code}\n' "$BASE/api/reports?id=8202"
```

Forvent: tilbagekaldt session **401**, support **404**, normalt login og egen
rapport **200**. Fremmed rapport er fortsat **200**, hvis ejerskabskontrollen
endnu ikke er rettet. Denne resterende fejl skal udpeges, ikke skjules.

```json
{
  "containment_status": 503,
  "revoked_status": 401,
  "restored_status": 200,
  "remaining_cross_status": 200
}
```

Hvis eleven selv også har aktiveret korrekt ejerskabskontrol, bliver det sidste
felt **403**; en korrekt ekstra forbedring accepteres. Eleven skal måle resultatet
og dokumentere det. Kontrollen kræver et faktisk lockdown-forsøg, senere afvist
læk-session, genåbnet drift og elevens test af den resterende ejerskabsadgang.
At skrive 503 i en JSON-fil alene dokumenterer ikke, at begrænsningen fandt sted.

**Spørg til forståelse:** Hvorfor er lockdown ikke en permanent løsning? Hvorfor
er det ikke nok at skjule supportpakken? Hvad kan en ny normal kundesession stadig
gøre? Bed om fire linjers ledelsesstatus med bekræftede forhold, kundepåvirkning,
handling og næste kontrol. Flaget er en godkendelse af delmålet, ikke hele portalen.

**Bro til 13:** Eleven kan begrænse skade tidligt, begrunde prioriteringen og
kommunikere restfejl, før den varige rettelse er gennemført.

## Opgave 12 · Ejerskab: Ret fejlen og bevis normal drift

**Læringsværdi.** Eleven skelner autentifikation fra autorisation, demonstrerer
en objektadgangsfejl og laver en testmatrix, der både fanger fremmed adgang og en
rettelse, som fejlagtigt lukker alle kunder ude.

**Starttilstand.** Supporteksporten er lukket og læk-sessionen tilbagekaldt.
Ejerskabskontrollen er slået fra. Eleven arbejder videre med det isolerede
delproblem og skal dokumentere fejlen, før der ændres noget.

**Facit, trin for trin:** Opret to separate cookies og gem før-matricen.

```bash
curl -sS -c a.cookies -d 'username=kunde-a&password=nordtele-lab-a' "$BASE/login"
curl -sS -c b.cookies -d 'username=kunde-b&password=nordtele-lab-b' "$BASE/login"
for kunde in a b; do
  for rapport in 8201 8202; do
    curl -sS -b "$kunde.cookies" -o "${kunde}-${rapport}-foer.json" \
      -w "$kunde / $rapport: %{http_code}\n" "$BASE/api/reports?id=$rapport"
  done
done
```

Alle fire giver **200** før rettelsen. Ret kun den relevante politiknøgle, så de
allerede korrekte sikkerhedstiltag bevares:

```bash
ssh -F "$CFG" nordtele python3 - <<'PY'
import json
p = '/etc/cybercon/policy.json'
v = json.load(open(p))
v['enforce_report_ownership'] = True
open(p, 'w').write(json.dumps(v, indent=2))
PY

for kunde in a b; do
  for rapport in 8201 8202; do
    curl -sS -b "$kunde.cookies" -o "${kunde}-${rapport}-efter.json" \
      -w "$kunde / $rapport: %{http_code}\n" "$BASE/api/reports?id=$rapport"
  done
done
curl -sS -o anon-efter.json -w '%{http_code}\n' "$BASE/api/reports?id=8201"
curl -sS -H 'Cookie: nt_session=nt-support-legacy' -o laek-efter.json \
  -w '%{http_code}\n' "$BASE/api/reports?id=8201"
curl -sS -o support-efter.json -w '%{http_code}\n' "$BASE/support/diagnostics.json"
```

| Identitet | Rapport 8201 | Rapport 8202 |
|---|---|---|
| kunde-a | 200, rigtig rapport | 403, afvist |
| kunde-b | 403, afvist | 200, rigtig rapport |

Anonym adgang og tilbagekaldt session giver **401**; supporteksport giver **404**.
Kontrolfil:

```json
{
  "a_own": 200,
  "a_other": 403,
  "b_own": 200,
  "b_other": 403,
  "anonymous": 401
}
```

Kontrollen kræver begge faktiske fremmede 200-svar før rettelsen og den korrekte
matrix efter rettelsen. En konfigurationsændring eller fire skrevne statuskoder
alene er ikke en praktisk aflevering. Efterprøv også body, så en generisk 200-side
ikke forveksles med den rigtige kunderapport.

**Spørg til forståelse:** Hvorfor kunne en gyldig bruger læse et fremmed objekt?
Hvilken negativ test beviser rettelsen, og hvilken positiv test beskytter normal
drift? Hvorfor er en afprøvet 2×2-matrix stadig ikke en fuld sikkerhedsrevision?

**Bro til 13:** Eleven kan nu afprøve en permanent rettelse, vise at den lukker
de dokumenterede adgangsveje og samtidig genopretter normal kundeadgang.

## Overgang til opgave 13 og vurdering

I Cybercon-sagen får eleven hele problemet: opdage, begrænse, bevare, undersøge,
reparere, genåbne og rapportere. Eleven skal selv vælge rækkefølge og begrunde
prioriteringer. Træningsopgaverne giver ikke automatisk godkendelse af opgave 13;
den har sit eget miljø, egen teknisk kontrol og selvstændig rapport.

Brug denne vurderingsrubrik for hver af de fire opgaver. Giv 0–2 pr. punkt som
feedback adskilt fra CRA-point: 0 mangler, 1 delvist, 2 korrekt og reproducerbart.

1. **Metode og mandat:** eleven forklarer scope og vælger relevante tests.
2. **Beviser:** tidsstempel, kilde, identitet, request og svar kan efterprøves.
3. **Forklaring:** eleven skelner observation, fortolkning og usikkerhed.
4. **Efterprøvning:** både forventet afvisning og legitim adgang er undersøgt.
5. **Overdragelse:** konklusion, kundepåvirkning og næste skridt er tydelige.

I opgave 9 lægges særlig vægt på identitet og statuskoder; i 10 på tidslinje og
afgrænset bevis; i 11 på beslutning og restfejl; i 12 på før/efter-matrix og
regression. Flagkontrollerne bedømmer ikke skrivekvalitet, faktisk forståelse,
ledelseskommunikation eller alle mulige sårbarheder. Følg derfor op mundtligt.

Ved problemer: behold miljøet og ret den konkrete fejl. Ugyldig JSON kan rettes
via SSH uden reset. Stop/start bevarer politik, rapporter og beviser; sessioner i
portalen skal oprettes igen efter genstart. Hvis aktuelle logs er roteret ud,
skal praktiske tests gentages. Gem altid den særskilte rapport før opgaveskift.

Den samlede sag har supplerende facit i
[Cybercon · Alarm hos Nordtele](cybercon-incident.md), og den efterfølgende opgave
14 har sin egen [Request Forge-vejledning](opgave-14.md).
