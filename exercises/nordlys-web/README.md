# Nordlys: offentlig forside og isoleret CRA-øvelse

Nordlys er en fiktiv organisation. Den offentlige side ligner en organisationsside; forsiden har ingen synlige opgavelinks, rapporthenvisninger eller flag. Første spor ligger i HTML-kommentaren mærket WEBTEAM. Sporet fortsætter gennem robots.txt, driftsoversigt, journal og rapport NL-207.

Eleverne åbner den offentlige side med knappen **Åbn Nordlys-hjemmesiden** under **Dine opgaver** i CRA. curl køres mod elevens egen mål-IP på port 8080. CRA-labs har fortsat ingen internetadgang. Den offentlige rapport er en demonstrationskopi uden personligt flag; rapporten på hver målmaskine har sit eksisterende generationsbundne CRA-flag og giver 100 point.

Det er samme HTTP-indhold, en browser med den præcise sti kan hente. Øvelsen lærer undersøgelse med curl, kildehenvisninger og forskellen på skjult navigation og adgangskontrol; den forsøger ikke at gøre curl til den eneste mulige klient.

## Drift

Runtime: /home/crauser/nordlys-web. Den aktuelle offentlige adresse står i url.txt her. Kun seks faste, på forhånd indlæste dokumenter serveres på 127.0.0.1:28110. Ingen vilkårlige filstier, uploads, proxyfunktioner, adgangslogs, cookies eller eksterne browserressourcer. Webserveren begrænser antal samtidige handlers og forbindelsestid.

Brugerservices:

```bash
systemctl --user status nordlys-web nordlys-web-tunnel
cat /home/crauser/nordlys-web/url.txt
```

Cloudflare Quick Tunnel er beregnet til test og kan få en ny adresse ved genstart; den har ingen oppetidsgaranti. Se [Cloudflares dokumentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

Tunnelrunneren registrerer den nye offentlige adresse. En enkelt publisher sammenfatter ændringer til seneste URL og kalder publish_url.py. Konfigurationen opdateres atomisk i CRA's statiske nordlys.json samt i frontendens public-mappe til næste build. CRA-knappen genlæser konfigurationen hvert 30. sekund. URL'en valideres som HTTPS på et enkelt trycloudflare.com-underdomæne, og knappen bruger en ny fane med noopener/noreferrer. Hosting sker på ejerens server og Cloudflare Tunnel som bestilt; Sites-hosting bruges ikke.

Services er enabled i brugerens systemd. Fuld servergenstart, automatisk start af selve CRA-VM'en og ændret tunnel-hostname er ikke en del af denne opgaves faktiske accept. CRA-linkopdateringen forsøges igen, hvis kontrol-VM'en er utilgængelig.

## Test

```bash
python3 -m unittest discover -s exercises/nordlys-web -p 'test_*.py' -v
```

Dækker faktisk lokal HTTP-trail, skjulte spor i synlig DOM, tilladte ruter, afvisning af filstier/source/listing, GET/HEAD/405/404 og sikkerhedsheaders. Lærerfacit findes i docs/teacher/opgave-03.md fra repositoryroden. Slutaccept beskrives i handoffs/CLASS-NORDLYS-WEB-01.md.
