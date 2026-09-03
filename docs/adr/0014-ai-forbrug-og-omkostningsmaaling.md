# ADR-0014: AI-forbrug måles fra første kald — én hændelse pr. operation

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** ai-llm
- **Deciders:** Project owner
- **Related:** ADR-0008 (`app/llm/` måler hvert kald), ADR-0009 (opgave → model; cache og
  batch), ADR-0010 (`agent_runs.cost_dkk`, døgnbudget), ADR-0011 (tre logs, tre formål),
  ADR-0003 (`okonomi`-tilladelsen); `docs/adr-plan.md` (bidflow ADR-0082 overføres)

## Kontekst

Bidflow byggede AI-forbrug-viewet, men aldrig opsamlingen: `usage_events` fandtes,
viewet fandtes, og tabellen var tom på produktion i månedsvis (ADR-0082). Fejlen var
ikke teknisk — den var rækkefølge. Målingen blev et senere increment og blev derfor
aldrig et increment.

Her er forudsætningerne anderledes og hårdere: **12 agenter kører hver nat for hver
kunde** (ADR-0010). Forbruget er ikke en nysgerrighed, det er en driftsparameter, der
afgør, om produktets prissætning holder — og ADR-0010's døgnbudget kan ikke håndhæves
uden et tal at måle imod.

Det meste er allerede besluttet i andre ADR'er: `app/llm/` er den eneste udgang og måler
hvert kald (ADR-0008 §2), `agent_runs` bærer kørslens omkostning (ADR-0010 §3), og
ADR-0011 fastslår, at måling ikke hører i auditloggen. Denne ADR fastlægger det, der
mangler: **hændelsens form, prismodellen og hvad tallene bruges til.**

## Beslutning

### 1. Én `usage_events`-række pr. **operation**, ikke pr. LLM-kald

En operation er en brugerhandling eller en agentkørsel — ét copilot-svar, ét
forpligtelses-udtræk for én kontrakt, én fakturakontrol. Består den af fem kald, bliver
det stadig **én** række, med tokens summeret pr. model.

