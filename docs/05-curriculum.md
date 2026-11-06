# 05 · Undervisningsforløb

## Ramme

15–20 elever; to dage om ugen à syv lektioner. Fire undervisningsuger giver otte dage og 56 lektioner. Forløbet kan udvides til otte undervisningsuger og 112 lektioner. Planen er et forslag til lærerens undervisning, ikke en angivelse af en bestemt officiel fagplan.

Normal dagsrytme: **lektion 1 teori og demonstration, lektion 2–4 individuel mission, lektion 5–7 holdmission**, med de sidste 10–15 minutter til fælles debrief. Pauser følger skolens skema. Mængden af ny teori kan reduceres, og demoer kan gentages, uden at det ændrer missionens læringsmål.

Hver dag har et kerneforløb og bonusopgaver. Hjælp i læringsforløbet koster som udgangspunkt ikke point; eleven skal turde bede om hjælp. I finalen kan hints have en på forhånd annonceret omkostning. Der skal altid være en vej gennem dagens kernemål uden at kende værktøjer, der endnu ikke er introduceret.

## Fire ugers kerneforløb

| Dag | Fagligt mål / teori | Solo | Hold / debrief |
|---|---|---|---|
| 1 | Scope, etik, Linux, filsystem, hjælpefunktioner | First Contact: navigér, søg og dokumentér i rigtige filer | Lost Signal: kombiner holdets fund; forklar én kommando hver |
| 2 | IP, net, porte og services | Network Recon: kend tilladte mål og identificér services | Ghost Network: fordel kortlægning og aflevér et begrundet netkort |
| 3 | HTTP, DNS og client/server | Web Inspector: `curl`, headers og DNS i labbet | Service Desk: find den rigtige webservice og begrund fundene |
| 4 | SSH, brugere, filrettigheder og least privilege | Access Granted: autoriseret login og rettighedsundersøgelse | Broken Access: løs en kontrolleret adgangsfejl og dokumentér ændringen |
| 5 | Logs, tid, netværkstrafik og PCAP | Packet Hunter: analysér en lokal capture med `tcpdump` og/eller Wireshark | Incident Timeline: sammenhold flere spor uden at opfinde årsager |
| 6 | Webautentifikation og en afgrænset appfejl | Vulnerable App: undersøg en bevidst sårbar øvelsesapp | Web Investigation: forklar fejl, effekt og en relevant afhjælpning |
| 7 | Systematisk undersøgelse, dokumentation og sammenhængende fund | Operation Shadow: kæd kendte teknikker | Qualification: mini-CTF og gennemgang af regler/scoring |
| 8 | Scope, samarbejde og strategi | Kort fælles opvarmning og tekniktjek | Cyber Championship med holdkonkurrence, efterfulgt af løsningsgennemgang |

Dag 3–6 kræver mere end en terminal i browseren, hvis eleverne skal bruge en grafisk browser, Burp eller Wireshark. Derfor skal den grafiske labprofil afprøves i M5. Browseren/GUI-værktøjet skal være inde i labbet; en blind reverse proxy til bevidst sårbar webkode under platformens origin er ikke løsningen. Kilde for browserbaseret remote desktop: S15.

## Eksempel på dag 2

Læreren viser forskellen på maskine, IP, port og service, og demonstrerer en tilladt scanning i eget lab. Eleverne læser en kort scopeboks og starter hver deres lab. Der er tre kernemål: finde det relevante system, identificere services, forklare hvad et portfund betyder og ikke betyder.

Solo-hints gives i trin: “hvilken type information mangler du?”, “hvilket værktøj kan hjælpe?” og først derefter et mere konkret eksempel. Bonusopgaven kan handle om at sammenligne værktøjers resultater eller dokumentere en misvisende bannertekst.

I holdmissionen får 3–4 elever hver deres operator, men et fælles målmiljø. Én kortlægger, én undersøger services, én dokumenterer, og en fjerde efterprøver. Roller byttes. Læreren ser labstatus og bekræftede fund; storskærmen kan vise holdenes fremdrift uden løsninger.

Debrief: Hvad observerede I? Hvad kan I bevise? Hvilken antagelse viste sig forkert? Hvad ville I gøre først næste gang? Der gives ikke automatisk en dårlig vurdering for få tastetryk eller få sensorhændelser.

## Differentiering

Tre opgaveniveauer kan præsenteres som Kerne, Selvstændig og Ekstra. Eleven vælger en rolig elevvisning uden løbende ranking. Læreren kan frigive flere ledetråde, ekstra tid eller et mindre målmiljø. Tidsbegrænsninger bruges bevidst i holdspil og konkurrence, ikke som konstant pres under indlæring.

Der skal være en konkret “jeg er kørt fast”-knap og en mulighed for at beskrive, hvad eleven allerede har forsøgt. Den hjælper lærerens prioritering bedre end automatisk at kalde en stille terminal for inaktivitet.

## Finalen

Brug først en enkel Jeopardy-lignende konkurrence: flere uafhængige opgaver i kendte kategorier og en samlet finalemission. Holdene vælger rækkefølgen. Der er unikke flags pr. hold, offentliggjorte faste point, tydelige hintregler og en serverstyret deadline.

Forslag til point: små Linuxopgaver 100, netværk 150, web/HTTP 200, PCAP 250 og en sammenhængende mission 400. Tallene er designforslag, som afprøves i generalprøven. Mere avanceret dynamisk pointberegning udskydes.

Første konkurrence skal ikke kræve, at hold angriber hinandens maskiner. Hvert hold har sin egen kopi af udfordringerne. Så undgår man, at et hold ødelægger et andet holds læringsmiljø. En separat lærerbedømmelse kan belønne forklaring, dokumentation og samarbejde; den blandes ikke uigennemsigtigt ind i den automatiske score.

## Måned 2

Uge 5: dybere web-/adgangsproblemer. Uge 6: Linux-hardening og dokumenterede ændringer. Uge 7: Blue Team, logs, sensorer og kontrollerede Red/Blue-øvelser. Uge 8: sammenhængende hændelsesforløb og ny finale.

Red/Blue er en særskilt senere missionstype med egne regler for reset, scope og ødelæggelse. Kernel-exploits, Windows/AD og malwarehåndtering må ikke glide ind som en hurtig bonus uden særskilt ressource-, licens- og sikkerhedsgennemgang.

## Materiale som skal følge hver dag

Lærernoter til tavlen, elevbriefing, oversigt over introducerede kommandoer, soloopgaver, holdopgave, hinttrin, facit/løsningsvej, typiske misforståelser og debrief-spørgsmål. Alt skal kunne eksporteres som tekst og bruges uden at læreren først skal spørge en AI om instruktioner.
