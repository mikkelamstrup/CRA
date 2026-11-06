# 02 · Udvikling på Ubuntu-serveren

## Beslutning og kendt hardware
Hele udviklingsmiljøet samles på én fysisk Ubuntu-server. MacBook Pro M4 Pro med 24 GB RAM bruges som SSH-/editor-/browserklient; ingen lokal Lima-node eller ARM64-gate kræves. Serverforbindelse er nu en bevidst forudsætning for VM-tests. Kodeagenternes tjenester er udviklingsværktøjer, ikke runtime-afhængigheder for undervisningen.

Ejeren har oplyst følgende hardware; den er endnu ikke inspiceret af agenten:

| Komponent | Oplyst konfiguration |
|---|---|
| Bundkort | ASUS PRIME B650M-A II |
| CPU | AMD Ryzen 5 7500F |
| RAM | 2 × 16 GB Kingston Fury Beast DDR5-6000 |
| Disk | Kingston NV2 1 TB NVMe PCIe 4.0 |
| GPU | ASUS GeForce RTX 4060 8 GB Dual OC |
| Køler | Be Quiet Pure Rock 2 |
| OS | Ubuntu Server; version og kernel ukendt |

AMD dokumenterer 6 kerner/12 tråde, AMD-V og x86-64 for CPU'en. Det beviser ikke aktiv SVM i firmware, fungerende /dev/kvm eller faktisk gæsteboot. Kingston angiver NV2 som en klientdisk, ikke beregnet til servermiljøer; begynd med den eksisterende disk, mål fri plads/I/O og revurdér før klassedrift. Kilder: S30–S32.

## Flytning nu og dedikering før installation
Serveren kører endnu et par andre mindre projekter. Den kan ifølge ejeren dedikeres inden for få dage, men det er ikke sket eller verificeret her.

Planpakken kan kopieres til en ny, tom projektmappe nu; følg overførselsvejledningen. M0-01 må foretage læsende inventar, mens de andre projekter stadig kører. Der må ikke læses i deres kode, databaser, logs, miljøfiler eller credentials. Ejeren leverer en oversigt over berørte services, porte og dataplaceringer; yderligere inspektion af dem kræver aftalt scope.

**M0-02 må først begynde, når ejeren har bekræftet, at serveren er dedikeret, og godkendt den konkrete installations-/netværksplan.** Dedikering betyder, at de andre projekter er flyttet eller stoppet efter aftale, med verificeret backup/gendannelsesmulighed for data som skal bevares. En agent må ikke selv afinstallere services, formatere disk, geninstallere Ubuntu eller fjerne Docker-net for at gøre maskinen klar. Eksisterende SSH-adgang skal bevares.

## Placering på samme fysiske maskine

```text
Mac: SSH / fjerneditor / browser
             │ godkendt managementadgang
             ▼
Ubuntu-vært, AMD64
├── Udviklingsbruger: Git, Codex/Claude Code, worktrees, hurtige tests
├── Betroet kontrol-VM: web/API, PostgreSQL, worker og gateway
├── Node-agent + afgrænset privilegeret helper + libvirt/QEMU/KVM
├── Uprivilegeret terminalconnector med snæver adgang til eget lab
└── Isolerede labnet
    ├── Lab A: operator-VM + mål-VM
    └── Lab B / hold: egne operatorer + fælles mål
```

Kildekode og agenter ligger under en dedikeret, uprivilegeret udviklingsbruger. Kontoen får ikke generel sudo, Docker-socket eller ubegrænset libvirt-administration. Privilegerede handlinger udføres gennem konkrete godkendte procedurer og senere en begrænset helper. Eksisterende installation af Codex/Claude er oplyst af ejeren; versioner og rettigheder er ikke verificeret.

Kontrol-VM'en oprettes som betroet sikkerhedsgrænse i M0; web/API/database implementeres først i M1. Compose kan senere køre kontrolservices inde i denne VM. Kode kopieres eksplicit til kontrolmiljøets worktree; dependencies/services startes under uprivilegerede konti dér. Ingen automatiske hjemmemounts eller SSH-agent-forwarding. Udviklerrettigheder til kontrolmiljøet giver ikke adgang til værtsadministration.

Kontrol-VM og lab-VM'er er søskende under værtens KVM; der kræves ikke nested virtualisering. Ingen labkode må køre i kontrol-VM'en. Node-management er på et separat, afgrænset net; labnet får ingen NAT, fysisk bridge eller generel vej til management. Design den effektive filtrering og managementtransport i M0-02/M0-03, før sårbare services startes.

Udviklingsweb bindes til localhost eller den aftalte managementgrænse og tilgås fra Mac gennem en godkendt tunnel. Database og hypervisorporte eksponeres ikke bredt. Der er ingen forhåndsgodkendelse af offentlig adgang, nye SSH-/Tailscale-indstillinger eller globale firewallændringer.

