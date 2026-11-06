OPGAVE 9 · HTTP · Hvem må læse rapporten?

Du kender nu filfund, DNS og SSH fra opgave 8. Nu undersøger du en kundeportal, der allerede har korrekt adgangskontrol. Dit mål er at kunne forklare en HTTP-request og efterprøve, hvem den repræsenterer.

1. Læs README.txt og mandat.txt. Sæt BASE til din egen mål-IP på port 8081. Gem headers og body fra en anonym rapportforespørgsel.
2. Log ind som kunde-a med formularfelterne username og password. Gem sessionen i en cookiefil og brug den igen. Undersøg /api/me.
3. Sammenlign egen rapport 8201 med kunde-b's rapport 8202. Notér metode, sti, identitet, status og hvad body faktisk beviser. Undersøg også hvad forkert adgangskode giver på /login: en 403 er ikke altid samme fejltype.
4. Udfyld kontrol.json ud fra dine resultater og overfør den til den faste afleveringsfil på målet. Kør bridge-check via den udleverede SSH-konfiguration. Flaget giver 150 point. Læreren vurderer din forklaring særskilt.

Politikken er skrivebeskyttet i denne opgave. Du skal forstå og måle korrekt adgang, ikke ændre serveren.

ARBEJDSMAPPE OG KONTROL
mkdir -p ~/beviser
Sæt BASE=http://DIN-MÅL-IP:8081 og brug kun dette mål.
Gem headers og body med curl -sS -D headers.txt -o body.json URL.
Brug fx curl -sS -o body.json -w '%{http_code}\n' URL til status.

kontrol.json i denne mappe er en tom skabelon. Erstat null med dine målte værdier og behold nøglefelterne. Tidsstempler og rapport-ID er tekst; HTTP-status er tal.
Overfør filen fra operatoren til den faste afleveringsfil:
cat /home/cra/academy/incident-bridge/kontrol.json | ssh -F /home/cra/academy/incident-bridge/ssh_config nordtele 'cat > /srv/incident-bridge/kontrol.json'
Kør kontrollen:
ssh -F /home/cra/academy/incident-bridge/ssh_config nordtele bridge-check
Et godkendt svar giver dit eget flag. Kopiér det ind i CRA's flagfelt.

Kør alle HTTP-forsøg fra DIN OPERATOR mod mål-IP'en. Verifierens egne localhost-tests eller øvelsesprocessens angreb tæller ikke som dine praktiske øvelser. Følg de konkrete stier fra sagsmappen, så kontrolfil og forsøg kan sammenholdes. Gem beviser undervejs; aktuelle logs roterer, mens det frosne første bevis bevares.
