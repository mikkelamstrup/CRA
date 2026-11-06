# 10 · Åben eller kommerciel fremtid

## Udskyd forretningsmodellen, ikke de billige arkitekturvalg

Et undervisningsprodukt kan være open source og samtidig have betalt hosting, support eller indhold. Open source og salg er ikke nødvendigvis modsætninger; Open Source Definition tillader kommerciel anvendelse og viderefordeling. Hvilken model der er rigtig her, er endnu ikke besluttet. Kilde: S25.

Byg derfor kernen, så den kan installeres af andre, men opbyg ikke betalingssystem og offentlig cloud nu. Første kvalitetstest er, om platformen virker i lærerens egen undervisning.

## Gør fra begyndelsen

Adskil platformkode, kursus-/missionindhold, imageopskrifter, branding og installationskonfiguration. Undgå hårdkodede skolen-navne, personlige filstier, interne IP'er og bestemte servere i forretningslogik. Dansk elevtekst og senere andre sprog skal være muligt uden at oversætte kodeidentifikatorer.

Versionér API, content manifest, nodeprotokol og database. Dokumentér installation, backup, hardwarekrav, understøttede arkitekturer og sikkerhedsgrænser. Gem afhængigheders oprindelse og licenser og opbyg en softwarekomponentliste/SBOM ved release.

Start med én organisation pr. installation og eksplicit organisationsscope i datamodellen. Det letter senere udvidelser, men er ikke en erklæring om, at offentlig multi-tenancy er sikker eller klar. Separate skoleinstallationer er en enkel første driftsmodel.

## Beslut før offentliggørelse

Afklar skriftligt rettigheder i forhold til skole, egen virksomhed og eventuelle medudviklere. Det er særlig vigtigt, fordi løsningen bygges til dit arbejde. Den konkrete vurdering afhænger af aftaler og arbejdssammenhæng; planen afgør ikke, hvem der ejer hvad. Kulturministeriet beskriver software som ophavsretligt beskyttet. Kilde: S27.

Vælg ikke MIT, Apache eller AGPL automatisk i et init-script. Indtil rettigheder og licens er besluttet, hold udviklingsrepositoryet privat og beskriv, at udgivelseslicensen er uafklaret. Et offentligt repository uden licens er ikke det samme som et brugbart open-source-projekt. Kilde: S24.

Når beslutningen træffes, gennemgå de konkrete licenstekster med relevant rådgivning: tilladelser, kildekodepligt, ændringer, distribution af images og eventuelt netværksbrug. En proces-/API-grænse gør ikke automatisk alle licensforpligtelser irrelevante.

## Afhængigheder er ikke alle ens

Teknisk valgte komponenter kan have forskellige licenser. AsyncSSH oplyser for eksempel EPL-2.0 med angivne betingelser for en sekundær GPL-licens; det må ikke bare registreres som MIT, fordi det er et Pythonbibliotek. Også systempakker, VM-images, fonts, grafiske assets, sensorregler og sårbare apps skal gennemgås. Kilde: S26.

Byg egne missioner frem for at kopiere kommercielle Hack The Box-images eller materiale. Inspiration til arbejdsformen er ikke en tilladelse til at distribuere andres indhold. Offentligt tilgængelige opgaver er ikke nødvendigvis frit genanvendelige i et salgsprodukt.

Eksterne bidrag kræver en gennemtænkt bidragsmodel, før bidrag accepteres. En plan om mulig senere dobbeltlicensering skal tage højde for bidragydernes rettigheder; den kan ikke antages alene ud fra, hvem der administrerer Git-repositoryet.

## CTFd og øvrige udvidelser

CTFd er en eksisterende mulighed for udfordringer og CTF-funktioner. Til den første klasseversion anbefales en snæver intern motor med faste point, teams, deadline og append-only scorehistorik, fordi platformen allerede skal styre progression og identitet. En CTFd-integration kan tilføjes senere, hvis et konkret konkurrencebehov gør det fordelagtigt. Kilde for CTFd's generelle funktion: S28.

Uanset løsning skal en konkurrence have én autoritativ scorekilde. Byg ikke to systemer, som begge mener, at de bestemmer holdenes point. Hold integrationsgrænsen smal og test overgang/eksport.

## Mulige senere udgivelser

Selvhostet open-source-kerne med dokumenteret installation; betalt undervisnings-/missionsindhold; support og installationshjælp; eller en administreret tjeneste. Det er muligheder, ikke en prognose om marked eller omsætning.

M10 forudsætter en stabil klasseversion, rettighedsafklaring, sikkerhedsgennemgang, licensbeslutning, dokumenteret opdateringsprocedure og en test hvor en anden person installerer løsningen uden lærerens personlige serveropsætning.
