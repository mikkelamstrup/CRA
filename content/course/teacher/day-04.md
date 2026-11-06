# Dag 4 · Access Granted / Broken Access

Syv lektioner. Ny mission `access-granted` v1. Forudsætning: filsystem og IP/port. Login i CRA og login i mål-VM’en er forskellige sikkerhedsgrænser. Opgaven kræver ikke exploitation eller adgang til skolen.

## Tavle og demonstration

Tegn klientnøgle, værtsnøgle, SSH-forbindelse og brugerens grupper. Brug `ssh -F /home/cra/academy/access/ssh_config nordlys`; den låste konfiguration vælger mål-IP, port2222, analyst og den separate øvelsesnøgle. Deaktivér aldrig værtstjek. Vis `id`, ejer/gruppe/andre og r/w/x. På mapper betyder x gennemgang; en læsbar fil kan stadig være utilgængelig gennem en lukket mappe.

## Solo og facit

Eleven læser README, logger ind som analyst og ser medlemskab af fieldops. `/srv/cra-access/reports` er root:fieldops0750; rapport404 er root:fieldops0640 og indeholder det gyldige dynamiske flag. `/srv/cra-access/admin` er root0700 og skal afvise analyst. Analyst må ikke bruge sudo. CRA’s managementkonto og nøgler indgår ikke i opgaven.

Efter `exit` laver eleven en fil på egen operator med printf, sætter den til000, observerer Permission denied og retter den til600 som filens ejer. Dette er den kontrollerede adgangsfejl. Ret kun denne fil, ikke systemmapper. Dokumentér før/efter. Bonus: forkert SSH-bruger skal afvises; ny generation får nye øvelsesnøgler og nyt flag.

## Broken Access

Gem og stop solos før holdstart. Opret holdlab med Access Granted. Hver operator har sin egen private øvelsesnøgle; alle arbejder mod fælles mål med den samme begrænsede rolle. Fordel bruger/grupper, mapper/filer, fejlgenskabelse og rapport. Byt efter15minutter. Holdets rapport skal angive præcis filsti, observeret fejl, årsag og mindst mulig rettelse. Efterprøv på to operatorer; del ikke private nøgler. Fælles flag giver point én gang.

## Hints og misforståelser

Start med README og vært/port. Peg derefter på id og ls -ld. Giv til sidst den konkrete rapportsti. Ingen pointstraf. Typiske fejl: stadig inde på målet efter SSH, forkert port, cd med filnavn, antagelsen at login giver adminadgang og løsningen chmod777 overalt. Vis hvordan prompt/id/pwd hjælper med at lokalisere problemet.

## Debrief og vurdering

Alle forklarer et r/w/x-fund og en ting login ikke gav lov til. Flaget dokumenterer fundet, ikke hele forståelsen. Brug elevens tre refleksionsspørgsmål og lad holdet efterprøve en forklaring. Gem noter og stop holdlabbet før eventuel tilbagevenden til solo.
