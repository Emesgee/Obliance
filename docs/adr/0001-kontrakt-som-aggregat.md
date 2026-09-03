# ADR-0001: Kontrakten som aggregat — udbud er en fase, ikke en anden entitet

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** arch
- **Deciders:** Project owner
- **Related:** `docs/adr-plan.md` (N01, K12); bidflow ADR-0040 (dashboard som afledt
  aggregering), ADR-0043 (kanban-komponent); kommende ADR om RLS + fortrolighed (N08)

## Kontekst

Obliance er bygget til **ordregiveren efter tildeling** — Amgros I/S, der indkøber
lægemidler for regionernes sygehusapoteker. Det er den modsatte side af bordet i forhold
til bidflow, hvor *udbuddet* var produktets kerneobjekt og alt hang under `tenders`.

Mockuppen (`contractflow-ai-amgros.html`) viser, at ordregiverens verden er organiseret
omkring **kontrakten**:

- Pipeline-siden lægger kommende udbud (`U-2027-001 · Udbud 2027: SGLT2-hæmmere`) i
  **samme liste og samme datastruktur** som aktive kontrakter (`K-2023-014`,
  `R-2024-1102`), kun med et `fase`-felt sat. Fasen løber Forberedelse → Udbud →
  Evaluering → Kontrahering → Aktiv drift → Genudbud/exit. Et udbud *bliver* en kontrakt
  ved tildeling; det er ikke to poster.
- Alle andre skærme er filtreringer af kontrakten: Forpligtelser, KPI og SLA, Risici,
  Økonomi (fakturaer), Opgaver og RACI bærer alle et `kontraktId`. Overblikket er en
  roll-up af dem.
- Kun to ting lever uden for kontrakten: **leverandøren** (stamdata på tværs af
  kontrakter, med CVR, certifikater og performance) og **brugeren** (ejer, manager,
  ansvarlig).

Mockuppens kontraktfelter, som modellen skal rumme:
`navn, leverandorId, status, type (Serviceaftale/Rammeaftale/Udbud), kategori, afdeling,
ejer, manager, niveau (N1–N4), risiko, vaerdi, aarligVaerdi, budget2026, forbrug2026,
start, slut, opsigelsesvarsel, senesteOpsigelse, optioner, prisregulering,
naesteDeadline, beskrivelse, fortrolighed (Intern/Fortrolig), governance, fase`.

Kravet til modellen er derfor: ét aggregat, der kan bære en post fra "vi overvejer et
udbud" til "kontrakten er udløbet og arkiveret" uden at skifte identitet, og som alle
andre entiteter kan hænge under.

## Beslutning

**Én tabel `contracts` er aggregatroden.** Et udbud er en kontrakt i en tidlig fase —
ikke en separat model. Der findes ingen `tenders`-tabel.

### Livscyklus: to felter, ikke ét

| Felt | Værdier | Betydning |
|---|---|---|
| `phase` | `forberedelse · udbud · evaluering · kontrahering · aktiv_drift · genudbud_exit` | Hvor i **livscyklussen** posten er. Bevæger sig fremad; kan gå tilbage (annulleret udbud → forberedelse). |
| `status` | `kladde · aktiv · udloebet · opsagt · arkiveret` | **Postens** tilstand i systemet. `kladde` indtil Contract Intake er godkendt; `arkiveret` er terminal og kræver `arkiver`-tilladelsen. |

De to er uafhængige: en post kan være `aktiv` i fasen `udbud` (udbuddet kører) og
`kladde` i fasen `aktiv_drift` (kontrakt uploadet, stamdata endnu ikke godkendt).

Mockuppens `type` ("Serviceaftale", "Rammeaftale", "Udbud") blandede aftaleform og fase.
Her skilles de: `agreement_form` er aftaleformen (`serviceaftale · rammeaftale ·
leveringsaftale · databehandleraftale · andet`) og er **nullable** før kontrahering.
"Udbud" er ikke en aftaleform.

### Identitet

- Primærnøgle: UUID.
- **Menneskelig reference** (`reference`, unik pr. organisation): tildeles ved oprettelse
  og **ændres aldrig**, heller ikke når et udbud bliver en kontrakt. Præfiks vælges af
  fasen ved oprettelse: `U-` (oprettet som udbud), `K-` (oprettet som kontrakt), `R-`
  (oprettet som rammeaftale). Format `<præfiks>-<år>-<løbenr>`.
- Konsekvens: `U-2027-001` hedder stadig `U-2027-001`, når den er i drift. Det er
  bevidst — sporbarheden fra udbud til kontrakt er vigtigere end et "pænt" K-nummer.
  Kunder, der vil have et kontraktnummer ved signering, får et **ekstra, valgfrit felt**
  `contract_number` (fritekst, fx journalnummer), ikke en ny reference.

### Kolonner på `contracts`

Stamdata: `organization_id` (RLS), `reference`, `contract_number`, `name`, `description`,
`agreement_form`, `category`, `department`, `phase`, `status`, `tier` (`N1`–`N4`),
`confidentiality` (`intern · fortrolig`), `risk_level` (`lav · mellem · hoej`, **afledt
default** fra godkendte risici, kan overstyres).

Governance: `governance_meetings` (JSONB-liste af `{type, kadence, naeste, deltagere}`,
fx `driftsmoede · kvartal · 2026-10-15 · [CM, LEV, IT]`) + `governance_note` (fritekst
til det, der ikke passer i skemaet). Struktureret fra start, fordi Meeting Preparation
Agent, deadline-motoren (N12) og copiloten ("agenda til næste driftsmøde") alle skal
kunne slå *næste møde* op — en fritekststreng kan ikke læses af systemet.

