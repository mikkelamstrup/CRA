# Dag 2 · Network Recon / Ghost Network

Syv lektioner. Forudsætning: dag1 og gemte noter. Brug den eksisterende mission Første kontakt. Læreren kan frigive individuelt eller sætte sessionen til dag2; det ændrer ikke elevens eksisterende VM. Et opgaveskift kræver elevens normale, tydelige valg om at fjerne det gamle miljø. Gem filer, der skal bruges senere, før dette valg.

## Tavle og demonstration

Tegn operator, lab0, mål-IP og en service på port8080. Skriv forskellen mellem IP, port og protokol. Vis `ip -br address`, en afgrænset `nmap -sT -p 22,8080 MÅL-IP` og `curl -i http://MÅL-IP:8080/`. Forklar at navnet ved en port ikke i sig selv identificerer programmet. Vis Ctrl-C. Alle eksempler bruger den viste labadresse, aldrig skolens net.

## Solo, hints og facit

Eleven undersøger sin mål-IP, finder TCP8080 åben, henter HTTP-forsiden og afleverer det dynamiske flag derfra. Hint1: skeln mellem mål og operator. Hint2: angiv to porte med komma. Hint3: curl kræver protokol, adresse og port. Der er intet fælles facitflag. Forvent en kilde med kommando, mål, port, tidspunkt og afgrænset konklusion.

## Ghost Network og overgang

Lektion5: eleverne gemmer notesbogen og stopper deres sololabs. Opret et hold på2–4 i Mit hold, og vælg Første kontakt. Hver elev åbner sin egen operator mod det fælles mål. Der er forskellige mål-IP’er pr. operatorlink; det er forventet. Fordel kortlægger, serviceundersøger, dokumentarist og efterprøver. Byt efter15minutter. Fælles flag giver holdpoint én gang; noter og forklaringer vurderes særskilt. Ved afslutning stoppes holdlabbet, før solos genstartes.

## Misforståelser og vurdering

En åben SSH-port er ikke bevis for et login. HTTP200 siger ikke at alle sider er tilgængelige. En bannertekst kan være misvisende. Flere pakker giver ikke automatisk flere point eller bedre forståelse. Bed alle forklare en kommando og en usikkerhed. Bonus: sammenlign forkert port og korrekt port uden at udvide scope.

## Debrief

Brug de tre refleksionsspørgsmål i elevmaterialet. Afslut med holdets mål/port/service-kort, en efterprøvet påstand og en uafklaret ting. Eksportér teorien og de gemte noter som tekst. Tillad gratis hints, og planlæg flere minutter til faktisk VM-opstart.
