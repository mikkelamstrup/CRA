OPGAVE 10 · Spor · Fra loglinje til tidslinje

En supporteksport kan have afsløret en session. Du må undersøge hændelsen, men endnu ikke ændre politikken. Alle spor er fra rigtige forespørgsler i dit isolerede lab.

1. Læs mandatet. Gem en kopi af initial-access.jsonl gennem din udleverede SSH-adgang, og beregn dens SHA-256. Filen på målet er skrivebeskyttet.
2. Find den tidlige forespørgsel til supporteksporten og den efterfølgende rapportlæsning med sessionstypen legacy-support. Notér de præcise UTC-tider, sti, status, bruger og antal svarbytes. Lav en tidslinje med kilder.
3. Hent supporteksporten via HTTP. Afprøv den fundne øvelsessession fra din operator og dokumentér, at den kan hente rapport 8202. Del aldrig den egentlige CRA-login-cookie med portalen.
4. Udfyld kontrol.json og kør bridge-check. Sæt conclusion til confirmed_report_response, hvis beviserne faktisk viser et udleveret svar, og attribution til unknown: en lokal kildeadresse beviser ikke angriberens identitet. Flaget giver 200 point.

Din rapport skal skelne et bekræftet HTTP-svar fra antagelser om videre brug eller hele kundens omfang. Dette er analyse, ikke en færdig reparation.

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
