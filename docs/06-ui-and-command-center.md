# 06 · Brugeroplevelse, intensitet og Command Center

## Designretning

Oplevelsen skal ligne et gennemført operationsmiljø, ikke et kommandolinje-rollespil. Mørk baggrund, læsbar typografi, få statusfarver, velafgrænset animation og tydelig sammenhæng mellem handling og feedback. Farve er aldrig eneste statusindikator. Lyd, bevægelse og pulserende effekter skal kunne slås fra.

Brug først et solidt 2D-layout og stabilt netværkskort. Byg senere en Three.js/React Three Fiber-projektorvisning, når korrekte data og målt renderbudget er på plads. WOW-effekten er missionens atmosfære, klart synlige ægte hændelser og et system som føles øjeblikkeligt responsivt, ikke tilfældige “hack detected”-animationer.

## Tre separate visninger

**Operator:** Terminal først, en kompakt briefing, kendt scope, notesbog, konkrete objectives og hjælp. Paneler kan foldes væk. Terminalen understøtter fuldskærm, resize, danske tegn, kopier/indsæt med passende kontrol, Ctrl-C, Ctrl-D, Tab, pipes, omdirigering, `less` og teksteditorer. Senere findes en separat grafisk labdesktop.

**Teacher:** En oversigt over klasse, dag, fase, aktive labs og kapacitet. Vis bekræftede objectives, hjælpeanmodninger, sidste kendte aktivitet og tydelige tekniske fejl. Læreren kan gå ind på et team, sende et hint, frigive indhold og nulstille det valgte lab. Destruktive handlinger angiver præcis hvilke labs og hvad der mistes.

**Projector:** Read-only holdvisning med netkort, missionsfase, anonymiserede/valgte holdnavne, events og finale-score. Den har eget begrænset datasæt og egen adgangskontrol. Den må ikke opstå ved blot at åbne lærerens skærm i fuldskærm.

## Pause er ikke ét begreb

“Pause timer”, “spær terminalinput”, “gem og stop lab” og “nødstop netværk” er forskellige handlinger. GUI'et bruger forskellige betegnelser og viser kvitteret tilstand. At pause point stopper ikke allerede kørende processer. At suspendere VM'er har konsekvenser for netværk og tid. Et tabt nodeheartbeat betyder, at en handling kan være ubekræftet.

## Sandfærdig telemetri

| Kategori | Eksempel | Hvad må UI sige? |
|---|---|---|
| Observeret trafik | Forbindelse fra en bestemt operator-vNIC til målport | “Trafik observeret mod webservice” |
| Indleveret fund | Eleven beskriver en service eller uploader godkendt Nmap-XML | “Fund indleveret af operatøren” |
| Valideret objective | Backend har accepteret korrekt svar/flag | “Objective gennemført” |
| Detektionssignal | Sensorregel giver et match | “Alarm fra regel X”, ikke “angreb lykkedes” |
| Lærerhændelse | Et kendt script stopper labservice | “Lærerudløst hændelse” |

En forbindelse til port 22 viser ikke automatisk et vellykket login. Et accepteret root-flag beviser ikke præcis, hvordan root blev opnået. Zeek/Suricata ser ikke vilkårligt kommandotekst i krypterede SSH-sessioner. En sensor kan miste pakker; den tilstand skal vises. Kilder: S13–S14.

## Fog of war

Spillerens netkort må ikke være sensorens fulde sandhedskort. Brug i første version dokumenterede elevfund og validerede objectives til at frigive information. Et scanresultat kan importeres som et elevindleveret fund, men XML/JSON er upålideligt input og skal parses sikkert. Sensortrafik kan vise aktivitet i allerede kendt scope uden automatisk at afsløre skjulte maskiner eller løsninger.

Læreren kan få et fuldt topologikort, fordi det kommer fra missiondefinitionen. Projektoren får en særskilt sanitiseret projektion. På den måde skal man ikke løse et umuligt problem, hvor passiv trafik antages at fortælle præcis, hvad eleven har læst og forstået.

## Arkitektur for flydende interface

Hold terminalstrømmen uden for almindelig React-state. Buffer/render med terminalbibliotekets egne mekanismer. Flow control og begrænsede buffere er nødvendige; WebSocket transport giver ikke automatisk sikker backpressure. Kilde: S8.

Fordel liveopdateringer i to strømme: opgavestatus/point med høj pålidelighed og visualiseringsdata med begrænset frekvens. Aggreger flows i korte vinduer, og send ikke ét UI-event pr. pakke. Et travlt netkort må gerne reducere detaljegrad; en terminal må ikke miste bytes eller input.

3D bruges kun, når brugerens browser kan bære det. Understøt 2D-fallback, `prefers-reduced-motion`, stabil zoom, tastaturnavigation og læsbar projektorvisning i 1080p. Start med højst få dusin synlige noder og kontrolleret antal animationer; mål senere i faktiske klassebrowsere.

## Replay

Gem domæneevents med event-ID, kilde, labgeneration, observeret tid og modtagelsestid. Replay genskaber hændelsesforløbet og scorerelationer, ikke nødvendigvis hele maskinens bit-for-bit-forløb. Hold historik og en maskins nuværende tilstand adskilt.

Netværksreplay og terminaloptagelse er forskellige funktioner. Standardreplay kan vise start, afleveringer, hjælp og udvalgte flows uden at lagre samtlige terminalbytes. Under konkurrencer må replay ikke frigive løsninger til andre aktive hold.

## Tidlig visuel accept

Når M2 er funktionel, skal der allerede være en tilfredsstillende operatoroplevelse, gode tomme tilstande, fejlvisning, loading og genforbindelse. M6 polerer og udvider; det er ikke tidspunktet, hvor læsbarhed og UX først bliver overvejet.

Tag screenshots af kendte syntetiske scenarier og test alle fire tilstande: klar, i gang, tabt forbindelse og fejl. Layout, terminalfokus, tab-navigation og tekst skal vurderes på en almindelig elevlaptop, ikke kun Mac'ens kraftige skærm.
