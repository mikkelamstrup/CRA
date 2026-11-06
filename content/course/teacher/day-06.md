# Dag 6 · Vulnerable App · Den forkerte rapport

Syv lektioner. Mission `vulnerable-app` v1. Forudsætninger og kommandoer fremgår af dagens elevmateriale. Frigiv opgaven individuelt eller gennem klassens dag. Brug gratis hints. Alle identiteter, rapporter og hændelser i labbet er fiktive.

Til grafik vælges “Grafisk labdesktop · browser og Wireshark” inden laboprettelsen. Budgettér 2 GiB RAM pr. operator og ekstra klargøringstid. Det samme kernemål kan løses via terminalen; brug aldrig en proxy til den sårbare app under CRA’s origin.

## Tavle og demonstration

01 / To forskellige spørgsmål

Autentifikation spørger hvem du er. Autorisation spørger hvad du må gøre med en bestemt ressource. En gyldig session besvarer ikke automatisk det sidste spørgsmål. Denne lille app har med vilje glemt kontrollen af rapportens ejer.

02 / Session og cookie

Serveren giver en tilfældig sessionscookie efter login. Browseren sender den med næste forespørgsel. I curl kan -c gemme og -b sende cookies. En cookie er adgangsmateriale: del den ikke og kopier den ikke til notesbogen.

03 / En afgrænset adgangsfejl

Når et id i en forespørgsel vælger et objekt, skal serveren kontrollere adgang til netop det objekt. Det hjælper ikke kun at skjule linket. Du har udtrykkelig tilladelse til at undersøge701 og702 i denne ene labapp.

## Solo og løsningsvej

Brug kun den fiktive øvelsesapp på http://MÅL-IP:8081/ i dit eget lab. Åbn den i browseren inde i labbets grafiske skrivebord, eller brug curl i terminalen. Øvelsesbrugeren er analyst; den offentlige øvelseskode er nordlys-training-only. Det er fiktive labcredentials og må aldrig genbruges.

Undersøg først /api/reports?id=701 uden login. Log derefter ind og læs /api/me og rapport701. Din rolle må kun læse701. Appens forside nævner rapport702, som tilhører administratorrollen. Undersøg den præcise forskel ved at ændre kun id til702. Find flaget i den beskyttede rapports indhold og indsend det.

CLI: curl -c /home/cra/academy-app.cookies -d "username=analyst&password=nordlys-training-only" http://MÅL-IP:8081/login. Brug derefter curl -b /home/cra/academy-app.cookies -i "http://MÅL-IP:8081/api/reports?id=701" og tilsvarende702. Anførselstegn beskytter URL’ens tegn mod shellen.

Dokumentér forventet adgang, faktisk adgang, forespørgsel og svar. Forklar hvorfor et login ikke er tilstrækkelig autorisation. Foreslå serverkontrol af brugerens adgang til hvert objekt. Kun denne labapp er bevidst sårbar; CRA-platformen, andre labs og eksterne sider er uden for scope.

Flaget er dynamisk pr. lab/generation. Bed om kilde og forklaring; et korrekt flag alene dokumenterer ikke hele læringsmålet. Vis først hintets spørgsmål, derefter værktøjet og til sidst den konkrete sti. Hints:

- Begynd uden login, og notér HTTP401.
- Gem sessionens cookie med curl -c, og send den igen med -b.
- Sammenlign /api/me, rapport701 og rapport702. Hold øvrige dele af URL’en uændrede.
- Rapport702 tilhører en anden rolle, men serveren kontrollerer kun login. Find flaget i dens body.

## Hold og roller

Gem og stop solos. Læreren vælger Vulnerable App som fælles holdmission. Fordel reproduktion uden login, reproduktion som analyst, afhjælpning og negativtest. Alle bruger kun deres eget link til fælles mål. Byt roller og efterprøv resultaterne.

Skriv en fælles rapport: forventet/observeret adgang, præcis request, effekt på fiktive data, rettelse og en test der skal give403 efter rettelsen. Gem ikke cookies, password eller selve flaget i rapporten.

Læreren opretter holdet, vælger dagens mission og sikrer at hver deltager bruger egen operator. Fælles mål har en adresse pr. operatorlink. Stop holdmiljøet før genstart af solos; noter og øvelsespoint bevares.

## Misforståelser og vurdering

Bed eleverne skelne kilde fra fortolkning, historik fra aktuel tilstand og login fra rettigheder. Forklar at et negativt resultat også kan være et fund. Ingen automatisk vurdering ud fra pakketal eller tastetryk. Se fejl som et udgangspunkt for et afgrænset nyt forsøg, ikke som grund til at udvide scope.

## Debrief

- Hvorfor var det ikke nok at være logget ind?
- Hvor skal ejerskabskontrollen ligge?
- Hvilken negativtest ville forhindre gentagelse?

## Differentiering

- Sammenlign401,403 og404 som designvalg uden at afprøve andre systemer.
- Forklar hvorfor en uforudsigelig rapport-ID ikke erstatter adgangskontrol.
