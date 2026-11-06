# 04 · Missioner, images og indhold

## Et lab er virkelige maskiner plus et undervisningslag

En missionpakke beskriver læringsmål, briefing, ressourcer, tilladt scope, topology, progression, hinttrin, løsninger, flags og tests. Selve sårbarheden findes i målmaskinens faktiske opsætning. Missionens YAML bestemmer ikke, hvad `nmap` skal udskrive.

Læringsopgaver skal kunne løses med alternative korrekte fremgangsmåder. Man må ikke få bestået, fordi en kommandostreng matcher en forventet tekst. Hints kan foreslå et værktøj, men evalueringen bygger på det efterspurgte fund eller en tilstand, som kan kontrolleres pålideligt.

## Faste dele af en missionpakke

| Del | Indhold og krav |
|---|---|
| Identitet | Stabilt ID, semantic version, sprog og kursusplacering |
| Læringsmål | Hvad eleven konkret skal kunne forklare og demonstrere |
| Forudsætninger | Tidligere opgaver, introducerede kommandoer og begreber |
| Elevbriefing | Fiktiv organisation, opgave, scope og kendte fakta |
| Resource manifest | Operator-/målantal, RAM, disk, CPU, arkitektur og nødvendige features |
| Provisioning | Reproducerbare services, konti, dokumenter og bevidste fejl |
| Objectives | Konkrete kriterier, validering og fagligt formål |
| Hinttrin | Først retning, dernæst begreb, derefter mere konkret hjælp |
| Lærervejledning | Tavleoplæg, løsning, typiske fejl og debrief-spørgsmål |
| Tests | Positiv løsning, afviste løsninger, isolation, reset og arkitekturer |
| Rettigheder | Oprindelse, licens og hvilke filer der må distribueres |

I første udgave er YAML og Markdown håndredigerbare og versionsstyrede. Byg ikke en stor visuel missioneditor før formatet er afprøvet gennem flere rigtige undervisningsdage.

## Imagepipeline

Begynd med officielle, rene Linux-images og egne provisioningopskrifter. Brug en lille headless operator med et kurateret værktøjssæt; Kali-desktop er en særskilt tungere profil. Et OS-navn gør ikke en kommando mere autentisk: det er det faktiske program, netværk og rettigheder, der gør forskellen.

Pipeline: godkendt base → checksumkontrol → offline-egnet pakke-/serviceopsætning → test → cleanup af buildsecrets → låst imageartifact → laboverlays. Cloud-init og/eller Ansible kan anvendes; tag først et tungere imagebuild-framework ind, når der er et konkret behov.

Buildopskrifter ligger i Git. Store VM-diske ligger i en separat imagecache/artifactstore. Cacheindhold får content-hash, byggeversion, arkitektur, diskformat, minimumsressourcer og understøttede features. Overlays nulstilles mod den låste base; en base må aldrig ændres under en igangværende klasse.

Første understøttede arkitektur er `x86_64` (AMD64). `aarch64` tilføjes kun efter en særskilt beslutning, med egne imagebuilds og accepttests. Arkitekturspecifikke opgaver angiver dette eksplicit og nægter at starte på forkert node. En cloud-init-opskrift kan være fælles uden at imagebittene eller alle opgavers adfærd er fælles.

## Flags og validering

Flags genereres pr. lab/team og opgave. De genbruges ikke på tværs af elevmiljøer. Scoringbackend gemmer en egnet afledt verifikator, ikke klartekstflags i en endpoint-respons. Flagværdier, hintsvar og lærerens løsningsvej må ikke komme i elevens generelle missionmetadata eller projektorfeed.

En korrekt flagaflevering bekræfter viden om flaget; det beviser ikke alene, hvilken angrebsvej eller præcis rettighedsstatus eleven havde. En anden type objective kan kræve et kontrollerbart service-/filfund. Agenttelemetri fra en maskine, som eleven kan være root på, må ikke være eneste tillidskilde for en konkurrencesejr.

Validering bindes til bruger/team, missionversion, objective og labgeneration. Gentagne afleveringer giver kun point én gang. Et flagsvar fra et andet hold afvises. Skeln et ugyldigt svar fra en validator, som ikke kan kontaktes; nedbrud er ikke et forkert elevsvar.

## Solo og hold

Solo: én operator og nødvendige mål pr. elev. Hold: hver elev har som udgangspunkt sin egen operator-VM, men holdet deler målmaskiner og missionsstatus. Det giver samtidig aktivitet og bedre attribution uden at gøre et fælles login til en sikkerhedsgrænse.

Fælles noter starter med simple versionskontrollerede tekstfelter/append-only observationer og konfliktbeskeder. En realtime-collaborative editor med CRDT er ikke et krav til første holdmission. Eleverne får rolleforslag og bytter roller undervejs; platformen kan vise, hvem der skrev et fund eller afleverede et flag.

Soloarbejde gemmes som personligt forløb. Skift til hold er en ny mission/session, ikke en destruktiv konvertering af sololabs. Sololabs stoppes efter en kontrolleret overgang, så RAM frigives. Noter og faglig progression bevares separat fra VM-diske.

## Historiehændelser

Læreren kan udløse predefinerede hændelser: en labservice genstartes, en fiktiv besked frigives, et logevent oprettes eller en simuleret backup starter. Sådanne hændelser sker faktisk i labbet og mærkes som lærerudløste. Injections er afgrænsede, versionerede og testede; UI'et accepterer ikke fri root-kode mod compute-noden.

Missionshistorien må ikke fremkalde en vilkårlig ændring i SSH-/Nmap-output alene i browseren. En tidsfrist skal være en lærerbeslutning, ikke en skjult mekanik som gør værktøjerne upålidelige.

## Den første dag og den første netværksmission

**Dag 1 — First Contact:** Filsystem, `pwd`, `ls`, `cd`, `cat`, `less`, `grep`, `find`, hjælpefunktioner og brugbar dokumentation. Soloopgaven er at finde og forklare oplysninger i et rigtigt filsystem. Holdopgaven fordeler et dokumenteret hændelsesforløb mellem flere konti/mapper i holdets lab.

**Dag 2 — Network Recon:** IP, porte, HTTP/SSH-service og forskellen på connectivity og identifikation. Et ægte `nmap`-resultat og `curl`-svar danner grundlag for fund. Holdmissionen kræver at sammenholde flere fund, ikke blot at trykke hurtigst på Scan.

Eksemplet i `examples/day-02-recon.mission.yaml` er et designudkast. Der findes ingen færdig parser eller images i startpakken. M1/M2 skal indføre valideringsskema og afvise ufuldstændige manifests.

## Indholdstests

Hver udgivet mission har en automatiseret happy-path-kontrol, et lærer-walkthrough og en reset-kontrol. Testen må kun ramme egne mærkede labressourcer. Isolationsfejl stopper hele labreleasen, også hvis missionens flagtest er grøn.

En anden person skal kunne følge briefingen uden adgang til udviklerens hukommelse. Afprøv målformuleringer, forventet niveau og hinttrin med en lille pilot, før de bruges til en hel klasse.
