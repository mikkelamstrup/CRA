# Releasepunkter efter M7

Status 2026-10-26. Ejer har bestilt både elevopgaver og tekniske projektopgaver, udvidet målet til **18 almindelige elever med hver sin terminal og en separat testkonto** og oplyst, at der ikke findes en fast elevadresse. Eleverne skal også kunne arbejde hjemmefra via internettet. De 12 oprindelige gemte labs er bevaret og forbliver STOPPED. De fire nye elev15–elev18-labs er autoriseret oprettet og er READY; de syntetiske prøver er ryddet op.

## Leveret lokalt

Otte dage med elevindhold og lærerpakker, faktiske Linux-/DNS-/SSH-/PCAP-/webopgaver, solo/hold/GUI, noter og frigivelser, hjælp, Command Center, projektor, replay og hold-CTF. Se handoffs/M6-04 og M7-01–04. M7-04 beviser lokal undervisning, mens kontrolgæstens eksterne default-ruter er væk, og korrekt gendannelse bagefter. Databasebackup/restore af syntetiske noter og konkurrencescore er prøvet i separat testdatabase. Det er ikke en komplet skolepilot eller katastrofegendannelse.

## Åbne gates

| ID | Status og konkret næste bevis |
|---|---|
| M4-03, menneskelig del | NOT RUN: en lærer og få testpersoner gennemfører opgaver, tastatur/terminal, hjælp og projektor i de faktiske klientbrowsere. Automatisk Chromium er ikke en menneskepilot. |
| M8-01 | Node1 alene har for lidt RAM. To-node-profilen med 18 CLI-solos er faktisk afprøvet: 6+12 labs, mindst 13.145/9.340 MiB ledig RAM, ingen swap. Funktionel load/reset/genforbindelse PASS; den strenge offentlige test er rå FAIL på terminalens p95. LAN-gaten er NOT RUN. |
| M8-02 | AMD64-basis og offlineværktøjer er versions-/hashlåste og faktiske missioner består solo/reset/hold. Endelig releaseimage-/driftsprofilaccept afventer den valgte kapacitet. |
| M8-03 | Schema24, fast nodebinding, separat mTLS-relæ, to workers og faktisk 18-solo-funktionalitet er installeret og prøvet. Offentlig terminal p95 var 340,4 ms mod testens 150 ms LAN-reference. Den faktiske LAN-gate er NOT RUN. Faktisk offentlig GUI/terminal, nodeudfald/recovery, stop/start/reset og oprydning PASS. Ingen automatisk failover. Se acceptbeviserne. |
| M8-04 | Kontrolservices kører i VM, men stabil elevadresse/TLS og dokumenteret klasseadgang er ikke etableret. En ny midlertidig Quick Tunnel er oprettet efter ejerens valg; faktisk offentlig browser-login/logout og cookie-/Origin-kontrol PASS. En separat named-tunnel-konfiguration er klargjort og lokalt valideret; se adgangsplanen. Den er ikke aktiveret. Vedvarende HTTPS-internetadgang skal vælges og prøves både fra elevnettet og hjemmefra; en lokal testorigin er ikke nok. |
| M9-01 | Faktisk 18-solo startkø, 30 sekunders overlappende CPU/RAM-load og 18 samtidige genforbindelser PASS funktionelt. API p95 under load 176,4 ms mod 300 ms. Offentlig terminal p95 340,4 ms mod testens 150 ms LAN-reference er rå FAIL; faktisk LAN-test er NOT RUN; kø-til-READY 103,57–1125,90 s overskrider 60-sekundersmålet. Længere soak, fysisk elevklientpilot og 20 solos er NOT RUN; 20 er ikke ejerens mål. |
| M9-02 | Separat PG-restore og kontrolgæst-offlineprøve PASS. Komplet restore af nodejournal, private nøgler og VM-diske til en afskærmet instans, fysisk adgang under internetudfald samt ekstern backupplacering/retention er stadig åbent. |
| M9-03 | Skolens dataansvar, konti, opbevaring, adgang og menneskepilot kræver skolens/ejerens beslutninger. Der er ikke indført live-elevdata i accepttests. |
| M9-04 | Klasseklar v0.1 er ikke udgivet; kræver ovenstående kapacitets-, adgangs-, restore- og pilotgates. Lokal kodeintegration er ikke en releasegodkendelse. |
| M10-01–03 | Licens, ejerforhold, komponentvilkår, installation af anden person og produktmodel er ikke besluttet. Ingen offentliggørelse eller betaling er aktiveret. |

## Ved næste driftsbeslutning

Node2 er installeret og midlertidig internetadgang virker. De funktionelle 18-solo-prøver er afsluttet. Afklar den målte forsinkelse ved første samtidige input, forbedr opstarten og prøv længere belastning samt faktiske elevklienter. Udfør fuld restore isoleret før menneskepilot. Driftsvejledningen beskriver de eksisterende kontroller, backupgrænser og nødprocedure.

Originale labdiske, konti, noter, point og generationer må ikke nulstilles for at frigøre testkapacitet. Bevar versionslåse og dokumentér ændringer i CURRENT_STATE; undgå at kalde en delprøve et fuldt klassebevis.

Ejer har valgt node2-example som ekstra node og en ny midlertidig Cloudflare-tunnel som aktuel internetadgang. KVM-/gruppeproblemet er løst, og node2-runtime samt produktprøve er PASS. Historisk preflight er bevaret i handoffs/M8-03-preflight.md. Fast adresse er ikke valgt, og et domænekøb er ikke nødvendigt for det aktuelle midlertidige valg.

## Udvidelse til 18 terminaler

CLASS-CAPACITY-03 følger ejerens nye mål om 18 almindelige elevkonti. Historiske 15-elevresultater bevares i handoffs/M8-03.md; 18-elevprøven og oprettelsen af elev15–elev18 er dokumenteret særskilt i handoff. Per-nodegrænser, RAM-reserve og eksisterende gemte labs ændres ikke.
