# Dag 3 · Web Inspector / Service Desk

Syv lektioner. Ny mission: Web Inspector · Navnet bag servicen (`web-trail-dns` v1). Den gamle Websporet-mission og dens gemte labs forbliver uændrede. DNS findes kun i den nye mission. Frigiv derfor den nye opgave; kræv ikke reset af et gammelt elevlab for at fortsætte dets gamle opgave.

## Tavle og demonstration

Tegn kæden navn → DNS-svar → IP:port → HTTP-sti → indhold. Forklar A-record, forespørgslens server, NXDOMAIN og timeout. Vis `dig @MÅL-IP rapport.nordlys.test A` og samme spørgsmål med `+tcp`. DNS kører kun på målmaskinens labinterfaces og har ingen upstreamserver. Vis bagefter curl og HTTP-headers. `--resolve` er curl-konfiguration, ikke et nyt DNS-opslag.

## Solo og facit

1. `curl -i http://MÅL-IP:8080/` giver serviceregisterets introduktion.
2. `dig @MÅL-IP rapport.nordlys.test A` returnerer elevens egen mål-IP. `mangler.nordlys.test` giver NXDOMAIN. UDP og TCP kan begge bruges.
3. `curl --resolve rapport.nordlys.test:8080:MÅL-IP -i http://rapport.nordlys.test:8080/service/` viser henvisningen til rapportNL-308.
4. Hent `/rapporter/rapport-308.txt` på samme service, og aflever dens dynamiske flag. Rapport307 er en tidligere sag uden gyldigt flag. `/findes-ikke` giver404.

Hints går fra hvilken information der mangler, til valg af DNS-server, til forskellen mellem domæne og URL, og til konkret rapportsti. Hints er gratis. Det er tilladt at finde rapporten på anden måde; forklaringen om DNS og HTTP vurderes stadig af læreren.

## Service Desk

Gem og stop solos, opret et fælles holdlab med Web Inspector, og fordel DNS, HTTP, dokumentation og efterprøvning. Alle bruger deres egen mål-IP, selv om servicen er fælles. Byt roller efter15minutter. Aflever en supportbesked med reproduktion, observerede svar og begrænsninger. Stop holdet inden genstart af solos. Notesbøger og tidligere point består.

## Typiske fejl og debrief

At skrive MÅL-IP bogstaveligt; at inkludere `http://` i et DNS-navn; at bruge cd til en URL; at tolke timeout som NXDOMAIN; at tro at DNS-svar er websidens indhold. Vis et eksempel på hver uden at udvide scope. Spørg om DNS kan virke med en stoppet webservice, hvorfor --resolve ikke er DNS-bevis, og hvilket negativt svar hjalp mest. Bonus: HEAD versus GET og robots.txt versus adgangskontrol.
