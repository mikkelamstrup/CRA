# 07 · Test, performance og kvalitet

## Testpyramide med ægte infrastruktur som separat gate

| Niveau | Hvad testes? | Miljø |
|---|---|---|
| Statisk | Typing, lint, kontraktdrift, dependency-/secretscan | Ubuntu-udviklingsmiljø og senere almindelig CI |
| Unit | Scoring, tilstande, adgangspolitik, hints og schedulerbeslutninger | Hurtige tests uden VM'er |
| Integration | PostgreSQL, transaktioner, jobs, API, rettigheder og events | Betroet kontrol-VM med isoleret testdatabase |
| Browser-E2E | Login, terminalvisning, genforbindelse, lærer- og projektorroller | Playwright med isolerede testkonti |
| VM-smoke | Imageboot, virkelige kommandoer, services, flag og reset | Rigtig AMD64/KVM-node; ARM64 kun ved særskilt senere understøttelse |
| Isolation | Negativ netværksadgang og tværbruger-/tværlabforsøg | Rigtige virtuelle net og VM'er |
| Belastning | Reelt antal labs, startstorm, høj outputrate og sensorload | Den faktiske serverkonfiguration |
| Undervisningspilot | Briefing, hints, samarbejde og lærerens arbejdsgang | Få testpersoner, derefter klasse |

Playwright kan køre projekter på tværs af browsermotorer. Kør browserautomatisering i det betroede Ubuntu-testmiljø, og kontrollér Chromium samt Safari/WebKit på Mac som klient og klassens faktiske browsere før undervisning. En godkendt headless-browser-test erstatter ikke manuel afprøvning af tastatur, terminal og projektor. Kilde: S21.

## Obligatoriske negative tests

1. Elev A kan ikke læse, resette, forbinde til eller abonnere på elev B's lab eller events.
2. Hold A kan ikke påvirke hold B; elevkonto kan ikke kalde lærerruter eller node-management.
3. Root i operator/mål kan ikke nå Mac, skole, hjem, internet, database eller compute-management — heller ikke via IPv6, ændret IP eller en eksisterende proxy.
4. En gammel terminalbillet eller event fra før reset kan ikke bruges mod næste labgeneration.
5. To samtidige korrekte afleveringer giver point én gang; genleveret event ændrer ikke totalscore.
6. Forfalsket/for stor netværks- eller telemetry-payload accepteres ikke som bekræftet opgaveløsning.
7. Ondsindet terminaloutput udfører ikke HTML/JavaScript, læser ikke clipboard og bliver ikke til hostkommandoer.
8. Disk fuld, nodefejl, databasegenstart og mistet WebSocket resulterer i tydelig, genoprettelig tilstand.
9. Et ugyldigt eller forkert-arkitektur-image kan ikke få en session til at se klar ud.
10. Projektorfeed og skjulte missionmetadata indeholder ingen flags, svar, private terminaler eller personlige hjælpenoter.

Der laves også positive modkontroller: en isolationstest er ikke meningsfuld, hvis hele labnettet er dødt. Tilladt operator→mål-trafik skal virke samtidig med, at forbudte veje afvises.

## Performancebudget — mål, ikke opnåede resultater

Definér målemaskine, browser, dataset og belastningsprofil før målingen. Et gennemsnit er ikke nok: brug p50/p95 og maksimal kø-/bufferstørrelse.

| Metrik | Første acceptmål |
|---|---|
| Terminal tast→synligt echo over lokalt LAN | p95 højst ca. 150 ms i normal arbejdsbelastning |
| Normal API-respons, uden VM-provisioning | p95 højst ca. 300 ms på målopsætning |
| Bekræftet objective→dashboard | typisk inden for 1 sekund uden backlog |
| Varmt lab, templates allerede lokale | indledende mål højst 60 sekunder til “ready”; mål faktisk |
| Cold boot/imagehentning | vis separat forventning/progress; ingen falsk spinnergaranti |
| Projector-render | 30 fps minimumsmål på målbrowser; sigt mod 60 når muligt |
| Lang terminaloutput | ingen tabte bytes; afgrænset RAM og korrekt backpressure |

Mål den faktiske Ubuntu/KVM-konfiguration med de samtidige udviklingsprocesser registreret. En eventuel senere ARM64- eller emuleret profil kræver selvstændigt testbevis. Sænk ikke klassekravet stiltiende for at få en grøn testrapport. Hver threshold kan justeres efter dokumenteret første benchmark og faglig accept, ikke efter en tilfældig langsom test.

## Scenarier til belastningstest

En elev, to elever i separate labs, to i samme hold, fem, ti, femten og tyve elever. Gentag start af en hel klasse, mange samtidige scans inden for scope, mange flagafleveringer, intensiv terminaloutput, sensorburst, skift solo→hold, og nedlukning af alle labs.

Test offline-drift efter forberedelse: images og undervisningsindhold skal ligge lokalt, og internetudfald må ikke forhindre login, terminal, hints eller scoring i en forberedt installation. Første installation og softwareopdateringer kan kræve netadgang.

Mål faktisk RAM pr. node, vCPU-belastning, disk-I/O, overlayvækst, netværk, drop af capturepakker og antallet af WebSockets. Sæt en cap på samtidigt bootende labs. Syntetiske API-brugere uden VM'er dokumenterer kun kontrolplanet.

## Fejlindsprøjtning

Stop worker efter et image er klonet, men før databasen er opdateret. Send samme job to gange. Lad et nodeheartbeat udløbe. Genstart API med aktive sessions. Afbryd en terminal midt i `less` eller en lang kommando. Reconnect må ikke gentage tastetryk eller starte shellkommandoen igen.

Tilføj test for forsinkede/duplikerede events, reset mens en session er aktiv og ufuldstændigt destroy. Reconciler skal finde projektejede ressourcer og genoprette kendt tilstand uden at røre andre maskiner på værten.

## Tests på udviklingsserveren

Kør hurtige tests på serverens udviklingskonto og browser-/databaseintegration i den betroede kontrol-VM fra starten. En hosted CI-service er valgfri. Almindelige tests får ikke privilegeret adgang til labinfrastruktur; VM-/isolationstests er et separat godkendt spor mod egne mærkede ressourcer. KVM kan ikke antages tilgængeligt på en vilkårlig CI-runner. Mac-klienttests er supplerende brugeroplevelsesbevis, ikke serverkapacitet.

Ingen self-hosted runner på skolens produktionsnode må ukritisk udføre kode fra ubetroede pull requests. Et åbent repository og privilegeret labinfrastruktur kræver adskilt build-/testtillid.

## Releasegate til klassebrug

Alle aktuelle opgaver består positiv løsning og reset på klassearkitekturen. Ingen kendte kritiske/high fejl i isolations- og adgangsgrænser. Backup er faktisk gendannet i et separat testmiljø. Klasseantal er afprøvet på målmaskinerne. Læreren har en nødprocedure og en oversigt over hvilke labs der må nulstilles.

Kontrolplatformens dependencies holdes opdaterede. Bevidste sårbarheder i opgaveimages er registrerede undtagelser; de må ikke filtreres væk i hele repositoriets scanning, så reelle sårbarheder i platformen også skjules.

## Testbevis

For hver gate gemmes: commit, task-ID, image-digest, OS/arkitektur, nodeantal, elev-/VM-antal, kommando, start/sluttid, resultat, relevante målinger og NOT RUN med årsag. Brug syntetiske data og redigeret output. Testbevis er en lille reproducerbar rapport, ikke tusindvis af loglinjer i en chat.
