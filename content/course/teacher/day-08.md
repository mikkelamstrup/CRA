# Dag 8 · Cyber Championship

## Tavle og mål

Tegn kæden observation → kilde → begrænset konklusion. Gennemgå scope før terminalerne åbnes. Finalen måler godkendte fund; forklaring, samarbejde og metode vurderes særskilt. Ingen angriber andre hold.

## Forberedelse og tidsplan

Kontrollér kapaciteten med de valgte holdstørrelser og GUI-profiler. Hver deltager har egen operator; GUI kræver mere RAM. Gem og stop solos. Opret hold uden fælles deltagere. Kontrollér et nyt testlab og hjælpekøen før undervisningen. Opret konkurrencen under Konkurrence med deltagende hold og offentliggjort varighed, fx 90 minutter. Ingen live-elevdata i udviklerlogs eller agentværktøjer.

Lektion 1 regler/tekniktjek; lektion 2 kort opvarmning og roller; lektion 3–5 konkurrence; lektion 6 løsninger; lektion 7 refleksion og eksport. Justér pauser efter skolens skema. Hvis teknikproblemer kræver pause, brug Sæt på pause for alle hold og fortæl at labs stadig er åbne. Uret og afleveringerne pauses; fysisk pause i labarbejdet aftales mundtligt. Afslut og fjern eventuelle opvarmningslabs, før finalens miljøer oprettes.

## Regler der annonceres før start

Fem version 1-opgaver med faste konkurrencepoint: Filsporet (first-files) 100; Servicen (first-contact) 150; Adgangsfejlen (vulnerable-app) 200; Den historiske rapport (packet-hunter) 250; Operation Shadow 400. Alle hints er gratis. Hold vælger rækkefølge. Ét aktivt miljø pr. hold; læreren opretter/skifter under Mit hold. Uret kører under opstart og skift. En teknisk fællespause kan kompensere for driftsproblemer.

Start konkurrencen **før** nye konkurrencelabs oprettes. Tidligere øvelsesmiljøer kan ikke bruges, selv om de nulstilles. Almindelige missionsafleveringer giver øvelsespoint; konkurrenceflags afleveres under Konkurrence. Reset eller nyt miljø giver ikke flere point for samme opgave. Ved lige point deles placering. Deadline afgøres af serveren efter nødvendige låse; et klik før fristen er ikke garanti for rettidig modtagelse.

Frys ranglisten skjuler senere point, mens afleveringer fortsætter. Vis alle point afslører den fulde rangliste igen. Brug Vis rangliste på denne skærm til en visning med holdaliaser og konkurrencepoint uden kontonavne, flags eller noter. Visningen kræver en eksisterende autentificeret session; den er ikke et offentligt delingslink. Afslut konkurrencen lukker for nye afleveringer og kan ikke fortrydes. Genindlæsning eller servergenstart rekonstruerer ranglisten fra scorehistorikken.

## Hinttrin og facit

Brug de tre hinttrin fra dag 1, 2, 5, 6 og 7. Først spørg hvilken information holdet mangler, derefter hvilket værktøj der passer, og til sidst et konkret eksempel. Ingen flag står i materialet; de skabes pr. lab og generation.

- Filsporet: læs README/journal og find rapporten i operatorens academy-mappe. Dag 1 viser den fulde vej.
- Netværk: scan kun mål-IP’en; undersøg den fundne HTTP-service på port 8080 med curl.
- Web: log ind med de offentliggjorte laboplysninger. Sammenlign /api/me og rapport 701 med /api/reports?id=702. Autentifikation uden objektadgangskontrol er fejlen.
- PCAP: læs /home/cra/academy/capture/incident.pcap med tcpdump eller Wireshark; følg HTTP-responsen med NL-505. Den historiske rapport-URL svarer nu 404. requests.json dokumenterer faktiske forespørgsler i UTC.
- Finale: læs shadow/journal.txt; slå rapport.nordlys.test op mod lab-DNS; læs /service/ på port 8080; brug egen academy/access/ssh_config til analyst på port 2222 og læs /srv/cra-access/reports/rapport-404.txt.

## Typiske misforståelser

Et portfund er ikke et kompromis. HTTP 200 beviser ikke lovlig adgang. Samme flag inden for et hold betyder ikke samme operator-IP, nøgle eller capture. En frossen rangliste betyder ikke, at afleveringen fejlede. Øvelsespoint er ikke finale-point. Et gammelt flag virker ikke efter reset. Holdnoter overlever opgaveskift, filer i et fjernet miljø gør ikke.

## Debrief og opsamling

Hvert hold fremlægger én løsning på fem minutter: scope, kilde, kommando, fund og usikkerhed. Lad en makker gentage et afgørende trin i et nyt øvelsesmiljø efter finalen. Vurdér forklaring og samarbejde særskilt. Lad eleverne eksportere egne noter. Stop resterende labs kontrolleret og bevar progressionen; slet kun miljøer efter aftale.
