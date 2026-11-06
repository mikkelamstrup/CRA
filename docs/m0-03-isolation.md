# M0-03 · Målelig isolation på den delte vært

Status: faktisk accept og uafhængigt review PASS. 33 VM-/trafik-/reset-/cleanupchecks består med begge procesfiltre aktive. Tre resets: 50,33 / 50,23 / 50,18 sekunder. 27 unit/kerneltests består.

## Den afprøvede grænse

Hver operator/mål-pair får en separat QEMU TCP-tunnel på loopback. Tunnelen transporterer Ethernet mellem de to virtuelle NIC'er; der oprettes ingen hostbridge, tap, NAT, host-interface, fysisk uplink eller globale firewallregler. Par A og B bruger hver sin port og kan ikke forbinde deres L2 via gæstens IP-konfiguration. Begge IP-familier kan bruges **inden for** det tildelte lab.

Management er et separat QEMU userspace-net med `restrict=on,ipv6=off`. Det eneste eksplicitte forward er host-loopback → gæste-SSH. Gæsten får ikke værtsforwarding, credentials til andre gæster, libvirt-/Docker-sockets eller hostmounts. Testen forsøger at tilføje default route og B-adresser med root i både operator og mål. Kendte host-TCP-/UDP-endpoints kontrolleres positivt fra den betroede kontrol-VM, før deres afvisning fra labs registreres. Kontrol-VM'ens fungerende management-SSH bruges også som forbudt endpoint.

Der scannes ikke på hjemmenet/skolenet eller mod Mac'en. Fraværet af en anden virtuel/ fysisk uplink sammen med den testede afvisning ved eneste management-gateway er argumentet for blokering af disse veje. Dette er ikke en påstand om en separat pakketest til brugerens Mac eller en skolemaskine.

## QEMU som værtproces

Før QEMU exec håndhæver wrapperen Landlock med `no_new_privs`. ABI 6 eller nyere er påkrævet; værten har ABI 8. Manglende kernelunderstøttelse giver fejl, ikke fallback. Hver proces får læseadgang til egne runtimebiblioteker, nødvendige systembiblioteker/kernelmetadata, sin egen seed og imagebaser samt skriveadgang til sin egen overlay. Udviklingsfiler, søskende-VM'ers filer og SSH-klientnøglen er ikke tilladt. Libvirts egen master-key til samme domæne tillades som én read-only fil; capability-probes får kun deres konkrete midlertidige qmp-mappe.

TCP-bind begrænses til tildelte porte, og lab-QEMU må kun forbinde til sit eget tunnel-portnummer. Signaler og abstrakte Unix-sockets scopes af Landlock. QEMU-miljøet erstattes af en kort whitelist, så udviklingsprocessens miljøhemmeligheder ikke arves. Libvirts eksisterende QEMU-seccomp er stadig aktiv. Kontrol-VM må som eneste profil få egress til betroet dependency-installation; den får ingen lab-NIC.

Landlock begrænser fremtidig filadgang, ikke allerede overdragne filbeskrivelser. Libvirt må derfor kun overdrage dette domænes nødvendige disk-/monitor-/logbeskrivelser. Kernel- eller hypervisorfejl er en resterende risiko; dette er ikke en formel sikkerhedsverifikation. ABI 8 regulerer ikke UDP via Landlock, så lab-UDP blokeres af QEMU userspace-nettet og testes særskilt.

Review fandt, at Landlock ABI 8 heller ikke håndhæver pathname Unix-sockets. Derfor lægges et ekstra libseccomp-filter på domæneprocessen før exec: nye AF_UNIX-sockets, socketpairs, io_uring_setup og pidfd_getfd afvises. Den 32-bit address-family sammenlignes med maskering, så øvre syscall-registerbits ikke omgår kontrollen. Filteret bruger native arkitektur og bevarer allerede tildelte monitorforbindelser; libvirts egne capability-probes uden gæst må fortsat oprette deres lokale QMP-socket. Ingen elevgæst køres i probeprofilen. Et faktisk kernelregressionstjek beviser, at en syntetisk pathname-socket er åben før policy og afvist efter policy, mens tildelt TCP og en arvet monitorforbindelse stadig virker.

