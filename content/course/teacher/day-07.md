# Dag 7 · Operation Shadow · Det samlede spor

Syv lektioner. Mission `operation-shadow` v1. Forudsætninger og kommandoer fremgår af dagens elevmateriale. Frigiv opgaven individuelt eller gennem klassens dag. Brug gratis hints. Alle identiteter, rapporter og hændelser i labbet er fiktive.

## Tavle og demonstration

01 / Plan før kommando

Start med spørgsmålet: hvilken oplysning mangler vi, og hvilken kilde kan give den? Et kendt værktøj er ikke en begrundelse for at bruge det overalt. Hold en lille liste over observeret, antaget og endnu ukendt.

02 / Sammenkæd kilder

En fil kan pege på et navn, DNS på en adresse, HTTP på en service og SSH på et autoriseret filsystem. Hvert led skal efterprøves. At en kilde henviser til en anden gør dem ikke nødvendigvis uafhængige.

03 / En forklaring der kan gentages

En god rapport viser metode, relevante resultater og begrænsninger. Kopiér ikke hele terminalen. Skeln især mellem et portfund, en vellykket HTTP-forespørgsel, et login og en faktisk fillæsning.

## Solo og løsningsvej

Læs /home/cra/academy/shadow/README.txt. Brug filsporet til at finde DNS-navnet, slå det op mod din mål-IP, og læs den tilhørende webservice på port8080. Følg henvisningen til den autoriserede SSH-rolle på port2222. Din egen øvelsesnøgle og kendte værtsnøgle ligger i academy/access; brug ssh -F /home/cra/academy/access/ssh_config nordlys. Find den rapport, du har lov at læse, og indsend dens flag.

Du bruger kun teknikker fra de tidligere dage. Der er ikke behov for brute force, scanning af andre mål eller adminadgang. Gem en metode med kilde, kommando, observation og afgrænset konklusion for hvert led. Sammenhold journalen med rapporten og markér mindst én usikkerhed. Flaget er en kvittering for det sidste fund, ikke for hele forklaringen.

Flaget er dynamisk pr. lab/generation. Bed om kilde og forklaring; et korrekt flag alene dokumenterer ikke hele læringsmålet. Vis først hintets spørgsmål, derefter værktøjet og til sidst den konkrete sti. Hints:

- Start med README og journal.txt i /home/cra/academy/shadow.
- Journalens navn kan slås op med dig @MÅL-IP NAVN A. Brug svarets IP på port8080.
- Læs /service/ for henvisningen til adgangsøvelsen.
- Brug din SSH-konfiguration, kør id og læs den tilladte rapport i /srv/cra-access/reports.

## Hold og roller

Gem og stop solos. Læreren starter Operation Shadow for hold og aftaler en rolig øvetid på45minutter. Dette er træning før finalen; den serverstyrede konkurrence kommer dag8. Fordel planlægger, undersøger, dokumentarist og efterprøver. Hints er gratis.

Løs fælles mission, byt roller undervejs og aflever en fælles reproduktion. Øv reglerne: kun eget lab, ingen delte private nøgler, ét holdflag, tydelige hjælpespørgsmål og stop ved scope-tvivl.

Læreren opretter holdet, vælger dagens mission og sikrer at hver deltager bruger egen operator. Fælles mål har en adresse pr. operatorlink. Stop holdmiljøet før genstart af solos; noter og øvelsespoint bevares.

## Misforståelser og vurdering

Bed eleverne skelne kilde fra fortolkning, historik fra aktuel tilstand og login fra rettigheder. Forklar at et negativt resultat også kan være et fund. Ingen automatisk vurdering ud fra pakketal eller tastetryk. Se fejl som et udgangspunkt for et afgrænset nyt forsøg, ikke som grund til at udvide scope.

## Debrief

- Hvilket led var den svageste del af din forklaring?
- Hvilke kilder var reelt uafhængige?
- Hvilken strategi vil I bruge i finalen?

## Differentiering

- Foreslå en alternativ metode til ét trin og efterprøv den i scope.
- Skriv en kort tjekliste til en makker uden at afsløre løsningen.
