# ADR-0011: Uforanderlig auditlog — append-only, tre aktørtyper, adskilt fra måling

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** auth-access
- **Deciders:** Project owner
- **Related:** ADR-0002 (RLS; Auditor-rollen læser alt), ADR-0003 (`audit`-tilladelsen,
  `approvals`), ADR-0004 (forslag og beslutninger), ADR-0006 (versionsskift),
  ADR-0008 (AI-forespørgsler logges før kaldet), ADR-0010 (`agent_runs`);
  `docs/adr-plan.md` (N09, K07); bidflow ADR-0082 (aktivitetslog ≠ forbrugsmåling),
  ADR-0068 (superadmin-adgang auditlogges); kommende ADR om opbevaring (N10)

## Kontekst

Mockuppen sælger auditloggen som en garanti, ikke som en funktion:

> **Fuld sporbarhed** — Både agenternes fund og dine beslutninger registreres i en
> **uforanderlig auditlog** med tid, bruger og objekt.

Skærmen viser rækker med `ts, bruger, handling, objekt`, hvor "bruger" er tre
forskellige slags aktører: mennesker (`Stefán Holm · Dokument uploadet`), systemet
(`System · Notifikation sendt`, `System · ERP-integration · Fakturaer synkroniseret`) og
agenter (`AI · Risk Agent · AI-forslag oprettet`, `AI · Invoice Compliance Agent ·
AI-analyse gennemført`). Der findes en **Auditor**-rolle, hvis eneste tilladelser er
`eksport` og `audit` (ADR-0003) — altså en bruger, hvis hele formål er at læse denne
tabel.

Bidflows `ActivityLog` var en almindelig tabel med almindelige rettigheder: appen kunne
opdatere og slette i den. Det var tilstrækkeligt for et internt værktøj; det er det
ikke, når produktet lover uforanderlighed til en offentligt ejet kunde, hvis egen
revision skal kunne bruge loggen.

Bidflow lærte også en ting, der er værd at gentage her: **aktivitetslog og
forbrugsmåling er ikke samme tabel** (ADR-0082). Brugeren så tunge AI-kørsler i
`activity_log`, mens AI-forbrug-viewet stod tomt, fordi `usage_events` blev fyldt af
ingenting. To logs, to formål, to opsamlinger.

## Beslutning

### 1. `audit_log` er append-only, håndhævet i databasen

