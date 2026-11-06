# Cybercon · Alarm hos Nordtele

Én individuel mission, nummer 13 i kataloget. Afsæt cirka fire lektionstimer til
undersøgelse og reparation samt tid til rapport og gennemgang. Eleven er ansat hos
Cybercon. Nordtele er en fiktiv telekoncern med millioner af kunder; labbet modellerer
kun en del af erhvervskundeportalen, ikke en hel teleinfrastruktur.

## Frigivelse og besked

Vælg eleven under Command Center → Elever og opgaver → Frigiv opgave 13.
Missionen kræver altid en individuel frigivelse, også uden en aktiv klassesession.
Inden for normalt tre sekunder får en online elev en stor centreret besked fra
Cybercons indsatsleder, også mens eleven er i et andet lab. Browserfaner i baggrunden
kan blive forsinket; beskeden kontrolleres igen, når fanen aktiveres.
En offline elev får beskeden ved næste login. Den forsvinder permanent for eleven,
når eleven kvitterer. Genindlæsning alene kvitterer ikke, og gentagen frigivelse
sender ikke samme sag igen. Lærerkonti får ingen alarm. Ved forbindelsesfejl kan eleven lukke beskeden uden
at kvittere; den dukker så op ved næste besøg. Efter installation af denne nye
funktion skal allerede åbne CRA-faner genindlæses én gang før første frigivelse.

Eleven kan åbne opgaven eller kvittere og blive på siden. Ugemte CRA-noter udløser
den eksisterende advarsel før navigation. Beskeden starter, fjerner eller nulstiller
ingen maskiner. Et gammelt lab håndteres gennem det eksisterende opgaveskift.

## Sammenhæng og facit

1. Supportpakken `/support/diagnostics.json` er offentligt tilgængelig og indeholder
   en fiktiv kundesession. Faktiske requests fra labbets øvelsesproces ligger i logs.
2. Sessionen tilhører kunde A. Manglende ejerskabskontrol lader den læse kunde B's
   rapport. Eleverne skal knytte de to fejl sammen og dokumentere datalækket.
3. Midlertidig `emergency_lockdown: true` afviser dataadgang med 503, men afbryder
   også normal kundedrift. En mere målrettet tidlig handling er sessionstilbagekaldelse.
4. Slutpolitikken har `emergency_lockdown: false`, `enforce_report_ownership: true`,
   `expose_support_bundle: false` og `revoked_sessions: ["nt-support-legacy"]`.
5. `cybercon-check` på målmaskinen kræver begge øvelsesservices aktive og tester
   portalen via HTTP: anonym og lækket session får 401; supporteksport får 404;
   begge kunder kan logge ind og læse egen rapport; krydslæsning får 403.
   Kun den tekniske slutkontrol frigiver flaget. Fortsat angrebsaktivitet i loggen
   med afviste svar er forventet og betyder ikke, at reparationen har fejlet.

Politikken er en rigtig serverkonfiguration, der ændrer håndhævelsen ved hver
forespørgsel. Denne udgave træner driftsmæssig afhjælpning; eleverne skriver ikke
en ny webserver eller ændrer kildekode. De har ingen generel sudo-adgang.

En elev kan redigere filen uden en editor, eksempelvis ved at indlæse JSON i Python,
ændre udvalgte nøgler og skrive tilbage med `open(path, 'w')`. Giv først dette hint,
når eleven har forklaret den ønskede ændring. Fejlformet JSON afviser dataadgang og
kan rettes via SSH; et labreset er ikke nødvendigt.

## Undervisning og vurdering

Hold en kort mundtlig status efter første begrænsning: Hvad ved du? Hvad har du
ændret? Hvad er konsekvensen for kunderne? Lad eleven arbejde individuelt videre.
Rapportskabelonen ligger på operatoren i `academy/cybercon/rapport.md`.
Eleven afleverer selv rapport og udvalgte beviser gennem undervisningens sædvanlige
afleveringskanal. CRA-point gemmer den tekniske kontrol, ikke rapporten.

Vurder selvstændigt: bevisbevaring og tidslinje; korrekt forklaring af kæden;
begrundet begrænsning; reparation og positive/negative tests; tydelig ledelsesstatus
og usikkerheder. Spørg hvad logs kan dokumentere om faktisk udleverede svar, og hvad
de ikke viser om senere brug af data. En lokal kildeadresse er ikke attribution.
Et flag alene er ikke tilstrækkelig faglig aflevering.

Første logs er bevaret skrivebeskyttet i målet. Aktuelle logs roteres med en
begrænset størrelse. Alle requests er ægte, men hændelsen er syntetisk. Angrebet
kører inde i målmaskinen med to requests hvert femte sekund og kræver ingen tredje
VM eller internetadgang. Det er ikke en belastnings- eller DDoS-øvelse.

Stop/start bevarer arbejde og rettelser. Reset genskaber den sårbare starttilstand
med nyt generationsbundet flag. Bed eleverne eksportere rapport og beviser før
reset, fjernelse eller opgaveskift. Den tekniske kontrol er et øjebliksbillede;
et tidligere flag beviser ikke, at en efterfølgende ændring stadig er sikker.