Relationer: `supplier_id` (nullable indtil kontrahering), `owner_id` (Contract Owner),
`manager_id` (Contract Manager) — begge FK til `profiles`, begge nullable, så
Responsibility Gap Agent har noget at finde.

Tid og vilkår: `start_date`, `end_date`, `notice_period` (interval), `last_termination_date`
(**gemt**, ikke afledt — den står i kontrakten og kan afvige fra `end_date − notice_period`),
`options` (JSONB-liste af `{antal, varighed, varsel_senest}`), `price_regulation`
(fritekst + `price_regulation_date` valgfri).

Økonomi: `total_value` og `annual_value` som `numeric(14,2)` i DKK. **DKK-only i v1**
(besluttet 2026-09-03): intet `currency`-felt; Amgros handler i DKK. Kommer der en
kontrakt i EUR, er det en ny ADR — ikke en stille kolonne. `budget2026`/`forbrug2026` i mockuppen bliver en sidetabel
**`contract_budgets (contract_id, year, budget)`**; **forbrug er afledt** af godkendte
fakturaer pr. år, ikke gemt.

**Afledte felter gemmes ikke.** `naesteDeadline` i mockuppen er den nærmeste af:
`last_termination_date`, optionsvarsler, åbne forpligtelsers deadlines,
certifikatudløb hos leverandøren. Den beregnes i et view/endpoint (samme princip som
bidflow ADR-0040), så den aldrig kan blive forældet.

### Hvad hænger under kontrakten (aggregatets børn)

Alle med `contract_id` FK + `organization_id` (RLS), `ON DELETE RESTRICT` — en kontrakt
med børn slettes ikke, den arkiveres (opbevaringspolitik kommer i egen ADR, N10):

- `contract_documents` — versionerede filer (egen ADR, N05)
- `obligations` — forpligtelser (part, ansvarlig, frekvens, frist, kilde)
- `kpis` + `kpi_measurements` — mål og tidsserie (N19)
- `sla_breaches` — konstaterede brud med beregnet credit (N06)
- `risks` — sandsynlighed × konsekvens, afværgelse, ansvarlig
- `invoices` — fakturaer med kontrol-resultat (N11)
- `tasks` — opgaver med `origin` (polymorf reference til forpligtelse/risiko/brud/faktura)
- `raci_entries` — aktivitet × rolle (N17)

### Stamdata på tværs af kontrakter (ikke børn)

- `suppliers` — leverandør med CVR som naturlig nøgle pr. org (N16)
- `profiles` / `organizations` / `organization_members` — som bidflow

## Konsekvenser

- **Pipeline-siden er en filtrering** (`phase < aktiv_drift`) af samme tabel som
  Kontrakter-siden; kanban-kolonner = `phase`. Ingen synkronisering mellem to modeller,
  ingen "konvertér udbud til kontrakt"-handling — kun en faseændring.
- **Overblikket er en roll-up** af kontrakter og deres børn. Ingen dashboard-tabeller.
- Referencen er stabil gennem hele livet. Prisen er, at et "U-"-præfiks kan stå på en
  aktiv kontrakt; det accepteres, jf. ovenfor.
- `supplier_id`, `owner_id`, `manager_id` er nullable → alle skærme skal tåle
  "ingen leverandør endnu" og "ingen ansvarlig" — det sidste er en *feature*
  (Responsibility Gap).
- Fasemaskinen (hvilke overgange er tilladt, hvem må udføre dem) og statusmaskinen
  bor i **ét servicelag**, ikke i UI'et; overgange auditlogges.
- To ADR'er følger direkte af denne: dokumentversionering (N05) og RLS + fortrolighed
  (N08), fordi `confidentiality` kun har mening, når noget håndhæver den.
- Ikke besluttet her: opbevaring/arkivering (N10), HITL-tilstande på børnene (N02),
  bods-/creditberegning (N06).

## Alternativer overvejet

- **Separat `tenders`-model, der "bliver til" en kontrakt ved tildeling (bidflows model).**
  Afvist: to poster for ét forløb, en konverteringshandling der kan fejle halvvejs, og
  dobbeltregistrering af dokumenter, ansvarlige og kategori. Mockuppen viser tydeligt ét
  forløb.
- **Generisk `agreements`-tabel med subtyper (single-table inheritance på aftaleform).**
  Afvist: aftaleformerne deler >90 % af felterne; forskellene (rammeaftalens
  leveringsgrad, serviceaftalens SLA) lever i KPI'er og forpligtelser, ikke i
  kontraktens kolonner.
- **Alle stamdata i én JSONB-kolonne (som bidflows CompanyProfile, ADR-0021).**
  Afvist: felterne her er stabile, filtreres og sorteres på (fase, niveau, udløb, værdi)
  og bærer forretningsregler (beløbsgrænser). JSONB reserveres til `options` og
  `governance_meetings`, hvor listeformen er det naturlige.
- **Ny reference ved faseskift (U- → K-).** Afvist: bryder links, auditlog og
  eksporter; sporbarhed vægtes højere. Løst med valgfrit `contract_number`.
- **Gem `naeste_deadline` og `forbrug` som kolonner.** Afvist: bliver forældede og
  kræver triggere/jobs for at holde dem sande. Afledning er billig ved denne skala.

## Afklaringer (2026-09-03)

1. **Reference-præfiks følger fasen ved oprettelse og er uforanderligt** — en aktiv
   kontrakt må gerne hedde `U-…`. Besluttet af project owner.
2. **DKK-only i v1.** Intet `currency`-felt. Besluttet af project owner.
3. **Governance struktureret fra start** (`governance_meetings` JSONB + `governance_note`),
   jf. afsnittet ovenfor. Anbefalet; sættes til Accepted sammen med resten, medmindre
   der er indvendinger.