## M0-01 · Inspicér uden at ændre
Kør `python3 -B scripts/doctor.py --json` på den tiltænkte Ubuntu-vært. Værktøjet læser kun faste systemmetadata og værktøjers tilstedeværelse på PATH. Det starter ikke Docker, libvirt eller VM'er. Rapporten omfatter OS, arkitektur, RAM/tilgængelig RAM, projektvolumens ledige disk, CPU-virtualiseringsflag og /dev/kvm-tilstedeværelse/adgang. Det er inventar, ikke accept af KVM eller isolation.

Et Linux-resultat beviser ikke, at processen kører direkte på den tilsigtede fysiske server. Bekræft vært kontra VM/container, Ubuntu-versionens support, installationernes faktiske versioner, firmware/SVM og ejerens dedikeringsstatus særskilt. Manglende læserettigheder giver UNKNOWN; giv ikke agenten bredere rettigheder blot for at få grønt output.

Lever et redigeret resultat, et ressource-/portbudget og en liste over nødvendige pakker, downloads, VM'er, netregler og tilbagerulning. Undgå serienumre, brugernavne, private stier, procesargumenter og hemmeligheder i rapporten.

## M0-02 · Versionslåst KVM og ren testgæst
Efter dedikeringsgaten vælges understøttet Ubuntu-/kernel-/QEMU-/libvirt-/firmwarekombination ud fra værtens faktiske installation og officielle versionsdokumenter. Bevar eksisterende Ubuntu, hvis det er understøttet og egnet; opgraderinger kræver særskilt plan. Dokumentér versionskombination og imagekilde/checksum.

Boot én ren AMD64-testgæst med faktisk KVM-acceleration. Test kommando, filskrivning, genstart og kontrolleret destroy; mål boot og ressourceforbrug. /dev/kvm og et svm-flag er ikke tilstrækkeligt bevis. Opret også den betroede kontrol-VM og verificér managementadgang og fravær af private hostmounts.

Hvis KVM ikke virker, stop ved præcis reproduktion og undersøg firmware, rettigheder, kernel og QEMU. Ingen stiltiende TCG-fallback og ingen nested-opsætning som genvej. Et alternativ kræver ny beslutning og samme isolationsgate.

## M0-03 · Et sikkert og nulstilleligt lab
Byg én lille operator og ét mål med syntetiske filer og kendt HTTP-/SSH-service. Bevis tilladt kommunikation samtidig med blokering mod vært, udviklingskonto, kontrol-VM, Mac, fysiske net og et andet lab. Brug kontrollerede testendpoints og positiv modkontrol: en slukket database eller timeout alene er ikke bevis for filtrering. Scan ikke skole-/hjemmenettet som test.

Kontrollér isolation med root i gæsten, ændret IP og IPv6 eller et verificeret fravalg af IPv6. Gentag reset tre gange. Rapportér, hvor filtrering håndhæves, og hvilke netværk, diske og VM'er der ryddes op. Ingen global prune eller ændring af andre services.

## Første ressourcebudget — ikke målinger

| Område | Indledende plan |
|---|---:|
| Betroet kontrol-VM | ca. 4 GiB |
| Én headless operator | ca. 1 GiB |
| Ét lille mål | ca. 0,75 GiB |
| Vært, udviklingsprocesser, QEMU-overhead og reserve | mål faktisk forbrug og memory pressure |

Start med ét lab, dernæst to og et lille holdlab. Budgettér ikke alle 32 GB til gæster, og brug ikke swap som forudsætning for kapacitet. Tyve sololabs med disse gæstetildelinger kræver alene 35 GiB. Hele klassen kræver senere måling og mulig RAM-opgradering eller flere noder.

Planlæg indledningsvis 60–100 GiB fri disk til images, overlays og builds og mål vækst. Kontroller fri plads før hver start og sæt grænser for overlays, logs og captures. 1 TB installeret disk er ikke 1 TB ledig disk.

## Grafik og værktøjskæde
RTX 4060 er ikke en første runtime-afhængighed. Platformens webgrafik renderes på browserklienten; en grafisk labdesktop prøves i M5 uden GPU-passthrough som baseline. GPU-deling/passthrough eller lokal AI kræver en separat beslutning, måling og sikkerhedsvurdering. Grafikkortets RAM er ikke gæste-RAM. Kilder: S33 og S15.

Fastlås understøttede Node-/Python-versioner ved M1 og test libvirt-driverkompatibilitet. Brug pnpm-lock.yaml, uv.lock og image-digests. Versioner i kildedokumenternes master/latest er ikke en lås. Ingen globale opgraderinger der påvirker andre projekter. Kilder: S22–S23.

## Senere udvidelse
AMD64 er første understøttede labarkitektur. ARM64/Mac-runtime er et valgfrit senere spor med egne builds og gates. M8 udvider serverdriften og kapaciteten; det er ikke en første migration fra Mac. Domænekode og manifests må ikke bindes til en personlig sti, IP eller bestemt server.
