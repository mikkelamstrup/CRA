# Offentlig kildeudgivelse

Denne kopi begynder med en ny Git-historik. Den oprindelige udviklingshistorik,
interne overdragelser, driftsjournaler og lokale installationsoplysninger er
udeladt. Den aktive installation og dens data ændres ikke af publiceringen.

Virkelige serveradresser, personlige hjemmestier og den aktive Nordlys-adresse
er erstattet med eksempelværdier. `crauser`, `node2-example`, `100.64.0.2` og
domæner under `.invalid` er eksempler. Standardadresser for QEMU og isolerede
labnet samt syntetiske testdata er bevaret. Driftsværktøjer skal tilpasses før brug.

Der medfølger ingen elevkonti, loginoversigter, runtime-databaser, private nøgler,
tokens, miljøfiler, netværksoptagelser, VM-diske, screenshots eller backups.
Et eksisterende runtime-miljø kan ikke gendannes ud fra dette repository alene.

Offentliggørelse af fremtidige ændringer kræver gennemgang af hele den nye diff
og ny secretscanning. En `.gitignore` beskytter ikke mod hemmeligheder, der er
skrevet direkte i kode eller allerede indgår i commits. Brug en særskilt offentlig
arbejdskopi; send aldrig den interne historik med `--mirror` eller `--all`.
