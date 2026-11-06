OPGAVE 12 · Ejerskab · Ret fejlen og bevis normal drift

Supporteksporten er allerede lukket og den lækkede session tilbagekaldt, men en kundes gyldige login kan stadig give adgang til den anden kundes rapport. Din opgave er en varig ejerskabskontrol og en reproducerbar regressionstest.

1. Brug separate cookiefiler til kunde-a og kunde-b. Før ændring: test begge kunder mod begge rapporter og gem hele 2×2-matricen. Du skal bevise selve fejlen, ikke blot gætte en konfigurationsnøgle.
2. Gem oprindelig politik. Aktivér enforce_report_ownership via din begrænsede SSH-adgang. Bevar de allerede gennemførte sikkerhedstiltag og normal drift.
3. Test igen begge kunder mod begge rapporter: egen adgang skal virke og fremmed adgang afvises. Test også anonym adgang, den tilbagekaldte session og supporteksporten. En server der afviser alle, er ikke en færdig løsning.
4. Udfyld kontrol.json og kør bridge-check. Kontrollen kræver faktiske krydslæsninger både før og efter rettelsen, en korrekt slutmatrix og fungerende tjenester. Flaget giver 300 point.

Aflever før/efter-matrix, begrundelse, afgrænsninger og en kort driftsoverdragelse. I opgave 13 får du den samlede hændelse og skal selv vælge rækkefølge og samle metoderne fra opgave 9–12.

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
