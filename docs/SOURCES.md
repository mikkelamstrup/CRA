# Kilder og evidensgrænser

Kontrolleret som grundlag for planen den 9. oktober 2026. Kilderne er primærkilder fra produktprojekter, dokumentationsudgivere og myndigheder. Designvalg, ressourcebudgetter, milestones og performancegrænser er **forslag i denne plan**, ikke målinger udført af kildeudgiverne.

Dokumentation under `latest`, `current` eller `master` kan ændre sig. Implementeringen skal vælge understøttede versioner og fastlåse den kombination, som faktisk bliver testet. Officiel understøttelse er ikke lig lokal verifikation.

S1–S6 er historiske kilder til den tidligere Mac-/fallback-plan. De er ikke aktive installationskrav efter ADR-002/PLAN-01. Den nye serverbaseline bruger især S12 og S30–S33.

Henvisninger til S16 omfatter S16a–b; S19 omfatter S19a–b.

## S1 · Apple · nested virtualisering

[Apple · nested virtualisering](https://developer.apple.com/documentation/virtualization/vzgenericplatformconfiguration/isnestedvirtualizationsupported)

Understøttelse for M3 og nyere; ikke bevis for den konkrete Mac-/Linux-installation.

## S2 · Lima · konfigurationsreference

[Lima · konfigurationsreference](https://raw.githubusercontent.com/lima-vm/lima/master/templates/default.yaml)

nestedVirtualization, CPU-mode, mounts og forwarding. Master er en bevægelig reference; pin en version ved implementering.

## S3 · Lima · startkommando

[Lima · startkommando](https://lima-vm.io/docs/reference/limactl_start/)

CLI-flags som nested-virt, mount-none og plain; effektive standarder skal gennemgås.

## S4 · Lima · Intel/ARM

[Lima · Intel/ARM](https://lima-vm.io/docs/config/multi-arch/)

Native arkitektur versus fuld fremmed-arkitektur-emulering.

## S5 · UTM · QEMU-netværk

[UTM · QEMU-netværk](https://docs.getutm.app/settings-qemu/devices/network/network/)

Netværkstyper og host-only-begrænsninger for en mulig fallback.

## S6 · Docker · sikkerhed

[Docker · sikkerhed](https://docs.docker.com/engine/security/)

Containernes namespaces, privileges og isolation; ikke fuld egen gæstekernel.

## S7 · xterm.js · sikkerhed

[xterm.js · sikkerhed](https://xtermjs.org/docs/guides/security/)

Sikker terminalintegration og advarsel mod at bruge demo-gatewayen direkte.

## S8 · xterm.js · flow control

[xterm.js · flow control](https://xtermjs.org/docs/guides/flowcontrol/)

Backpressure og bufferproblemer, især med WebSocket-transport.

## S9 · FastAPI · WebSockets

[FastAPI · WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)

WebSocket-understøttelse; ikke automatisk sikker sessions- eller jobmodel.

## S10 · AsyncSSH · API

[AsyncSSH · API](https://asyncssh.readthedocs.io/en/latest/api.html)

SSH-sessioner, PTY, host-key-validering og tilknyttede parametre.

## S11 · PostgreSQL · SELECT / locking

[PostgreSQL · SELECT / locking](https://www.postgresql.org/docs/current/sql-select.html)

SKIP LOCKED til kølignende forbrugere; øvrig jobpålidelighed er applikationsdesign.

## S12 · libvirt · virtuelle netværk

[libvirt · virtuelle netværk](https://wiki.libvirt.org/VirtualNetworking.html)

Isolerede netværk kan stadig tale med værten; yderligere værtsbeskyttelse kræves.

## S13 · Zeek · conn.log

[Zeek · conn.log](https://docs.zeek.org/en/current/reference/logs/conn.html)

Forbindelsesmetadata og protokol-/trafikobservationer.

## S14 · Suricata · EVE JSON

[Suricata · EVE JSON](https://docs.suricata.io/en/latest/output/eve/eve-json-output.html)

Strukturerede sensorhændelser. latest kan være udviklingsdokumentation; vælg stabil release til drift.

## S15 · Apache Guacamole · manual

[Apache Guacamole · manual](https://guacamole.apache.org/doc/gug/)

Browserbaseret adgang til grafiske fjernmiljøer; integration og isolation skal stadig designes.

## S16a · OpenAI Codex · AGENTS.md

[OpenAI Codex · AGENTS.md](https://developers.openai.com/codex/agent-configuration/agents-md)

Projektinstruktioner til Codex.

## S16b · OpenAI Codex · sandbox og approvals

[OpenAI Codex · sandbox og approvals](https://developers.openai.com/codex/agent-approvals-security)

Forskellen på eksekveringsgrænser og godkendelsespolitik.

## S17 · Claude Code · memory og projektinstruktioner

[Claude Code · memory og projektinstruktioner](https://code.claude.com/docs/en/memory)

CLAUDE.md/AGENTS.md, importer og fokuseret projektkontekst.

## S18 · Claude Code · permissions

[Claude Code · permissions](https://code.claude.com/docs/en/permissions)

Rettighedskonfiguration og godkendelser.

## S19a · Claude Code · worktrees

[Claude Code · worktrees](https://code.claude.com/docs/en/worktrees)

Isolerede parallelle arbejdsmapper.

## S19b · OpenAI Codex · worktrees

[OpenAI Codex · worktrees](https://developers.openai.com/codex/environments/git-worktrees)

Parallelle uafhængige forløb i separate worktrees.

## S20 · Datatilsynet · skoler og daginstitutioner

[Datatilsynet · skoler og daginstitutioner](https://www.datatilsynet.dk/regler-og-vejledning/skoler-og-daginstitutioner)

Myndighedens indgang til skolers behandling af personoplysninger. Konkrete valg afklares med skolen.

## S21 · Playwright · test projects

[Playwright · test projects](https://playwright.dev/docs/test-projects)

Testkonfigurationer og browserprojekter.

## S22 · Node.js · releaseoversigt

[Node.js · releaseoversigt](https://nodejs.org/en/about/previous-releases)

Understøttede LTS-linjer; Node 24 LTS er foreslået baseline, ikke en permanent versiongaranti.

## S23 · uv · locking og syncing

[uv · locking og syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

Lockfiles og reproducerbar dependencyinstallation.

## S24 · Choose a License · ingen licens

[Choose a License · ingen licens](https://choosealicense.com/no-permission/)

Et repository uden licens giver ikke automatisk normale open-source-rettigheder.

## S25 · Open Source Initiative · definition

[Open Source Initiative · definition](https://opensource.org/osd)

Open source udelukker ikke kommerciel anvendelse.

## S26 · AsyncSSH · upstream og licens

[AsyncSSH · upstream og licens](https://github.com/ronf/asyncssh)

Den konkrete upstream-licensangivelse skal registreres og vurderes.

## S27 · Kulturministeriet · computerspil og edb-programmer

[Kulturministeriet · computerspil og edb-programmer](https://kum.dk/kulturomraader/ophavsret/computerspil-og-edb-programmer)

Generel myndighedsinformation om software og ophavsret; ikke en afgørelse af projektets ejerskab.

## S28 · CTFd · overblik

[CTFd · overblik](https://docs.ctfd.io/docs/overview/)

Eksisterende platform for CTF-udfordringer; en mulig senere integration.

## S29 · Colima · upstream

[Colima · upstream](https://github.com/abiosoft/colima)

Historisk Mac-mulighed. Colima er ikke et installationskrav i Ubuntu-serverprofilen.

## S30 · AMD · Ryzen 5 7500F

[AMD · Ryzen 5 7500F](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-5-7500f.html)

6 kerner/12 tråde, x86-64 og AMD-V. Producentdata, ikke bevis for aktiv SVM eller KVM på den konkrete server. Kontrolleret ved planændringen 2026-10-09.

## S31 · KVM · forudsætninger

[KVM · FAQ](https://www.linux-kvm.org/page/FAQ)

Linux og CPU-virtualiseringsudvidelser som AMD-V/SVM. Faktisk boot og adgang skal testes; distro-/værktøjsversioner vælges ved installation.

## S32 · Kingston · NV2

[Kingston · NV2-datablad](https://www.kingston.com/datasheets/snv2s_en.pdf)

NVMe-kapaciteter og producentens afgrænsning til klientarbejdsbelastning. Ikke en måling af VM-I/O eller holdbarhed i dette projekt.

## S33 · MDN · WebGL

[MDN · WebGL](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API)

Webgrafik renderes i browseren med klientens grafikacceleration; et grafikkort i backendserveren accelererer ikke automatisk elevbrowseren.
