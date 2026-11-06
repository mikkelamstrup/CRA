# 11 · Kontrakter og tilstandsmodeller

**Status: oprindelige designkrav. Den kørende M1–M3-implementering og dens verificerede omfang fremgår af CURRENT_STATE.md, backend/cra og handoffs; kapitlet beskriver også kontrakter til senere milepæle.**

## Datamodel

| Entitet | Rolle |
|---|---|
| Organization | Installations-/organisationsscope og politik |
| User / Membership | Konto, klasse-/organisationsrolle og status |
| Class / Enrollment | Elever, lærer og tilknyttet forløb |
| CourseVersion / Lesson | Versionslåst undervisningsrækkefølge |
| MissionVersion | Manifest, objective-definitioner og imagekrav |
| Team / TeamMembership | Holdidentitet og medlemskab |
| LabSession | Ejer/team, missionversion, node, generation og lifecycle |
| OperatorAllocation | Bruger → operator-VM/vNIC i en labgeneration |
| ObjectiveAttempt / Completion | Indsendelse og valideret progression |
| ScoreEntry | Uforanderlig score-/korrektionshistorik |
| Job / Lease | Holdbar udførelse af provisioning og cleanup |
| Node / Reservation | Inventory, trust, arkitektur og kapacitetsreservation |
| Event / Outbox | Domænehændelser, dispatch og replay |
| TerminalGrant | Kortlevende tildeling med user/lab/generation og revokation |
| HintUse / HelpRequest | Faglig hjælp adskilt fra tekniske fejl |

Alle følsomme forespørgsler og eventabonnementer scopes på serveren. Et UUID er ikke en adgangskontrol. Organisation, klasse, hold og rolle hentes fra autoriseret medlemskab, ikke blindt fra browserens JSON.

## Lab-lifecycle

`REQUESTED → QUEUED → PROVISIONING → READY → STOPPING → STOPPED`

`STOPPED → STARTING → READY`

`READY/STOPPED → RESETTING → READY` med ny generation.

`… → DESTROYING → DESTROYED`

Enhver asynkron overgang kan ende i `ERROR` med fejlkode og forklarlig recovery. Manglende heartbeat vises separat som node-/connectivityproblem; databasen må ikke foregive, at en VM er slukket, hvis vi ikke ved det.

Et `start`-kald returnerer operation-ID og aktuel tilstand, ikke falsk “ready”. Browseren følger opgaven via status/eventkanal. Samme idempotency key må ikke oprette et ekstra lab. Udgåede jobs og sene callbacks kontrolleres mod generation og aktuel ønsket tilstand.

## Eksempel på HTTP-overflade

```text
POST /api/v1/labs
GET  /api/v1/labs/{lab_id}
POST /api/v1/labs/{lab_id}/stop
POST /api/v1/labs/{lab_id}/reset
POST /api/v1/labs/{lab_id}/terminal-grants
POST /api/v1/labs/{lab_id}/objectives/{objective_id}/attempts
POST /api/v1/classes/{class_id}/sessions
POST /api/v1/teams/{team_id}/help-requests
GET  /api/v1/classes/{class_id}/teacher-view
GET  /api/v1/projections/{projection_id}/public-view
GET  /api/v1/operations/{operation_id}
```

Ruterne er et designudkast, ikke eksisterende endpoints. Valider schemas, returnér strukturerede fejl og generér TypeScript-typer fra én versioneret kontraktkilde. Projections kan være beskyttet read-only-adgang; “public-view” beskriver datasættet, ikke at hele internettet må tilgå det.

## Terminalprotokol

Adskil kontrolframes (`resize`, `ack`, `ping`, `close`) fra terminalbytes. Fastlæg encoding, maksimale størrelser, acknowledgements og flow control. Binary frames kan bruges til byte-stream; konvertering skal bevare UTF-8 og kontrolsekvenser korrekt. Sikkerhed og flow control skal gennemgås mod xterm.js-guides. Kilder: S7–S8.

Serveren resolver target fra en autoriseret operatorallocation. Brugeren kan ikke vælge en anden maskine via et host-felt. En grant bindes til organisation, bruger, lab, operator, generation og udløb. Brug og revokation skal være atomisk. Ved disconnect overlever shellen i den aftalte sessionmanager, eksempelvis tmux, men et reset lukker den og invaliderer genforbindelse til gammel generation.

## Event envelope

```json
{
  "schema_version": 1,
  "event_id": "synthetic-example-event-001",
  "type": "objective.completed",
  "organization_id": "demo-org",
  "class_id": "demo-class",
  "lab_session_id": "demo-lab",
  "generation": 1,
  "actor_id": "demo-operator",
  "observed_at": "2026-10-09T08:00:00Z",
  "received_at": "2026-10-09T08:00:00Z",
  "source": "score-service",
  "evidence_level": "validated",
  "payload": {"objective_id": "identify-http", "points_delta": 100}
}
```

Dette er syntetiske eksempeldata. Serveren tildeler/validerer identiteter, tider og eventtype. Sensorer må ikke få rettighed til at udsende `objective.completed` med autoritativ score. Frontend får rolleafhængige projektioner, ikke hele rå envelope-/payloadindholdet.

Terminaloutput og pakker holdes uden for den normale domæneeventstrøm. Sensorflow-data kan aggregeres/droppes efter aftalte budgetter; point- og lifecycle-events må ikke tabes. Outbox leverer mindst én gang, og forbrugere håndterer deduplikering.

## Scoring

Solo-progression og konkurrencescore er adskilte. Konkurrence starter med faste point. Unik constraint for færdiggørelse/score bindes til competition, team og objective efter de valgte resetregler. Gem forsøg uden at lække rå flags i almindelige logs. Pointkorrektioner er nye historikposter, ikke usynlige databaseændringer.

Deadline og pause styres server-side. Dokumentér modtagelsestidspunkt, tie-break-regel og håndtering af tekniske udfald. Scoreboard-freeze stopper visningen, ikke nødvendigvis valideringen; UI og regelsæt skal forklare forskellen. Genskab scoreboard ud fra scorehistorikken i en test.

## Ressourceplacering

Manifest deklarerer arkitekturkrav, operator-/målressourcer og features som `desktop` eller `raw-network`. Scheduler reserverer hele labbet på en node, og noden validerer det igen mod sin politik. Et tomt memory-felt må ikke betyde ubegrænset hukommelse. Image-reference skal matche en godkendt digest på korrekt arkitektur.

## Versionering

Gem missionversion på enhver session. En ny kursusrelease ændrer ikke en igangværende session under eleverne. Breaking changes i event-/nodekontrakt kræver eksplicit håndtering af gamle klienter/noder. En minimumsversion som ikke er opfyldt giver en forklarlig afvisning, ikke tilfældige runtimefejl.
