# 01 · Arkitektur

## Én produktkerne, flere sikkerhedsgrænser

Begynd med et monorepo og en modulær backend. Web-API, kursuslogik, teams, point og scheduler deler modeller og migrationsdisciplin. Det betyder ikke, at alle processer får de samme rettigheder.

```text
Browserklienter: elev · lærer · separat projektor
                │ HTTPS / WebSocket
                ▼
Kontrolplan: Web + API ── PostgreSQL
                   │        ├─ durable jobs og leases
                   │        ├─ domæneevents/outbox
                   │        └─ scorehistorik
                   ▼
Worker/scheduler ── autentificeret nodeprotokol
                   ▼
Compute-node: afgrænset node-agent + terminalrelay
                   │
              libvirt / QEMU / KVM
                   │
     lab A: operator(er) ── målmaskine(r)
     lab B: operator(er) ── målmaskine(r)
                   │
        netværkssensor → afgrænsede events
```

Første fysiske vært er Ubuntu-serveren med Ryzen 5 7500F og 32 GB RAM. Node-agent/libvirt kører på værten, mens web/API/PostgreSQL/worker ligger i en særskilt betroet kontrol-VM. Lab-VM’er og kontrol-VM er søskende under KVM; der kræves ingen nested virtualisering. Kode og agenter arbejder under en uprivilegeret udviklingskonto på serveren; Mac er SSH-/editor-/browserklient. Kontrolplanet og udviklingskontoen er aldrig mål i missionerne. Se kapitel 02 for placering og dedikeringsgate.

## Foreslået kodeopdeling

```text
apps/web/                       React/TypeScript og skærmvisninger
services/control/               FastAPI + domænemoduler + jobworker
services/terminal-relay/         Uprivilegeret sessions- og byte-stream-relay
services/node-agent/             Lifecycle, inventory og afgrænset privilegeret adapter
packages/contracts/             Genererede klienttyper og eventskemaer
content/courses/                 Dage, læringsmål og forløb
content/missions/                Briefing, hinttrin, versioner og resource manifest
images/                         Byggeopskrifter; ikke færdige VM-diske i Git
infra/linux/                    Ubuntu-preflight, nodeinstallation og firewalltests
infra/control/                  Compose, reverse proxy og database
scripts/                        Samlede dev-/test-/driftskommandoer
tests/                          Integration, VM-smoke, isolation, E2E og load
```

Opret mapper, når der findes reel kode eller en task til dem. Undgå tomme services, som blot giver indtryk af en færdig arkitektur.

## API og terminal har forskellig livscyklus

FastAPI håndterer korte autoriserede API-kald og WebSocket-handshakes. En durable worker udfører langsomme operationer: imagekloning, boot, nulstilling, suspend og destroy. Brug ikke en web-request eller en ikke-persistent background task som eneste ejer af et VM-startjob.

Terminalruten er browser → autentificeret gateway/relay → node-lokal SSH-forbindelse med PTY → operator-VM. Gatewayen må kun kontakte en operator, som backend allerede har tildelt brugeren. Browseren angiver et session-ID, ikke vilkårlig værts-IP, SSH-bruger eller shellkommando på værten. xterm.js er terminalvisningen; FastAPI og AsyncSSH kan indgå i transporten. Kilder: S7–S10.

En separat proces for relay beskytter API-responstid mod terminalstrømme. På node-siden oprettes forbindelse fra en netværksafgrænset connector ind i det enkelte lab. En privilegeret helper kan forberede netværksnavnerummet, men byte-relay må ikke køre som generel root-shell. Begge komponenter skal kunne afvises ved forkert bruger, lab, organisation og generationsnummer.

## Minimal, holdbar jobmodel

PostgreSQL er første sandhedskilde. Implementér jobtabellen med idempotency key, status, lease owner, lease expiry, forsøg, næste forsøg og fejlkode. PostgreSQL dokumenterer `SKIP LOCKED` til blandt andet kølignende forbrugere; det er kun en mekanisme, ikke en færdig pålidelig kø. Vores recovery-, lease- og idempotenslogik skal stadig bygges og testes. Kilde: S11.

Forretningsændring, scoreændring og outbox-event skrives i samme transaktion. Dispatch er mindst én gang; forbrugere deduplikerer efter event-ID. Notifications vækker forbrugere, men en tabt notification må ikke betyde en tabt hændelse. Ved genforbindelse læses eventloggen fra en cursor.

Start uden Redis, Kafka eller NATS. Tilføj kun en broker ved dokumenteret behov; en ny broker må ikke blive en anden sandhedskilde for point eller missionstilstand.

## Node-agentens kontrakt

Kommandoer er begrænsede til registrerede handlinger såsom `prepare_image`, `create_lab`, `start_lab`, `stop_lab`, `reset_lab`, `destroy_lab` og `get_inventory`. Input indeholder versionsnummer, job-ID, lab-ID, organisation, ressourcekvote, generation og kendte image-referencer. Node-agenten accepterer ikke en vilkårlig shellstreng.

Kommunikationen sker over et managementnet med gensidig autentifikation og kryptering. Til serverudvikling kan en godkendt, afgrænset SSH-tunnel transportere forbindelsen; labgæster må aldrig kunne bruge den. Senere bruges samme nodekontrakt over et privat net/VPN med mTLS. En Tailscale-forbindelse kan være transport for administration, ikke en erstatning for lab-isolation.

Noden rapporterer faktisk inventar, heartbeat, ledig/reserveret RAM, diskplads, CPU-belastning, arkitektur og imagecache. Scheduler reserverer kapacitet atomisk før start. En node, der ikke melder tilbage, får ingen nye jobs; eksisterende sessioner markeres som utilgængelige, ikke som gennemført.

## Håndtering af modstridende tilstand

Desired state og observed state holdes adskilt. En periodisk reconciler sammenholder databasen med nodeinventar og retter kun projektejede objekter. Alle VM'er, diske og netværk mærkes med projekt-ID, organisation, lab-ID og generation. Automatisk oprydning må aldrig bruge globale prune- eller destroy-operationer.

Reset betyder ny generation: gamle terminalbilletter afvises, pointregler anvendes eksplicit, og sårbare diske genskabes fra en låst base. Stop/resume bevarer disktilstand. Gemte proces-/RAM-snapshots er valgfrie og ikke bærbare løfter mellem CPU-arkitekturer.

## Data, tilpasning og senere produkt

Organisation, klasse, kursus, team og lab er selvstændige begreber. Branding, sprog og fagindhold kommer fra konfiguration; skolen er første installation, ikke hårdkodet softwarelogik. OIDC kan tilføjes via identitetsadapter. Start med lokal single-organisation-installation og testede roller, ikke selvbetjent offentlig multi-tenancy.

En mulig Proxmox-adapter skal opfylde samme lifecycle-kontrakt, men live-migration og RAM-snapshots bliver ikke automatisk ens på tværs af leverandører. Udskiftelighed skal bevises ved tests, ikke blot ved navnet på en Python-interfaceklasse.
