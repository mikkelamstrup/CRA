REQUEST FORGE · Opgave 14 · Individuel offensiv mission

Nordtele har bestilt en autoriseret penetrationstest af sin fiktive ordreportal.
Læs mandat.txt. Målet er http://MÅL-IP:8081/ på din egen udleverede mål-IP.
Du har en almindelig testkonto: auditor / nordtele-lab-only.
NT-1042 og NT-1043 er gyldige demoordrer. NT-0000 findes ikke.

Opgaven er avanceret. Du skal kunne bruge curl, forstå HTTP/JSON og skrive en
kort løkke i Python. Du skal arbejde metodisk med hypoteser og kontrolforsøg.
Sæt cirka fire timer af. Start med en plan og gem dine beviser undervejs.

DIN OPGAVE
1. Log ind og udfør en normal søgning. Gem den faktiske request og response.
2. Rekonstruér og gensend den uden browserens formular. Forklar alle nødvendige
   HTTP-dele. Fjern én del ad gangen og dokumentér serverens forskellige svar.
3. Find en SQL-injektion. Bevis den med et sand/falsk-par, hvor resten er ens.
4. Udled databasens struktur og en skjult intern beviskode uden direkte SQL-output.
   Koden består af 16 små hextegn. Tabel- og kolonnenavne er ikke udleveret.
5. Automatisér først efter dine manuelle beviser. Skriv selv din udledning;
   sqlmap eller en færdig solver opfylder ikke rapportkravet i denne opgave.
6. POST /api/proof med JSON {"code":"DIN_UDLEDTE_KODE"}, samme session og CSRF.
   Indsend det returnerede flag i CRA. Aflever rapport.md til læreren.

VÆRKTØJER
Hele forløbet kan løses med curl og Python 3 i terminalen. Grafisk desktop er
valgfrit; brug i så fald Network-fanen i labbrowserens udviklerværktøjer.
Klientens JavaScript kan også læses med curl. Ingen ekstra pakker er nødvendige.
curl -i viser responsheaders. -c/-b gemmer/bruger cookies. --data-binary @fil.json
sender filens bytes; -H sætter en header. Gem body og headers separat, når du
skal parse JSON. curl -v viser den udgående request, inklusive øvelsessessionen.
I Python kan json, urllib.request og http.cookiejar løse samme opgave.

Sessioner udløber efter en time eller portalgenstart. Log ind igen, og brug det
nye CSRF-token sammen med den nye cookie. GET /api/session viser aktuelt token.
Beviskoden bevares ved stop/start; reset giver en ny kode og et nyt flag.
HTTP 200 er ikke i sig selv et fund. 401/403/400/415 er heller ikke et falsk
SQL-resultat: kontrollér først transport, login, CSRF, JSON og SQL-syntaks.

GEM BEVISER
Brug en mappe under /home/cra/academy/request-forge. Notér requestantal og kilde.
Maskér cookies/CSRF i rapporten; andre skal genskabe deres egen session.
Kopiér rapporten ud før reset/fjernelse. CRA-noter og point bevares separat.
