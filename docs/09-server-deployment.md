# 09 · Fra udviklingsserver til klassedrift og flere noder

## Første server findes allerede i M0
Udvikling, kontrolmiljø og de første AMD64-labs placeres fra starten på Ryzen-/Ubuntu-serveren. Mac er klient. M0 håndterer læsende preflight, dedikering og faktisk KVM/isolation; M8 handler om driftsmodning og kapacitetsudvidelse, ikke første flytning fra Mac.

M8 kan forberedes efter M3, når behov og eventuel ekstra hardware er kendt. M9 kræver relevant M7-indhold og en målt kapacitetsprofil. En anden node er valgfri: hvis én opgraderet node består klassekravene, dokumenteres dette eksplicit, og multi-node-placement markeres NOT RUN/ikke anvendt i releasen. Funktionen må ikke kaldes testet uden to noder.

## Forudsætninger for ændret eller ny node
Indsaml CPU, kerner, RAM, disk, OS/kernel, virtualisering, netværk, administratorscope og andre arbejdsbelastninger uden at læse uvedkommende projektdata. Gentag preflight efter hardware-/OS-ændringer. Hvis en fremtidig node selv er en VM, skal nested virtualisering og ressourcegarantier afklares særskilt.

Ingen geninstallation, Proxmox-migration, formatering eller globale firewallændringer som automatisk standard. Ubuntu/libvirt er baseline. Ekstra noder må først få installationer efter aftalt scope; SSH-adgang alene er ikke tilladelse til ændringer.

## Udvidelsen trin for trin
1. M8-01: mål den eksisterende servers headroom og vælg klasseprofil: opgradering eller ekstra node efter målt behov. En lavere samtidighed kan bruges til udvikling/delpilot, men består ikke M9-gaten for 15–20 elever. Dokumentér backup, ændringsvindue og rollback.
2. M8-02: verificér låste AMD64-images og hele løsnings-/reset-/isolationssuiten på driftsprofilen. Samme arkitektur er ikke bevis for identisk adfærd på en ny CPU/kernel.
3. M8-03: hvis node 2 vælges, etabler afgrænset managementadgang, gennemfør preflight og godkendt installation, og registrér nodeidentitet, kapacitetslofter og imagecache. Gentag isolation på noden, før placement aktiveres.
4. Mål 1, 2, 5 og derefter flere labs, med startbegrænsning, ressourcekonkurrence og nodeudfald. M9 udfører den fulde klassetest.
5. M8-04: gør kontrol-VM'en til en versionslåst driftsinstallation med serviceopstart, adgangskontrol og dokumenteret backup/restore. Klassens login, terminal og scoring skal fungere uden Mac, SSH-devtunnel, Vite-devserver eller kodeagent.

Kontrolplanet kan blive i den eksisterende betroede VM på første server, hvis isolation og ressourcer består. Udviklings- og undervisningsinstallationer har separate databaser, konti, porte og releases; en igangværende klasse må ikke ændres af en agentbuild. En tredje maskine er ikke et krav. En kontrol-VM er en sikkerhedsgrænse, ikke automatisk høj tilgængelighed.

## Placering på to noder

Fordel hele labs, ikke brudstykker af samme lab, på tværs af noder. Et hold får sine operatorer og mål på én node. Så kræver multiplayer ikke et L2-netværk på tværs af serverne. Kun browsertrafik, management og begrænset telemetri bevæger sig mellem kontrolplan og noder.

Scheduler bruger deklarerede ressourcekrav, reserveret kapacitet og målt load. 64 GiB fordelt på to værter er ikke en fælles RAM-pulje. En VM og som standard et helt holdlab skal passe på en enkelt node. Nodefejl betyder nedetid for den nodes labs, indtil de genskabes; en ekstra node er ikke automatisk failoverkapacitet.

## Foreløbigt budget for en mulig senere 2 × 32 GiB-installation

Eksempel med ti lette sololabs pr. node:

| Ressource på én node | Budget |
|---|---:|
| 10 operatorer × 1 GiB | 10 GiB |
| 10 simple mål × 0,75 GiB | 7,5 GiB |
| Gæster i alt | 17,5 GiB |
| Host, QEMU/libvirt-overhead, node-agent og let sensor | indledende reserve ca. 4–6 GiB, skal måles |
| Eventuelt kontrolplan på denne node | ca. 2–4 GiB, skal måles |
| Tilbageværende plads | sikkerhedsmargin, caches og belastningsvariation |

Dette er en regnemodel, ikke en kapacitetsgaranti. Memorycommit på gæster er ikke hele værtsforbruget. GUI-værktøjer, flere mål, tunge databaser, pcap-optagelser og mange builds ændrer budgettet. Der bør ikke være afhængighed af aggressiv RAM-overcommit eller swap for at få undervisningen til at se mulig ud.

Ved fem hold à fire elever er antallet af operatorer stadig tyve, men nogle mål deles. Hold med flere mål kan forbruge lige så meget som solo. Stop sololabs kontrolleret, før holdmiljøerne allokeres; ellers opstår et stort midlertidigt RAM-spike.

CPU, SSD-I/O og samtidig opstart skal måles. To svage servere er ikke nødvendigvis bedre end én stærk med samme samlede RAM. Netværkskvalitet og administrationsarbejde er også en del af valget.

## Klargøring af en undervisningsdag

Vælg kursusdag, verificér alle nødvendige imageversioner i nodecache, kontrollér ledig disk/RAM og kør et smoke-lab. Forbered teams og test én elevlogin samt projektorvisningen. Prewarm efter den målte opstartsgrænse, ikke alle VM'er på én gang.

Under undervisningen vises teknisk labfejl særskilt fra elevens progression. Læreren kan nulstille et enkelt lab og genåbne dets terminal uden at genstarte hele installationen. Ved pause til næste undervisningsdag gemmes progression/noter og en aftalt VM-disktilstand.

## Backup, gendannelse og offline-drift

Sikkerhedskopiér database, kursusindhold, konfiguration og nødvendige nøgler med adgangskontrol. Base-images kan som udgangspunkt genskabes fra opskrifter, men undervisningsrelease bør have en kontrolleret cache. Beslut eksplicit om aktive VM-overlays skal sikkerhedskopieres; progression i databasen og elevens VM-filer er ikke samme datasæt.

En konsistent restore omfatter databaseversion, missionversioner og eventuelt tilhørende overlay-state. Test restore på en separat instans, før backupløsningen kaldes fungerende. Beskyt backup med samme hensyn til elevdata som live-systemet.

Forberedt undervisning skal kunne fungere uden internet: lokalt login, assets, briefing, hints, terminal og scoring. SSO eller andre eksterne tjenester må ikke indføres uden en plan for udfald. Opdateringer og nye imagebuilds planlægges uden for undervisningen.

## Hvad vi bevidst ikke bygger nu

Ingen Kubernetes, delt Ceph-lager, live-migration, tværgående VXLAN eller quorum-klynge blot for at bruge to noder. Det kan blive relevant senere, men den første nødvendige funktion er stabil placement af uafhængige labs på kendte maskiner.