Bidflow nåede samme konklusion (ADR-0082, "pr.-kald-granularitet fravalgt: tusindvis af
rækker for for lidt ekstra indsigt"). Den holder — og her er argumentet stærkere, fordi
batch-kørsler (ADR-0009 §4) ellers ville producere én række pr. kontrakt pr. nat.

| Kolonne | Betydning |
|---|---|
| `organization_id` | RLS niveau 1 fra første migration |
| `occurred_at` | |
| `task` | ADR-0009's opgavenavn (`obligation_extract`, `copilot`, `cert_extract` …) |
| `actor_type`, `user_id` | `human` med bruger for interaktive; `agent` med null for kørsler |
| `contract_id`, `agent_run_id` | attribution — hvad kostede denne kontrakt, denne kørsel |
| `model`, `backend` | `claude-opus-5`, `anthropic`/`vertex_eu` (ADR-0008) |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` | |
| `batch` | bool — halv pris (ADR-0009) |
| `cost_usd`, `cost_dkk` | beregnet ved skrivning, se §2 |

### 2. Prisen beregnes ved skrivning, ikke ved visning

- **Pristabel pr. model i konfigurationen** (`$/1M tokens` ind, ud, cache-læsning,
  cache-skrivning), med batch-rabatten som en faktor. Ved beslutningstidspunktet:
  Opus 5 $5/$25, Sonnet 5 $2/$10, Haiku 4.5 $1/$5 pr. million tokens.
- `cost_usd` beregnes og **gemmes** på rækken. Ændrer udbyderen priser i morgen, står
  gårsdagens tal fast — det er hele forskellen på et regnskab og et estimat.
- `cost_dkk` gemmes ved siden af med den kurs (`DKK_PER_USD`), der gjaldt ved
  skrivningen. Kursen gemmes også, så tallet kan efterprøves.
- Prisændringer er en konfigurationsændring med en dato, ikke en retroaktiv omregning.

### 3. Måling må aldrig vælte det, den måler

Best-effort, som bidflow ADR-0082: en fejl i optælling eller skrivning wrappes, logges
og sluges. En agentkørsel, der lykkes, må ikke fejle, fordi omkostningsrækken ikke kunne
skrives. Omvendt gælder ADR-0010's budgetstop *før* kaldet, ikke efter — den kontrol er
ikke best-effort.

### 4. Hvad tallene bruges til

1. **Døgnbudget pr. organisation** (ADR-0010 §7) — summen af dagens `cost_dkk` pr. org
   er det, loftet måles mod.
2. **Enhedsøkonomi:** kroner pr. kontrakt pr. måned, pr. agent, pr. opgave. Det er tallet,
   der afgør, om abonnementsprisen holder, og om en model skal skiftes (ADR-0009's
   åbne spørgsmål 1 kan først besvares, når det findes).
3. **Cache-effekt:** `cache_read_tokens` mod `input_tokens` viser, om prompt caching
   virker. Er cache-læsningerne nul over gentagne copilot-spørgsmål til samme kontrakt,
   er der en stille invalidator — en fejl, ikke en detalje (ADR-0009 §4).
4. **Batch-andel:** hvor stor en del af nattens forbrug der faktisk gik gennem batch.
   Falder den, er noget faldet tilbage til synkron kørsel til dobbelt pris.

Viewet ligger i Administration bag `okonomi`-tilladelsen (ADR-0003): total, opdeling pr.
opgave og pr. agent, USD og DKK side om side, samt de tre nøgletal ovenfor.

### 5. Rækkefølgen: målingen bygges sammen med det første kald

Den ene beslutning, der adskiller dette fra bidflow: **`app/llm/` må ikke tages i brug,
før den skriver `usage_events`.** Ikke som disciplin, men som accept-kriterium — første
copilot-svar i staging skal have en tilhørende række, ellers er funktionen ikke færdig.

## Diagram — bevidst fravalgt

Skriveflowet er allerede tegnet: ADR-0008's sekvensdiagram viser `usage_events`-skrivningen
som trin i vejen for ét kald, og ADR-0010's flowchart viser kørslen, der aggregeres.
Resten af denne beslutning er én tabel (§1) og en prismodel (§2), som prosaen dækker.
Et tredje diagram ville gentegne de to, der allerede findes. Vurderet og fravalgt.

## Konsekvenser

- Forbruget er kendt fra dag ét i drift, i stedet for at være et tomt view i månedsvis.
- **Én række pr. operation** betyder, at man ikke kan se, hvilket enkeltkald der var dyrt
  inde i en batch. Accepteret: `agent_runs` giver kørselsniveauet, og en dyr enkeltopgave
  findes ved at måle opgavetypen, ikke det enkelte kald.
- Gemte priser betyder, at en prisfejl i konfigurationen forplanter sig til rækkerne,
  indtil den opdages. Derfor en test, der sammenholder en kendt tokenmængde med et
  forventet beløb pr. model.
- Tabellen vokser med antal operationer, ikke med antal kald — håndterbart. Ældre rækker
  aggregeres pr. måned efter ADR-0012's politik.
- Tests/tjek: et copilot-svar giver præcis én række med rigtig opgave, model og bruger;
  en batch-kørsel giver én række med `batch = true` og halv pris; en fejl i
  prisopslaget skriver rækken med `cost_usd = null` frem for at vælte kaldet;
  cache-nøgletallet er ikke-nul i staging efter to spørgsmål til samme kontrakt.

## Alternativer overvejet

- **Pr.-kald-granularitet.** Afvist som i bidflow: tusindvis af rækker pr. nat for
  marginal indsigt.
- **Én blandet tokenpris.** Afvist: Haiku og Opus adskiller sig 5× i pris, og cache-
  læsninger endnu mere. En blandet pris ville gøre enhedsøkonomien ubrugelig.
- **Beregn prisen ved visning** ud fra en aktuel pristabel. Afvist: gør historiske tal
  ustabile og umuliggør en efterprøvning af, hvad noget faktisk kostede.
- **Kun tokens, ingen kroner.** Afvist: kroner er hele formålet — tokens uden pris
  besvarer ikke spørgsmålet, produktet skal kunne svare på.
- **Udled forbrug af auditloggen.** Afvist: præcis bidflows fejl (ADR-0011 §4).

## Afklaringer (2026-09-03, besluttet af project owner)

1. **Selvhostede embeddings får ingen kronepris** i `usage_events` — de koster CPU, ikke
   API. Men antal og varighed måles, så indekseringens belastning kan ses.
2. **Kunden kan se sit eget AI-forbrug** via `okonomi`-rollen. Det understøtter
   gennemsigtighedsløftet og gør en samtale om prisplaner konkret.
3. **Rækker ældre end 12 måneder aggregeres** til månedssummer pr. (org, opgave, model).
   Detaljen er værdiløs efter et år; summen er ikke.
