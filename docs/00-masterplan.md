# 00 · Masterplan

## Beslutningen i én sætning

Byg først en **autentisk Linux-cyberrange på din dedikerede Ubuntu-server**, derefter én hel undervisningsdag med solo og holdspil, dernæst et gennemarbejdet Command Center, og til sidst den målte klasseinstallation på en eller flere servere. Teknisk kvalitet er ikke lig flest komponenter: få velafgrænsede komponenter med stærke tests er udgangspunktet.

## Produktets faste rammer

Primær bruger er crauser som lærer på en undervisningsinstitution. Eleverne er en gruppe på 15–20. Planlægningsgrundlaget er fire undervisningsuger, to dage pr. uge og syv lektioner pr. dag: 56 lektioner. En udvidelse til otte undervisningsuger giver 112 lektioner. En kalendermåned er ikke nødvendigvis præcis fire undervisningsuger.

Hver normal dag begynder med én lektion teori og demonstration, fortsætter med individuel træning og slutter med holdmission. Der afsluttes med en CTF mod hinanden. De samme faglige begreber skal optræde i dagens teori, solo og holdmission. Rolige visninger, dansk hjælpetekst, valgfri lyd, tydelige mål og ekstra udfordringer indbygges; konkurrence må ikke være den eneste måde at opleve fremskridt på.

Kernekravet er virkelighedstro teknik: Bash, programmer, processtyring, SSH, filrettigheder, netværk og sårbarheder skal være faktiske forhold i Linux-miljøer. Spilfortælling og animation må understøtte arbejdet, aldrig fabrikere værktøjsoutput. Browseren er arbejdsflade, ikke en kommandosimulator.

Første brug er egen undervisning. Produktet skal kunne flyttes og udvides, men licens, open source og kommerciel model er ikke besluttet. Derfor skilles skoleopsætning, indhold, branding og platformskode ad uden at bygge abonnementsadministration eller offentlig cloud nu.

## De vigtigste arkitekturvalg

| Område | Anbefalet valg | Begrundelse |
|---|---|---|
| Web | React, TypeScript, Vite | En separat browserflade for elev, lærer og projektor |
| Backend | Python/FastAPI som modulær monolit | Ensartet udvikling og test uden tidlig servicespredning |
| Tilstand | PostgreSQL | Én sandhed for brugere, forløb, jobs, events og scoring |
| Terminal | xterm.js + WebSocket-relay + SSH/PTY | Rigtig Linux-session og rigtig procesadfærd |
| Labs | QEMU/KVM via libvirt | Fuld VM-grænse omkring bevidst sårbare systemer |
| Første vært | Ubuntu på Ryzen 5 7500F, AMD64 og KVM direkte på værten | Hele udviklingsmiljøet på én server; Mac som SSH-/browserklient |
| Infrastruktur | Compose i betroet kontrol-VM; node-agent på compute-værten | Ingen adgang fra elever til Docker-/libvirt-management |
| Grafik | 2D først; Three.js/R3F til projektorvisning efter målt behov | Terminalydelse og overskuelighed først |
| Telemetri | Domæneevents først, derefter bridge-sensor/Zeek; Suricata senere | Tydelig forskel på opgavestatus og observeret trafik |
| Agentarbejde | Ét repository, task-ID'er, worktrees, uafhængigt review | Små verificerbare leverancer og en fælles hukommelse |

Det er vores designvalg, ikke dokumenterede hastighedssammenligninger. De underliggende funktioner beskrives i [kilderne](SOURCES.md), særligt S7–S15 og S30–S33.

## Implementeringsrækkefølge med gates

