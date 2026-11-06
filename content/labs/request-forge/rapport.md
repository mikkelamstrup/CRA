# Request Forge · individuel angrebsrapport

## 1. Mandat og mål

Egen mål-IP, dato, afgrænsning og testkonto (ingen sessionsværdier).

## 2. Normal request og baseline

Metode, sti, headers, cookie, JSON-body og faktisk response. Forklar formålet
med hver nødvendig del. Angiv filnavne og maskér cookie/CSRF.

## 3. Kontrolforsøg

| Ændring | Forventning | Faktisk status/body | Konklusion |
|---|---|---|---|
| Uændret gyldig ordre | | | |
| Ukendt ordre | | | |
| Manglende cookie | | | |
| Forkert CSRF-token | | | |
| Forkert format | | | |

## 4. Påvisning af SQL-injektion

Dit præcise sand/falsk-par, begge faktiske responses og en gentagelse i omvendt
rækkefølge. Forklar hvorfor forskellen følger SQL-betingelsen.

## 5. Blind udledning

Hvordan identificerede du databasen, tabellen og kolonnen? Vis mindst ét fund
udledt fra systemkataloget. Beskriv længde/tegn-metoden, afgrænsninger, antal
requests, timeout, stopbetingelse og kontrol af den samlede udledte værdi.
Vedlæg dit eget script og instruktion til genkørsel med en ny session.

## 6. Resultat og begrænsninger

Responsstatus fra bevis-endpointet, CRA-aflevering og hvad de beviser.
Hvilke øvrige data eller rettigheder har du ikke påvist adgang til?

## 7. Årsag og kort afhjælpning

Hvor blev brugerdata til SQL-kode? Hvilken afhjælpning adresserer årsagen?