- Tabel `audit_log` med `organization_id` (RLS niveau 1 fra første migration, jf.
  bidflow ADR-0074's lærdom om glemte tabeller).
- **App-rollen har `INSERT` og `SELECT` — ikke `UPDATE`, ikke `DELETE`.** Rettighederne
  gives i migrationen, ikke i kode. En udvikler, der skriver `audit_log.query().delete()`,
  får en databasefejl, ikke et resultat.
- Sletning sker **kun** gennem opbevaringspolitikken (ADR-0012), som kører som en
  særskilt rolle med et særskilt formål — ikke som appen.

### 2. Rækkens form

| Kolonne | Betydning |
|---|---|
| `id`, `occurred_at` | tidspunkt i UTC; vises i dansk lokaltid |
| `actor_type` | `human · agent · system` — de tre slags i mockuppen |
| `actor_id` | `profiles.id` for mennesker; null for agent/system |
| `actor_label` | frosset visningsnavn: `Stefán Holm`, `AI · Risk Agent`, `System · ERP-integration` |
| `actor_role` | rollen **på handlingstidspunktet** (ADR-0003) |
| `action` | kode fra en fast taksonomi (se §3) |
| `object_kind`, `object_id`, `object_label` | fx `obligation`, id, `F-102 Varsling af forlængelsesoption (K-2023-014)` |
| `contract_id` | når handlingen vedrører en kontrakt (RLS niveau 2, ADR-0002) |
| `details` | JSONB: beløb, før/efter for feltændringer, begrundelse ved afvisning |
| `request_id`, `agent_run_id` | korrelation til HTTP-request og til ADR-0010's kørsel |

**`actor_label`, `actor_role` og `object_label` fryses ved skrivning.** En bruger, der
skifter navn eller rolle, ændrer ikke historikken; en kontrakt, der omdøbes, gør heller
ikke. Det er hele pointen med en log frem for et join.

### 3. Hvad der logges — en lukket taksonomi

`action` er en enum, ikke fritekst, så loggen kan filtreres og eksporteres pålideligt.
Ved første version:

- **Adgang:** login, mislykket login, MFA-nulstilling, invitation sendt/accepteret,
  medlem deaktiveret, rolle ændret, **superadmin-adgang** (ADR-0002/K07 — i *kundens*
  log, ikke kun i vores).
- **Kontrakt:** oprettet, stamdata ændret (før/efter i `details`), fase ændret, status
  ændret, arkiveret, fortrolighed ændret, adgang tildelt/tilbagekaldt.
- **Dokument:** uploadet, version gjort gældende (med `sha256`), OCR kørt.
- **AI:** forslag oprettet (agent), forslag godkendt, forslag afvist **med begrundelse**,
  forslag forældet, AI-forespørgsel (copilot, før kaldet — ADR-0008), agentkørsel
  gennemført/fejlet.
- **Beslutninger med penge:** bodskrav rejst, service credit godkendt, faktura afvist,
  kreditnotakrav sendt — hver med beløb og med **begge** signaturer, når
  beløbsgrænsen krævede to (ADR-0003).
- **Governance:** RACI godkendt, ansvarlig ændret, KPI-måling registreret.
- **Data:** rapport eksporteret (ADR-0003's `eksport`), GDPR-eksport, sletning
  igangsat/gennemført, legal hold sat/ophævet (ADR-0012).

Nye handlinger tilføjes til enum'en i en migration — bevidst friktion, så taksonomien
ikke skrider.

### 4. Tre logs, tre formål — bland dem ikke

| Tabel | Formål | Skrives af | Læses af | Opbevaring |
|---|---|---|---|---|
| `audit_log` | **Hvem gjorde hvad** — revisionsspor | alle mutationer | Auditor, kunde, revision | ADR-0012's politik (lang) |
| `agent_runs` (ADR-0010) | **Hvordan kørte agenten** — drift | scheduler/worker | operatør, AI-agenter-skærmen | kort (måneder) |
| `usage_events` (bidflow ADR-0082) | **Hvad kostede det** — økonomi | `app/llm/` | operatør, fakturering | mellem (år, aggregeret) |

At skrive en agentkørsel i `audit_log` ville drukne menneskernes beslutninger i
maskinstøj; at udlede omkostning af `audit_log` ville gentage bidflows fejl. Én
handling kan optræde i to af dem — en copilot-forespørgsel er både en auditrække og en
`usage_events`-række — men de skrives hvert sit sted, med hvert sit skema.

### 5. Læsning, filtrering og eksport

- Auditloggen er **RLS-scoped som alt andet**: en kunde ser kun sin egen. Auditor-rollen
  ser alle kontrakter i sin org, inklusive fortrolige (ADR-0002's afklaring 3).
- Skærmen filtrerer på fritekst (som mockuppen), plus aktørtype, handling, objekt og
  periode.
- **Eksport** (CSV/JSON) kræver `eksport`; eksporten logges selv. En revisor får dermed
  et udtræk, der indeholder sit eget udtræk — hvilket er korrekt.
- Superadmin-adgang (bidflow ADR-0068) skrives i **kundens** log med `actor_type =
  human` og operatørens navn, så kunden kan se, at vi har været inde.

### 6. Manipulationsbevis: forberedt, ikke bygget

Append-only rettigheder beskytter mod appen og mod uheld, ikke mod en, der har
databaseadgang. Det næste niveau er en **hash-kæde** (hver række hasher forrige rækkes
hash + eget indhold), der gør en efterfølgende ændring synlig.

Beslutning: **kolonnerne `prev_hash` og `row_hash` oprettes nu og fyldes fra dag 1**,
men verifikationsværktøjet og den formelle garanti udskydes, til en kunde beder om den.
Grunden til at fylde dem alligevel er, at en hash-kæde ikke kan påføres bagud — starter
vi ikke nu, dækker den aldrig de første års historik.

## Diagram — bevidst fravalgt

Beslutningen er én tabel med ét skrive-mønster og et sæt rettigheder. Datadimensionen er
kolonnetabellen i §2; den vigtige skelnen mellem de tre logs er sammenligningstabellen i
§4, hvor tabelformen er stærkere end bokse og pile, fordi læseren skal sammenligne fire
egenskaber på tværs af tre ting. Der er ingen proces at vise: "skriv en række ved hver
mutation" er ét trin, og de flows, der *fører* til rækkerne, er allerede tegnet i
ADR-0004 (forslagets livscyklus), ADR-0006 (versionsskift) og ADR-0010 (agentkørsel). Et
diagram her ville gentegne noget, der allerede står klarere andetsteds. Vurderet og
fravalgt.

## Konsekvenser

- **"Uforanderlig" bliver sandt på det niveau, en app kan gøre det:** appen kan
  hverken rette eller slette. At det ikke er et kryptografisk bevis, siger vi højt —
  §6 er forberedelsen, ikke et løfte.
- **Frosne labels betyder redundans** (navnet står i hver række). Det er med vilje:
  en log, der viser dagens navne på gårsdagens handlinger, er ikke et revisionsspor.
- **Enum-taksonomien koster en migration pr. ny handling.** Den friktion er formålet.
- Auditlogningen skal ske i **samme transaktion** som handlingen, ellers kan en
  handling lykkes uden spor. Undtagelsen er ADR-0008's AI-forespørgsel, som logges
  *før* kaldet — der er det vigtigere at vide, at nogen spurgte, end at vide, at de fik
  svar.
- Tabellen bliver den største i systemet efter dokumenttekst. Indeks på
  `(organization_id, occurred_at DESC)` og `(contract_id, occurred_at DESC)`; partitionering
  pr. år hvis den vokser ud af det. Opbevaringen fastlægges i ADR-0012 — en auditlog, der
  slettes efter 90 dage, er ikke det, mockuppen lover.
- Tests/tjek: app-rollen kan ikke `UPDATE`/`DELETE` i `audit_log` (rå SQL-test);
  en afvisning uden begrundelse skriver ingen række, fordi handlingen selv afvises
  (ADR-0004); superadmin-adgang optræder i kundens log; en rolleændring efter en
  handling ændrer ikke `actor_role` på den gamle række; eksport logger sig selv.

## Alternativer overvejet

- **Almindelig tabel med app-rettigheder (bidflows `ActivityLog`).** Afvist: produktet
  lover uforanderlighed, og et løfte, der kun holder så længe ingen skriver den forkerte
  linje kode, er ikke et løfte.
- **Ekstern, append-only log-tjeneste (WORM-storage, ledger-database).** Afvist for v1:
  tilføjer en underdatabehandler og en driftskomponent til ADR-0007's bevidst enkle stak,
  for en garanti, ingen kunde endnu har bedt om. Hash-kæden er den billige forberedelse.
- **Log alt, også agentkørsler og tokenforbrug, i én tabel.** Afvist: bidflow ADR-0082
  viser præcis den forvirring — og en revisor, der skal finde menneskelige beslutninger
  i millioner af maskinrækker, er ikke hjulpet.
- **Join til `profiles` og `contracts` i stedet for frosne labels.** Afvist: viser
  nutidens navne på fortidens handlinger, og går i stykker, når et medlem deaktiveres
  (bidflow ADR-0067) eller en kontrakt arkiveres.
- **Hash-kæde fuldt bygget nu (verifikation, ankring, alarm).** Fravalgt som scope, ikke
  som idé: kolonnerne fyldes fra dag 1, netop fordi kæden ikke kan påføres bagud.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **Mislykkede login-forsøg logges i auditloggen** — det er et sikkerhedsspor, kunden
   selv skal kunne se, og volumenet er lavt bag MFA.
2. **Læsninger logges kun for fortrolige kontrakter og for eksporter.** Fuld
   læselogning ville fordoble tabellen for lav værdi; adgang til det fortrolige er
   præcis det, en revision spørger til.
3. **Hash-kolonnerne (`prev_hash`, `row_hash`) fyldes fra dag 1**, selv om
   verifikationsværktøjet udskydes — kæden kan ikke påføres bagud.

## Implementeringsnote (2026-09-04, første increment)

`audit_log` er oprettet med §2's kolonner, enum-taksonomien fra §3 (første 14
handlinger) og §1's rettigheder: app- og worker-rollen har `INSERT` + `SELECT`, og en
test bekræfter, at `UPDATE`/`DELETE` afvises af Postgres. `prev_hash`/`row_hash`
fyldes fra første række (§6; verifikationsværktøjet er ikke bygget). Skrivning sker
gennem `app/core/audit.py` med frosne labels og tre aktørtyper. Første skrivende
handlinger: `ai_query`, `ai_suggestion_created/approved/rejected/expired`,
`agent_run_completed/failed`, `contract_updated`, `contract_status_changed`.
`GET /api/contracts/{id}/audit` findes bag `audit`; auditskærmen er ikke bygget.

**Andet increment (2026-09-04):** `login` og `login_failed` (afklaring 1; kun når
e-mailen tilhører et kendt medlem, ellers findes ingen org at skrive i),
`document_uploaded` og `document_version_made_current` (med `sha256`), samt
`obligation_created/updated/status_changed` og `citations_reresolved` er koblet på.
Nye handlinger tilføjes til enum'en i en migration (§3): 0005 gjorde det med
`ALTER TYPE … ADD VALUE`.
