# Opgave 3: Websporet · Den glemte rapport

35 minutter som begyndermission efter Første kontakt. Mission-ID: web-trail, version 1. Ét flag giver100 point. Forklaring og refleksion vurderes af læreren; fundet af flaget beviser ikke forståelse.

## Vis hjemmesiden

Eleverne kan trykke **Åbn Nordlys-hjemmesiden** under **Dine opgaver**. Knappen åbner demonstrationssiden i en ny fane. Genindlæs CRA én gang, hvis den nye knap endnu ikke vises. Vis gerne forsiden før øvelsen, uden at afsløre HTML-kommentaren eller den fulde rapportsti.

## Frigivelse

I Command Center vælges Elever og opgaver, den ønskede elev og Frigiv opgave 3. En individuel frigivelse ændrer ikke klassens øvrige adgang og starter ikke et miljø. Eleven vælger opgaven i Dine opgaver. Et opgaveskift kræver bekræftet fjernelse af det tidligere miljø; gem derfor ønskede VM-filer først. Platformnoter og point bevares. Ny klargøring kan stå i kø.

Adgang følger CRA's eksisterende regler: I en aktiv undervisningssession på dag 1 eller 2 kræver opgave 3 individuel frigivelse. Uden en aktiv undervisningssession er kataloget åbent; en session på dag 3 eller senere giver også klasseadgang, hvis disse kursusdage senere bliver tilføjet. Installationen ændrer ikke klassens session eller tildeler individuelle frigivelser. Opgaven tilføjer ikke en ny undervisningsdag.

Den offentlige Nordlys-side på Cloudflare er til introduktion i browseren og indeholder ingen personlige flag. Eleverne skal bruge deres egen mål-IP i CRA til kommandoerne herunder. Labbet har ikke internetadgang. Kopierne har samme forside og spor, men hver CRA-rapport har sit eget flag. curl er undersøgelsesværktøjet; en browser med den præcise sti kan også hente en offentligt tilgængelig rapport.

## Kort introduktion

- En IP-adresse udpeger målet; porten udpeger servicen.
- En URL-sti, fx /robots.txt, hører til webserveren. Den skal hentes med curl, ikke åbnes med cd i elevens egen maskine.
- HTTP-status 200 betyder, at forespørgslen lykkedes; 404 betyder, at ressourcen ikke blev fundet.
- curl -i viser svarheaders og indhold. curl uden -i viser indholdet.
- robots.txt er instruktioner til samarbejdende robotter. Disallow er ikke en adgangsbegrænsning.
- Gode fund dokumenteres med kommando, URL, observation og en afgrænset konklusion.

## Løsningsvejledning til læreren

Erstat MÅL-IP med adressen i elevens eget lab. Undersøg kun dette mål. Nedenstående er hele løsningen; giv gerne eleverne kun næste nødvendige hint.

```bash
nmap -sT -p 1-9000 MÅL-IP
curl -i http://MÅL-IP:8080/
curl -s http://MÅL-IP:8080/ | grep -A 3 WEBTEAM
curl http://MÅL-IP:8080/robots.txt
curl http://MÅL-IP:8080/drift/
curl http://MÅL-IP:8080/drift/journal.txt
curl http://MÅL-IP:8080/rapporter/rapport-207.txt
```

Forsiden ligner en almindelig organisationsside. Henvisningen til robots.txt er skjult i en HTML-kommentar og kan læses med curl; den er ikke et synligt link i browseren. Her står /drift/, hvis indeks henviser til journal.txt. Journalen skelner mellem NL-206 og NL-207. Rapport 207 indeholder elevens eget flag. Eleven indsender hele flaget inklusive klammer. Et andet miljøs flag eller et flag fra før nulstilling skal afvises. Der er intet fælles facitflag i dette dokument.

## Spørgsmål og vurdering

Bed eleven vise porten, mindst én HTTP-status, kilden til rapportstien og den faktiske rapport. Spørg: Hvorfor virkede direkte adgang trods Disallow? Hvad er forskellen på, at et link ikke vises, og at serveren afviser adgang? Løsningen er at fjerne interne dokumenter fra det offentlige område eller indføre serverkontrolleret autentifikation og autorisation. At ændre navnet eller tilføje mere til robots.txt er ikke tilstrækkeligt.

Bonus: Brug curl -I på robots.txt og sammenlign med curl -i. Prøv en opdigtet URL-sti og forklar 404-svaret. Det er ikke bevis for, at hele webserveren er nede.

## Typiske stop

Hvis nmap ikke finder webporten, kontrollér mål-IP og vent på Klar. Ved404: kontrollér stavning og hele URL-stien, inklusive skråstreg efter drift. Ved forkert flag: kontrollér NL-207, eget miljø, klammer og at labbet ikke er blevet nulstillet. Nulstil ikke et fungerende miljø blot for at prøve igen.
