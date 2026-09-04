# ADR-0019: KPI- og måledata — strukturerede mål, målinger som fakta, status som afledning

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** finance
- **Deciders:** Project owner
- **Related:** ADR-0001 (`kpis` + `kpi_measurements` som børn), ADR-0004 (målinger fra
  dokumenter er forslag), ADR-0005 (målet har en kilde; målingen har en kilde),
  ADR-0009 (`kpi_parse` på Haiku; status beregnes i kode), ADR-0013 (brud → beregning
  automatisk), ADR-0015 (grå som fjerde tilstand), ADR-0017 (målingsfrister som
  forpligtelser), ADR-0018 (fakturaer som datakilde); `docs/adr-plan.md` (N19)

## Kontekst

Mockuppens KPI'er: `navn, maal ("≥ 99,8 %"), enhed, status (grøn/gul/rød/grå), aktuel,
kilde, historik[{m, v}]`. Seks KPI'er, fem datakilder:

| KPI | Mål | Kilde til målet | Kilde til målingen |
|---|---|---|---|
| Oppetid, kritiske systemer | ≥ 99,8 % | Hovedkontrakt, s. 12, pkt. 8.2 | leverandørens driftsrapport |
| P1-løsningstid inden for 4 timer | ≥ 95 % | Hovedkontrakt, s. 14, pkt. 9.1 | driftsrapport |
| Leveringsgrad (rammeaftale) | ≥ 98,5 % | Rammeaftale, s. 7, pkt. 5.1 | *"opgøres af Amgros på baggrund af data fra sygehusapotekernes bestillingssystem"* |
| Økologiprocent | ≥ 60 % | Kontrakt, s. 6, pkt. 4.4 | halvårlig rapport |
| Oppetid, WAN | ≥ 99,9 % | Kontrakt, bilag 2 | **"Data mangler"** → grå |

Tre ting følger:

1. **Målet er en klausul** med en kilde — samme mønster som bodsparametre (ADR-0013).
   "≥ 99,8 %" er en operator, en værdi og en enhed, ikke en streng.
2. **Målingen kommer udefra**, fra dokumenter (rapporter), fra filer (Excel/CSV, som
   mockuppen selv nævner), fra mennesker (manuel registrering) og — for leveringsgraden —
   fra kundens eget system. Den er et **faktum om en periode**, ikke en vurdering.
3. **Grå er en tilstand, ikke en fejl.** *"grå = data mangler. Målinger kan registreres
   manuelt eller importeres fra Excel/CSV."* Et system, der viser grønt, fordi der ikke er
   noget rødt at vise, lyver.

Og én kobling, der allerede er besluttet: når en godkendt måling bryder et mål,
oprettes et `beregnet` krav automatisk (ADR-0013's afklaring 3). Denne ADR fastlægger,
hvad "godkendt måling" og "bryder et mål" betyder.

## Beslutning

### 1. KPI-definitionen er struktureret og har en kilde

**`kpis`** (barn af kontrakten): `name`, `unit` (`pct · antal · timer · dkk · score`),
**`target_operator`** (`>= · <= · = · mellem`), **`target_value`** (og `target_value_high`
for `mellem`), `period` (`maaned · kvartal · halvaar · aar`), **`warn_band`** (afstand fra
mål, der giver gul — default 1 procentpoint for `pct`, ellers 5 % relativt),
`citation_id` (ADR-0005: målet står i pkt. 8.2), `penalty_term_id` (nullable — hvilken
bodsklausul et brud udløser, ADR-0013), `measurement_obligation_id` (nullable — se §4).

Målet udtrækkes af `obligation_extract`/`kpi_parse` som et **forslag** (ADR-0004) fra
klausulen, ligesom bodsparametre. Et menneske godkender det én gang pr. dokumentversion.
"≥ 99,8 %" som fritekst findes ikke i skemaet — kun i visningen, hvor det genereres af
`target_operator` + `target_value` + `unit`.

### 2. Målinger er fakta med kilde og periode

**`kpi_measurements`**: `kpi_id`, **`period_start`, `period_end`** (skal matche KPI'ens
`period`), `value`, `source_kind` (`manual · import · document · integration`),
`citation_id` (ADR-0005: `document` → dokumentversion + side; `integration`/`import` →
`record`-citation til filen/posten), `entered_by`, `approved_by`, `approved_at`, `note`.

Vejene ind, og hvem der godkender:

| source_kind | Hvordan | Godkendelse |
|---|---|---|
| `document` | `kpi_parse` (ADR-0009) læser en uploadet rapport og foreslår `(periode, værdi, kilde)` | forslag (ADR-0004) → menneske med `hitl` |
| `import` | CSV/Excel via samme adapter-mønster som ADR-0018, fast skema `(kpi_ref, periode, værdi)` | direkte, `source_kind = import`, filen gemmes som bevis; uploaderen er `approved_by` |
| `manual` | bruger med `kontraktRed` eller kontraktens manager indtaster | direkte; auditlogges (ADR-0011) |
| `integration` | fx leveringsgrad fra kundens bestillingssystem — **adapter-interface, ingen konkret leveret i v1** | direkte fra kilden; kilden er `approved_by` |

**Præcis én måling pr. (kpi, periode).** En ny værdi for samme periode **erstatter** med
`supersedes_measurement_id` og begrundelse — historikken består (ADR-0011), og en
korrektion udløser genberegning af et eventuelt krav (ADR-0013's "genberegn"-knap, nu
automatisk).

### 3. Status er en afledning — og grå er første regel

Status gemmes aldrig. Den beregnes i kode fra seneste **godkendte** måling for den
**aktuelle** periode:

| Regel (i rækkefølge) | Status |
|---|---|
| Ingen godkendt måling for indeværende eller forrige periode | **grå** — *data mangler* |
| Seneste måling er ældre end to perioder | **grå** — *forældet* (vises med dato) |
| Målet opfyldt, afstand ≥ `warn_band` | **grøn** |
| Målet opfyldt, afstand < `warn_band` | **gul** — *tæt på grænsen* |
| Målet ikke opfyldt | **rød** |

`mellem`-mål (fx en temperatur, der skal ligge i et interval) bruger samme regler mod
nærmeste grænse. Reglen er den samme for alle KPI'er; `warn_band` er det eneste, en
kunde justerer.

Grå tæller på Overblikket som **"KPI'er uden data"** — et tal, der skal ned, ikke et, der
skjules. Mockuppens grå WAN-oppetid er præcis den tilstand.

### 4. Målingsfrister er forpligtelser, ikke en ny fristtype

Mockuppen har allerede *"Kvartalsvis driftsrapport"* som **forpligtelse** (F-103,
leverandørens, kvartalsvis, kilde pkt. 10.2). Beslutning: en KPI's `measurement_
obligation_id` peger på den forpligtelse, der leverer målingen. Dermed:

- En manglende rapport er en **forsinket forpligtelse** — synlig, varslet (ADR-0017's
  kind `forpligtelse`) og med konsekvens ("påkrav og evt. tilbagehold i vederlag", som
  mockuppen skriver). ADR-0017's lukkede enum udvides ikke.
- Når rapporten uploades og målingen godkendes, opfyldes forpligtelsen for perioden.
- KPI'er uden en tilknyttet forpligtelse (leveringsgrad fra kundens eget system) har
  ingen leverandørfrist — deres grå status er kundens eget ansvar, og det vises sådan.

### 5. Brud er deterministisk og udløser ADR-0013

Når en måling **godkendes**, sammenlignes den i kode med målet. Ikke opfyldt →
**`sla_breaches`**-række `(kpi_id, measurement_id, period, target, actual, penalty_term_id)`
— og hvis `penalty_term_id` er sat og parametrene er godkendt, oprettes et `beregnet`
krav (ADR-0013) i samme transaktion. Er parametrene *ikke* godkendt, oprettes bruddet
uden krav og en opgave: "SLA-brud registreret, bodsklausul mangler godkendte parametre".

Et brud er et faktum om en periode og gemmes, også hvis kravet senere frafaldes.
Mockuppens `SLA-B1` (*"Oppetid juli: 99,62 % (krav 99,8 %) → credit 30.625 kr."*) er
præcis denne kæde: måling → brud → krav.

Ingen model er involveret i sammenligningen. `kpi_parse` læser rapporten; koden
afgør, om målet er nået.

### 6. Historik og visning

Historikken *er* `kpi_measurements` — mockuppens `historik[{m, v}]` er en forespørgsel,
ikke et felt. Grafen viser målet som en linje, målingerne som punkter, erstattede
målinger som udgråede punkter, og grå perioder som huller — ikke som nul. En kurve, der
dykker til 0 %, fordi en rapport mangler, er en løgn i grafisk form.

## Diagram — bevidst fravalgt

Beslutningen har to halvdele, og begge er dækket bedre andetsteds end af et nyt
diagram: **nedstrøms** (måling → brud → krav → godkendelse) er ADR-0013's flowchart, som
denne ADR blot fodrer med sit første trin; **statuslogikken** er en beslutningstabel
(§3), og en beslutningstabel i rækkefølge er sværere at læse som forgreninger end som
fem rækker. Vejene ind (§2) er en tabel af samme grund. Et diagram ville gentegne
ADR-0013 med ét ekstra felt foran. Vurderet og fravalgt.

## Konsekvenser

- **Grå bliver et tal, kunden ser** — "KPI'er uden data" på Overblikket. Det er ubehageligt
  og korrekt; en KPI-side, der er grøn, fordi ingen har målt noget, er værre.
- **Mål og målinger har hver sin kilde**, så et brud kan forsvares i to led: "kontrakten
  siger 99,8 % (pkt. 8.2), rapporten siger 99,62 % (Driftsrapport Q3, s. 4)". Det er
  det, ADR-0013's beregningsgrundlag citerer.
- **Én måling pr. periode med erstatning** betyder, at en leverandørs "korrigerede tal"
  aldrig overskriver stille. Korrektionen er synlig, begrundet og udløser genberegning.
- Integration til kundens egne systemer (leveringsgrad fra bestillingssystemet) er et
  interface, ikke en leverance. Indtil den findes, er leveringsgraden `import` eller
  `manual` — og det er, hvad mockuppens tekst selv lægger op til.
- At målingsfrister er forpligtelser genbruger ADR-0017 uden at røre enum'en, og gør
  "manglende rapport" til noget, der allerede har en konsekvens i kontrakten.
- Tests/tjek: KPI uden måling i to perioder er grå, ikke grøn; en måling inden for
  `warn_band` er gul; en godkendt måling under målet opretter `sla_breaches` og et
  `beregnet` krav, når parametrene er godkendt, og en opgave, når de ikke er; en ny
  måling for samme periode erstatter med begrundelse og genberegner kravet; en grå
  periode vises som hul i grafen, ikke som nul; `kpi_parse`-forslag uden verificeret
  kilde vises med advarsel (ADR-0005).

## Alternativer overvejet

- **Mål som fritekst ("≥ 99,8 %"), sammenlignet af modellen.** Afvist: en sammenligning,
  der kan give to svar, kan ikke udløse et krav. Målet er tre felter; koden sammenligner.
- **Gem status på KPI'en og opdatér den ved ny måling.** Afvist: ADR-0001's princip om
  afledte felter — status ville stå grøn efter en periode uden data.
- **Behandl manglende data som "ikke opfyldt" (rød).** Afvist: straffer leverandøren for
  kundens manglende registrering og gør rød meningsløs. Grå er sin egen tilstand.
- **Lad `kpi_parse` skrive målinger direkte** (springe forslaget over for
  dokumentkilder). Afvist: ADR-0004 — og en leverandørs rapport er ikke-betroet input
  (ADR-0016); tallet skal ses af et menneske, før det kan udløse en bod.
- **En ny fristtype `maaling` i ADR-0017.** Afvist: forpligtelsen findes allerede i
  mockuppens egen datamodel; en ny type ville dublere den.
- **Tillad flere målinger pr. periode og vis gennemsnittet.** Afvist: gør "hvilket tal
  udløste bruddet?" ubesvarligt. Én måling, erstatning med spor.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **`warn_band` default 1 procentpoint for `pct`-mål**, justerbart pr. KPI. Gul betyder
   *opfyldt, men inden for båndet fra grænsen*: for `>= 99,8 %` er `[99,8 ; 100,8)` gul,
   `>= 100,8` grøn, `< 99,8` rød. For `<=`-mål spejlvendt.
2. **`import`-målinger kræver ikke ekstra godkendelse.** Uploaderen har `okonomi` eller
   `kontraktRed` og er selv `approved_by`; filen gemmes som bevis.
3. **En erstattet måling frafalder ikke automatisk et `fremsat` krav.** Et fremsat krav
   er ude af huset (ADR-0013); systemet opretter en opgave "grundlaget for krav X er
   ændret", og et menneske frafalder.

## Implementeringsnote (2026-09-04, første increment)

Bygget: `kpis` og `kpi_measurements` (migration 0007) som §1/§2 foreskriver — målet er
operator + værdi + enhed + periode, målingen er et faktum om en periode med præcis én
levende række pr. (kpi, periode) og erstatning med begrundelse; status beregnes i
`app/finance/kpi_status.py` efter §3's fem regler med grå som første; `warn_band` default
1 procentpoint (afklaring 1). Veje ind: `manual` (kontrakt_red/okonomi, direkte) og
`document` (KPI/SLA Agent læser en rapport, `kpi_parse` på Haiku, og foreslår målinger,
som `hitl` godkender). Godkendelse sammenligner i kode og skriver `sla_breaches` og et
`beregnet` krav i samme transaktion (§5); uden godkendte parametre står bruddet med en
note. En erstattet måling frafalder et åbent krav og lader et fremsat stå med en note
(afklaring 3). Ikke bygget: `import` (CSV/Excel) og `integration`, `measurement_
obligation_id`-koblingen til forpligtelser i UI'et, grafen (§6) — historikken vises som
liste.
