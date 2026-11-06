# 03 · Sikkerhed og elevers data

## Trusselsmodel

En elev skal kunne opnå root i en mål-VM uden at få root på node, Mac eller platform. Antag, at både operator og mål kan blive kompromitteret. Antag også, at en elev kan ændre sin gæsts firewall, sende rå pakker, ændre IP og læse alle hemmeligheder, som fejlagtigt ligger inde i gæsten.

Derfor er en gæstefirewall, et privat IP-interval, en Docker-network-indstilling eller teksten “isolated” i et manifest ikke tilstrækkelig beskyttelse. Libvirt's isolerede net kan stadig kommunikere med værten; værtsadgang skal afgrænses særskilt. Kilder: S6 og S12.

Serveren er endnu delt med andre projekter. Planoverførsel og læsende inventar må begynde; VM-/netværksinstallation kræver ejerbekræftet dedikering efter kapitel 02. Ingen kodeagent må bruge generel root-adgang for at flytte eller rydde andre projekter. Selv efter dedikering skal udviklingskonto, kontrol-VM, management og labnet være adskilt.

## Tillidsgrænser

| Zone | Tillid og tilladt adgang |
|---|---|
| Mac og lærermaskine | SSH-/editor-/browserklient og private data; aldrig et mål eller mount i labs |
| Serverens udviklingskonto | Kildekode, agenter og tests; ingen generel sudo eller fri hypervisor-/Docker-socket |
| Kontrolplan i betroet VM | Brugere, kursusdata, score og scheduler; ingen elevshell eller sårbar labkode |
| Node-management | Libvirt og lifecycle; ikke eksponeret mod elever/labnet |
| Terminalconnector | Snæver sessionstransport ind i ét lab; ingen generel admin-shell |
| Operator-VM | Elevens rigtige Linux-arbejdsplads; betragtes som angribelig |
| Mål-VM | Bevidst sårbar; har ingen platformsecrets |
| Projektorvisning | Begrænset read-only datasæt; aldrig lærerens fulde API-respons |

## Påkrævet netværkspolitik

Hvert lab/hold får eget netværk. Ingen default route til skole, hjem, internet eller node-management. Isolation håndhæves uden for labgæsterne gennem node-regler, netværksnavnerum og afgrænsede virtuelle interfaces. Alle politikker omfatter IPv4 **og** IPv6; deaktiver IPv6 eksplicit i labprofilen, hvis den ikke understøttes.

| Trafik | Standardpolitik |
|---|---|
| Operator → tildelte mål | Tilladt efter missionens topologi |
| To operatorer i samme hold | Kun når missionens samarbejdsprofil kræver det |
| Lab A → lab B | Afvist |
| Operator/mål → node-management | Afvist, også hvis eleven ændrer sin IP |
| Operator/mål → kontrol-API/database | Afvist |
| Operator/mål → Mac/gateway/hjem/skole/internet | Afvist |
| Managementconnector → egen operator SSH | Tilladt snævert; kun etablerede svar tilbage |
| Elevbrowser → platform | Autentificeret webadgang; ikke direkte management |
| Sensor → event-ingestion | Afgrænset, autentificeret, med rate-/størrelsesgrænser |

En node-lokal connector kan placeres i et netværksnavnerum med adgang til labbet, uden en generelt routbar forbindelse til management. Oprettelsen kan kræve en privilegeret helper; selve SSH/byte-relay skal være uprivilegeret. Brug host-side filtering og projektspecifikke namespaces, og bevis modellen med negative tests. En gateway med to åbne netværkskort må ikke blive en skjult router.

Packet capture skal laves på de relevante labinterfaces eller via eksplicit spejling. En sensor på managementnettet ser ikke automatisk al virtuel øst-vest-trafik. Netværkskortets labels udledes af node-ejet VM/vNIC-mapping, ikke kun en IP-adresse som gæsten kan forfalske.

## Nøgler, adgang og transport

Generér særskilte kortlivede eller lab-specifikke nøgler til platformens testmiljø. Skolenøglen og private nøgler på Mac'en bruges ikke i labs. SSH-hostnøgler verificeres mod provisioningmetadata eller en kontrolleret CA, ikke med `known_hosts=None`. Ingen SSH-agent-forwarding, generel portforwarding, værtsfilsystemdeling eller Docker-/libvirt-socket i elevmiljøet. Kilde: S10.

