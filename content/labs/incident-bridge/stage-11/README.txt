OPGAVE 11 · Inddæmning · Stop lækket, genåbn driften

Du har nu fundet lækvejen. Træn den første driftsbeslutning uden at forveksle begrænsning med en komplet reparation. Politikken kan ændres gennem den udleverede analyst-adgang.

1. Gem oprindelig politik og beviser i din rapportmappe. Mål at den lækkede session kan bruges, og notér kundepåvirkningen.
2. Sæt emergency_lockdown til true. Afprøv fra operatoren GET /api/reports?id=8201 og gem det faktiske 503-svar. Forklar hvem der også mister adgang.
3. Fjern supporteksporten med expose_support_bundle=false og tilbagekald den fundne øvelsessession i revoked_sessions. Genåbn med emergency_lockdown=false. Kontrollér syntaks, så ugyldig JSON ikke forveksles med vellykket indgriben.
4. Afprøv den tilbagekaldte session, almindeligt kundelogin og egen rapport. Afprøv også kunde-a mod rapport 8202: er ejerskabsfejlen stadig til stede? Udfyld remaining_cross_status med dit faktiske 200- eller 403-resultat. En ekstra korrekt ejerskabsrettelse er tilladt, men er fokus i næste opgave.
5. Udfyld kontrol.json og kør bridge-check. Kontrollen kræver både et faktisk 503-forsøg under begrænsning og senere fungerende drift samt afvist lækket session. Flaget giver 250 point for denne afgrænsede kontrol.

Skriv en kort status til ledelsen: hvad er begrænset, hvilke kunder er påvirket, hvilke beviser er bevaret, og hvad mangler før en fuld godkendelse?

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