| Milepæl | Leverance | Hvad skal være bevist, før vi går videre? |
|---|---|---|
| **M0 · Ubuntu/KVM-spike** | Læsende preflight, dedikeret AMD64-vært, kontrol-VM, operator/mål og netværkspolitik | Rigtig gæst booter; SSH/curl/nmap virker; forbudte forbindelser blokeres; reset gentages |
| **M1 · Fælles fundament** | Repo, tests, minimal web/API, PostgreSQL, lokale testidentiteter | Ren opsætning er gentagelig; migrationer virker; roller og kontrakter er testede |
| **M2 · Første spilbare lab** | Start → terminal → fund → flag → reset | Browseren styrer en rigtig VM; CLI og UI er samme sessionstype; ingen fake driver i accepttesten |
| **M3 · Flere brugere og robusthed** | To adskilte labs, genforbindelse, jobkø og oprydning | Ingen tværadgang; ingen dobbeltoprettelser; genstart giver kendt tilstand og ingen tabte point |
| **M4 · Én undervisningsdag** | Dag 1-materiale, progression, hints, lærerstart og refleksion | Læreren kan gennemføre teori og solo; en anden testbruger kan følge opgaven uden udviklerhjælp |
| **M5 · Holdmission og grafisk adgang** | Separate operatorer mod fælles holdmål; valgfri lab-desktop | To elever kan bidrage samtidig; roller er synlige; andre hold er isoleret; grafiske værktøjer testes før web-/PCAP-dagene |
| **M6 · Command Center** | Lærerpanel, projektion, telemetri, evidensmærkning og replay | Projektor får kun godkendte data; langsom grafik påvirker ikke terminaler; kilde/tabt telemetri vises |
| **M7 · Fire ugers undervisning og CTF** | Otte sammenhængende dage, versionerede missioner, enkel turneringsmotor | Opgaver kan løses og nulstilles; konkurrence kan afvikles og genskabes fra scorehistorik |
| **M8 · Kapacitet og serverdrift** | Driftsprofil, eventuel node 2 og klasseplacering på eksisterende AMD64-grundlag | Kapacitet og imagekompatibilitet er målt; placement og fejlhåndtering virker |
| **M9 · Klasseklar release** | Belastningstest, pilot, backup/restore, nødprocedure og godkendelser | Faktiske 15–20 sessioner på den valgte hardware; ingen åbne kritiske isolation-/dataproblemer |
| **M10 · Muligt produkt** | Rettigheder, licens, dokumenteret installation, udvidelser | Ny installatør kan installere uden lærerens personlige opsætning; udgivelsesmodel er besluttet |

M0–M7 udvikles på den første Ubuntu-server med få virkelige AMD64-labs. Kode, agenter og kontrolmiljø ligger på serveren; Mac bruges som klient. Serveren har endnu andre projekter: M0-01 er læsende, mens M0-02 kræver bekræftet dedikering og godkendt installationsscope. Browser- og API-belastning med syntetiske klienter dokumenterer ikke tyve samtidige VM-labs. M8–M9 skal bevise den valgte klassekapacitet med eventuel opgradering eller flere noder. En lokal Mac-runtime er ikke længere en forudsætning; se ADR-002.

M0 er bevidst før en stor frontend: netop faktisk KVM-drift og sikker isolation på den fælles fysiske vært er de største usikkerheder. M1 må gerne bygge et enkelt visuelt designsystem, men må ikke erstatte M0 med en pæn demonstrationssimulator.

## Hvad den første komplette demonstration skal vise

Du åbner elevvisningen fra din Mac. En mission starter en operator-VM og en mål-VM. Eleven bruger `ls`, `cat` og senere `nmap` og `curl`. Du kan se forbindelsesstatus og bekræftede opgavetrin fra lærervisningen. En forkert flagaflevering giver ingen point. En korrekt giver point én gang, også ved gentagen indsendelse. Reset genskaber filsystemet og lukker gamle sessions. Ingen af maskinerne kan kontakte Mac'ens tjenester, skolens netværk eller internettet.

Når dette er robust, åbnes to elevkonti mod et fælles holdlab og senere en separat projektorvisning. Det er produktets første reelle kerne, ikke en throwaway prototype.

## Definition af færdig

En funktion er færdig, når den er implementeret, testet på det angivne niveau, negativtestet, dokumenteret, reviewed og integreret. Et grønt unit-testresultat er ikke bevis for et fungerende VM-lab. En vedlagt screenshot er ikke bevis for isolation. En fremtidig task er ikke implementeret, fordi en API-stub returnerer `ok`.

Rapportér altid PASS, FAIL eller NOT RUN med miljø, imageversion og commit. Gate-undtagelser er synlige beslutninger; sikkerhed og ægte kommandoudførelse må ikke undtages for at nå en demo.

## Ikke i første release

Ingen offentlig multi-tenant SaaS, Kubernetes, Ceph, Proxmox-klynge, Windows/Active Directory, kernel-exploit-opgaver, autonom AI-dommer, fleragent-runtime i elevproduktet, stemmeinterface, marketplace eller avanceret dynamisk CTF-scoring. Proxmox, CTFd, SSO og nye sensorer forbliver mulige integrationer bag klare grænseflader.

Høj kvalitet betyder også at stoppe rigtigt: når en milepæl er godkendt, tag et Git-tag og behold en kendt fungerende demo, før du ændrer næste lag.
