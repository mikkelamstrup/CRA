# CRA — Cyber Range Academy

Dansk undervisningsplatform med virkelige Linux/AMD64/KVM-labs. Elever arbejder
med browserterminal, valgfrit grafisk skrivebord, individuelle missioner og
holdøvelser. Lærerfladen omfatter opgavefrigivelse, hjælpekø, Command Center,
projektor og replay.

## Indhold

- `backend/`: Python/FastAPI-kontrolplan og PostgreSQL-migrationer.
- `frontend/`: React/TypeScript/Vite-brugerflade.
- `node/` og `infra/`: nodeagent og projektlokal libvirt/QEMU-infrastruktur.
- `content/`, `exercises/` og `docs/teacher/`: undervisningsindhold og lærermateriale.
- `scripts/`: udviklingsværktøjer, test og eksempler på drift.

Start med [arkitekturen](docs/01-architecture.md),
[sikkerhedsmodellen](docs/03-security-and-privacy.md) og
[publiceringsnotatet](PUBLICATION.md).

## Lokal udvikling

Linux x86_64, Python 3.12–3.14 og Node-versionen i `.node-version` kræves.
KVM kræves til egentlige VM-labs. Pakkeversioner og værktøjer er låst i projektet.

```bash
python3 scripts/check_pack.py
python3 scripts/cra.py doctor
python3 scripts/cra.py setup
python3 scripts/cra.py build
```

`setup` henter de låste værktøjer og afhængigheder. Kontrol-VM, noder, netværk og
adgangsoplysninger skal etableres særskilt i dit eget isolerede miljø. Kommandoer
til provisionering og deployment må først bruges efter tilpasning og review af
din lokale opsætning. Denne kildeudgivelse er ikke en færdig installationspakke.

## Status og rettigheder

Udgivelsen er et renset kildeøjebliksbillede. Driftsdata, konti, hemmeligheder og
den tidligere private udviklingshistorik følger ikke med. Historiske
testbeskrivelser i materialet er ikke en garanti for et nyt miljø. Fuld KVM-,
database-, kapacitets- og elevpilotaccept af denne offentlige kopi er ikke udført.

Repositoryet indeholder også bevidst sårbare øvelser, syntetiske øvelseslogins og
lærerfacit. Kør dem kun i de beskrevne isolerede undervisningsmiljøer.

Der er endnu ikke valgt en open source-licens. At koden kan læses offentligt er
ikke i sig selv en tilladelse til fri viderebrug. Tredjepartsafhængigheder beholder
deres respektive licenser.