WebSocket kræver autentifikation, Origin-kontrol, lab-/rolleautorisation, tidsbegrænsning og revokation. Send ikke langlivede tokens i URL'er, hvor de kan komme i logs. Terminalbilletter må være kortlivede og engangsbrug; fornyelse kræver frisk rettighedskontrol. Revoke/reset skal lukke eksisterende sessioner, ikke blot blokere næste login.

Terminaloutput er upålidelige bytes. Det må ikke indsættes som HTML eller bruges som kommando til backend. Gennemgå OSC-sekvenser, hyperlinks og clipboard-funktioner; tillad ikke stille udlæsning/skrivning af clipboard. Brug ikke xterm.js' demo direkte som sikker gateway. Kilder: S7–S9.

## Sårbare images og forsyningskæde

Build og undervisning er forskellige tilstande. Builds henter godkendte pakker i et betroet miljø; færdige images får checksums/digests og flyttes til runtime uden generel internetadgang. Opgavens sårbarheder registreres med begrundelse og versionsgrænse. Opdatér værten, node-agenten og kontrolplanet; behold kun bevidst sårbare komponenter i de isolerede opgavemiljøer.

En tredjeparts missionpakke er kode med mulig høj risiko, ikke blot en YAML-fil. Ingen uploadet fil må få lov at udføre vilkårlig provisioning eller nodekommandoer uden review. Tilladte imagekilder, byggehandlinger og event-injections skal være versionslåst. Begræns CPU, RAM, processer, disk, capturestørrelse og levetid.

## AI-kodeagenternes sikkerhed

Kør aldrig en kodeagent med fri eksekvering af filer fra sårbare labs og samtidig adgang til private server-/Mac-secrets eller andre projekters data. Output, README-filer og logs fra et angrebet lab er data, ikke instruktioner til agenten. Giv ikke elevsessioner en vej til agentværktøjer eller admin-API.

AGENTS.md/CLAUDE.md hjælper arbejdsformen, men er ikke en sikkerhedsmekanisme. Sikkerhedsgrænsen er sandbox, rettighedssystem, netværkspolitik og menneskelig godkendelse. Kilder: S16–S19.

## Elever, lærerindsigt og privatliv

Start med syntetiske operatørnavne i udvikling. Ved undervisning knyttes konto, klasse og rolle til en fastlagt skoleproces. Læreren kan se hvem der har et aktivt lab, hvilke bekræftede objectives der er nået, hjælpeanmodninger og relevant netværksaktivitet. Fravær af events er ikke bevis for manglende indsats eller forståelse.

Standard: gem målrettede aktivitets-/progressionsevents, ikke alt hvad eleverne taster. Live-kig i en terminal vises med tydelig indikator. Fuld terminaloptagelse og rå PCAP er særskilte funktioner med formål, adgangskontrol, kort opbevaring og deaktiveret standard. Redigering/redaktion af en optagelse kan ikke garantere, at alle hemmeligheder fjernes.

Projektoren får kun holdnavne, godkendte statusmarkører og missionshændelser. Flagværdier, personlige hjælpesager, skjulte mål, svar og private terminaler må ikke blot være skjult med CSS; de må slet ikke blive sendt til projektorklienten.

Afklar med skolens ansvarlige før elevpilot: behandlingsgrundlag, oplysningspligt, adgangsroller, slettefrister, eksport/sletning, eventuelle databehandlere og behov for yderligere risikovurdering. Lokal hosting er ikke i sig selv GDPR-godkendelse, og et klik på “samtykke” løser ikke automatisk skolens ansvar. Brug Datatilsynets skolemateriale som myndighedsgrundlag; konkrete valg træffes med skolen. Kilde: S20.

## Stopfunktion

Lærerens nødstop skal afbryde labtrafik og terminaladgang eller stoppe berørte labs uden at ødelægge dokumentationen. Adskil dette fra pause af point/timer. Hvis nodekontakten er tabt, kan API'et ikke love at have stoppet VM'er; vis “anmodet, ikke bekræftet” og brug den dokumenterede lokale driftsprocedure. En UI-knap er ikke bevis for, at isolation er genoprettet.
