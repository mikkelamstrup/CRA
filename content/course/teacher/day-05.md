# Dag 5 · Packet Hunter · Spor i trafikken

Syv lektioner. Mission `packet-hunter` v1. Forudsætninger og kommandoer fremgår af dagens elevmateriale. Frigiv opgaven individuelt eller gennem klassens dag. Brug gratis hints. Alle identiteter, rapporter og hændelser i labbet er fiktive.

Til grafik vælges “Grafisk labdesktop · browser og Wireshark” inden laboprettelsen. Budgettér 2 GiB RAM pr. operator og ekstra klargøringstid. Det samme kernemål kan løses via terminalen; brug aldrig en proxy til den sårbare app under CRA’s origin.

## Tavle og demonstration

01 / Hvad er en capture?

En capture indeholder pakker, som et målepunkt faktisk så. Den fortæller ikke automatisk, hvem der sad ved tastaturet, eller hvorfor noget skete. Din fil er skabt fra rigtig trafik i dit eget lab under oprettelsen. requests.json beskriver de forespørgsler, klargøringen faktisk sendte.

02 / Afgræns dit udsnit

-r læser en gemt fil; -i ville vælge et interface til ny optagelse. -nn holder adresser og porte numeriske. Et filter vælger hvilke pakker du ser, men ændrer ikke originalfilen. HTTP uden TLS kan vise indhold i klartekst.

03 / Tid og kilde

Et tidsstempel afhænger af målemaskinens ur og tidszone. Her bruges UTC. En log og en capture kan måle forskellige øjeblikke af samme forespørgsel. At to ting ligger tæt i tid beviser ikke, at den ene forårsagede den anden.

## Solo og løsningsvej

I /home/cra/academy/capture ligger incident.pcap, requests.json og README.txt. Capturefilen er optaget med tcpdump under dit labs oprettelse, mens virkelige HTTP-forespørgsler blev sendt på dit eget lablink. Hændelserne er en fiktiv Nordlys-øvelse. Læs README og undersøg filen med tcpdump -nn -tttt -r /home/cra/academy/capture/incident.pcap. Brug derefter -A og filteret tcp port 8080 for at se den ukrypterede HTTP-tekst.

Find rapportNL-505 i den historiske respons og indsend dens flag. Rapporten er ikke længere tilgængelig på den kørende webserver; skeln mellem historisk capture og nuværende HTTP-status. Sammenhold med requests.json, som indeholder faktisk forespørgselstid i UTC, sti, status og antal bytes, men intet flag. Åbn eventuelt filen i Wireshark inde i labbets grafiske skrivebord.

Dokumentér mindst tre hændelser i tidsorden, kilde og én usikkerhed. Ingen scanning eller capture af skole, hjemmenet eller andre labs. Brug kun din udleverede capture. Flaget dokumenterer et fund, ikke din fulde årsagsforklaring.

Flaget er dynamisk pr. lab/generation. Bed om kilde og forklaring; et korrekt flag alene dokumenterer ikke hele læringsmålet. Vis først hintets spørgsmål, derefter værktøjet og til sidst den konkrete sti. Hints:

- Læs cat /home/cra/academy/capture/README.txt.
- tcpdump -nn -tttt -r FIL viser tid og adresser uden nye netværksopslag.
- Tilføj -A og filteret tcp port 8080 for at se HTTP-tekst.
- Find responsen på /evidence/trace-505.txt. Sammenhold med requests.json; den nuværende URL kan være404.

## Hold og roller

Gem og stop solos. Læreren starter et Packet Hunter-holdlab. Hver operator har en capture fra sit eget link til fælles mål, og tidsstemplerne er derfor forskellige. Fordel capture, requestlog, efterprøvning og dokumentation. Byt roller.

Aflevér en fælles tidslinje i holdnotesbogen med mindst tre hændelser, UTC, kilde og sikkerhed i konklusionen. Markér hvad I ikke kan afgøre. Forklar hvorfor ens flag ikke betyder identiske captures.

Læreren opretter holdet, vælger dagens mission og sikrer at hver deltager bruger egen operator. Fælles mål har en adresse pr. operatorlink. Stop holdmiljøet før genstart af solos; noter og øvelsespoint bevares.

## Misforståelser og vurdering

Bed eleverne skelne kilde fra fortolkning, historik fra aktuel tilstand og login fra rettigheder. Forklar at et negativt resultat også kan være et fund. Ingen automatisk vurdering ud fra pakketal eller tastetryk. Se fejl som et udgangspunkt for et afgrænset nyt forsøg, ikke som grund til at udvide scope.

## Debrief

- Hvad kan et packetfund faktisk bevise?
- Hvorfor kan en historisk200 være en404 nu?
- Hvilken usikkerhed er vigtigst i jeres tidslinje?

## Differentiering

- Sammenlign TCP-handshake og HTTP-forespørgsel i Wireshark.
- Forklar hvilken information TLS ville skjule, og hvilke metadata der stadig kunne være synlige.