Tre faktiske kerneltests bruger syntetiske filer og kontrollerede sockets. De beviser tilladt fil-/TCP-adgang samtidig med afviste andre filer, skrivning, TCP-port og signal til parent. Den konkrete QEMU-policy testes desuden for egen overlay/seed kontra klientnøgle, søskende-seed og udviklingsmappe. Ingen virkelige private nøglebytes læses eller udskrives af disse tests.

## Værktøjer, baseline og reset

Seks Noble-pakker til nmap 7.94 hentes i den betroede kontrol-VM fra APT-metadata og kontrolleres med SHA-256. `infra/lab-tools.lock.json` fryser versioner/hashes. `lab_tools.py` kopierer dem til operatoren og installerer offline; labbet får aldrig internet som en installationsgenvej. HTTP-endpointet er Pythons virkelige standardserver, og guest-to-guest SSH bruger en særskilt nøgle genereret inde i operatoren og en verificeret target-hostnøgle.

Canonical-basen hashes før hver oprettelse og er read-only for QEMU. Hver gæst får en ny 12 GiB sparse overlay og nye nøgler. Reset stopper og undefinerer kun ejermærkede domæner, fjerner kun deres mapper og laver nye overlays fra den låste base. Testen ændrer indhold, stopper services og skriver i begge gæster før tre resets. Baseline-service/indhold og fravær af de skrevne filer kontrolleres efter hvert reset. `nmap` er testværktøj i første par; installationskommandoen er reproducerbar fra låsen. Fuld missionsimage-versionering til produktflow følger i M2.

## Kørsel

Sæt `CRA_NODE_HOME` til den godkendte M0-02-node-checkout. Kun én lifecycle-writer må bruge nodehjemmet. Kontrollens egress skal være aktiveret for download/positive hostprobes; `runtime.control_egress(True)` accepterer kun den betroede `cra-control` og genstarter den. Kør ikke dette på et lab med ubevarede elevdata.

```bash
python3 infra/linux/lab_tools.py
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
python3 -u infra/linux/smoke_m0_03.py
```

Accepttesten opretter `cra-a-operator`, `cra-a-target`, `cra-b-operator`, `cra-b-target`; den stopper og fjerner dem ved afslutning og kontrollerer domæner, mapper og listener-porte. Kontrollen bevares. Rapport: `reports/local/m0-03/isolation.json`. Ressourcetildeling under testen: kontrol 4 GiB, to operatorer à 1 GiB og to mål à 768 MiB. Det er 7,5 GiB gæste-RAM, ikke en klasselastmåling.

## Procesaudit under den faktiske VM-test

`audit_qemu.py` kontrollerede kontrol-VM og alle fire lab-QEMU-processer efter seccomp-rettelsen. Alle bestod: fil-/Unix-deskriptorer var egne disk/seed/base/PID/kernelressourcer eller eget domænes monitor; base og seed var åbnet read-only. Ingen descriptor til klientnøgler, udviklingsfiler eller den generelle libvirt-socket blev fundet. Alle fem havde capabilities 0, `NoNewPrivs=1`, mindst to seccomp-filtre og præcis miljø-whitelisten. Netværkssockets prøves af den separate positive/negative trafiktest; auditten er et øjebliksbillede af disse konkrete processer.

```bash
python3 infra/linux/audit_qemu.py cra-control cra-a-operator cra-a-target cra-b-operator cra-b-target
```

Køres mens testgæsterne findes, og uden en samtidig reset. Rapporten indeholder kun status og descriptor-kategorier, ikke filindhold, nøgler eller fremmede filstier.

## Officielle kilder

[libseccomp API](https://github.com/seccomp/libseccomp/tree/main/doc/man/man3), [Linux Landlock API](https://docs.kernel.org/userspace-api/landlock.html), [libvirt TCP-tunnelinterfaces](https://libvirt.org/formatdomain.html#tcp-tunnel), [QEMU user networking/restrict](https://www.qemu.org/docs/master/system/invocation.html).
