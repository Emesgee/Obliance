# ADR-0009: Model pr. opgave bag én abstraktion — og embeddings i egen drift

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** ai-llm
- **Deciders:** Project owner
- **Related:** ADR-0004 (forslag skal være skemagyldige), ADR-0005 (citater verificeres
  lokalt), ADR-0007 (worker-container), ADR-0008 (serverside adgang, ZDR, residens);
  `docs/adr-plan.md` (N15, K02, K11); bidflow ADR-0003 (én udbyder til alt — afløses),
  ADR-0035 (én kilde til defaulten), ADR-0014 (gate ændringer på målte tal), ADR-0063
  (skift ikke embeddings uden målt gevinst), ADR-0082 (omkostningsmåling)

## Kontekst

Bidflow ADR-0003 valgte "OpenAI til alle AI-stadier" for operationel enkelhed og blev
overhalet internt inden et kvartal (Claude til aux-stadier, OpenAI til motoren) —
genbrugsvurderingen kalder den allerede fravalgt. Mockuppen går den modsatte vej og
hardkoder **én** model i klienten (`claude-sonnet-4-6`), men beskriver samtidig **12
agenter** med vidt forskellige opgaver:

- **Tunge, konsekvensfyldte udtræk:** Obligation Extraction (en overset forpligtelse er
  en juridisk eksponering), RACI Design, Risk Agent.
- **Billige, høj-volumen kontroller:** Compliance Agent (certifikatdatoer),
  Renewal & Exit (datoer), Supplier Performance (opsummering af tal).
- **Ren regning og regler:** Workload & Capacity, Responsibility Gap — som slet ikke
  behøver en model.
- **Lang kontekst med citatpligt:** Contract Copilot over portefølje og dokumenter.
- **Syn:** OCR af scannede, underskrevne kontrakter.

Én model til alt betyder derfor enten at betale Opus-pris for at læse en dato ud af et
certifikat, eller at læse forpligtelser med en model, der er for let til opgaven.

Tre tekniske forhold fra den aktuelle API (verificeret 2026-09-03) former beslutningen:

1. **Sampling-parametre er væk.** `temperature`, `top_p` og `top_k` afvises med 400 på
   de aktuelle modeller. Bidflows determinisme-opskrift ("temp 0, seed 42", ADR-0003)
   findes ikke længere. Reproducerbarhed skal komme et andet sted fra.
2. **Thinking styres med `effort`, ikke med et token-budget.** `thinking: {type:
   "adaptive"}` plus `output_config.effort` (`low` → `max`); `budget_tokens` er fjernet.
3. **Anthropic har ingen embeddings-endpoint.** Vektorindekset (ADR-0002) kan ikke
   forsynes af den udbyder, der leverer generering.

Dertil ADR-0008's ZDR-default, som har en konkret konsekvens for modelvalget: Anthropics
**Covered Models** (Fable 5.1/5, Mythos 5.1/5) kræver 30 dages opbevaring og er *ikke*
tilgængelige under ZDR uden særlig godkendelse. ZDR-defaulten udelukker dem dermed —
et eksempel på, at residens- og retentionsvalg begrænser modelmenuen.

## Beslutning

### 1. Én abstraktion, opgave ind, model op af konfigurationen

`app/llm/` (ADR-0008) eksponerer `run(task, context) -> ValidatedResult`. Kaldsteder
navngiver **opgaven**, aldrig modellen. Opgave → model, effort og skema slås op i én
konfigurationstabel, der er **eneste kilde** (bidflow ADR-0035, hævet fra "én default"
til "én tabel pr. rolle"):

| Opgave | Model | Effort | Hvorfor |
|---|---|---|---|
| `obligation_extract` | `claude-opus-5` | `high` | Recall er den dyre akse (K11); en overset forpligtelse koster mere end alle tokens tilsammen |
| `raci_design` | `claude-opus-5` | `high` | Ræsonnement over roller og ansvar, foreslås til Legal-godkendelse |
| `risk_assess` | `claude-opus-5` | `medium` | Vurdering med begrundelse og kilde |
| `contract_intake` | `claude-opus-5` | `medium` | Stamdata fra hele dokumentet; fejl her forplanter sig til alle skærme |
| `copilot` | `claude-opus-5` | `medium` | Lang kontekst, citatpligt, blandet SQL/RAG-kontekst (K10) |
| `invoice_check` | `claude-sonnet-5` | `medium` | Udtræk af prislinjer; **beregningen er kode** (N06) |
| `meeting_prep` | `claude-sonnet-5` | `low` | Udkast til agenda ud fra RACI og åbne punkter |
| `draft_letter` | `claude-sonnet-5` | `medium` | Udkast til påkrav/rykker, altid til menneskelig redigering |
| `ocr_page` | `claude-sonnet-5` | `low` | Vision-transskription (bidflow ADR-0023) |
| `cert_extract` | `claude-haiku-4-5` | `low` | Certifikattype + udløbsdato ud af et dokument |
| `kpi_parse` | `claude-haiku-4-5` | `low` | Målinger ud af en leveringsrapport; status beregnes i kode |
| `supplier_summary` | `claude-haiku-4-5` | `low` | Opsummering af tal, der allerede er beregnet |
| `renewal_scan` | *(ingen model)* | — | Datologik i SQL |
| `responsibility_gap` | *(ingen model)* | — | Regelmotor: aktiviteter uden A, fratrådte ansvarlige (N17) |
| `workload_capacity` | *(ingen model)* | — | Optælling pr. medarbejder |

Priser pr. million tokens (ind/ud) på beslutningstidspunktet: Opus 5 **$5/$25**,
Sonnet 5 **$2/$10**, Haiku 4.5 **$1/$5**. Tabellen er derfor også et
omkostningsdesign: de tre høj-volumen-natlige opgaver ligger på den billigste model, og
Opus bruges hvor konsekvensen af en fejl er juridisk.

**Tre agenter bruger slet ingen model.** Det er en del af beslutningen, ikke en
udeladelse: Responsibility Gap, Workload & Capacity og Renewal & Exit er regler og
datoer. En LLM ville gøre dem dyrere, langsommere og mindre pålidelige, og mockuppens
egne beskrivelser af dem indeholder ingen sprogforståelse.

`LLM_MODEL_<TASK>` i miljøet kan overstyre én opgave (fejlsøgning, pilotmåling), men
konfigurationstabellen er defaulten, og **ingen** model-id må stå i kaldskoden.

### 2. Embeddings i egen drift, i EU

En **selvhostet, flersproget embedding-model** kører i worker-containeren (ADR-0007) på
CPU. Begrundelse:

- **Residens:** embeddings ville ellers sende *hele* dokumentbestanden til en udbyder,
  ikke kun de uddrag et enkelt spørgsmål kræver. Egen drift betyder, at ADR-0008's
  EU-løfte gælder for indekseringen uanset backend.
- **Dansk:** modellen skal forstå dansk juridisk sprog; en flersproget model er
  nødvendig uanset udbyder.
- **Økonomi:** indeksering er en høj-volumen, gentaget operation (hver
  dokumentversion, ADR-0006). Pr.-kald-pris skalerer dårligt her; CPU-tid gør ikke.
- **Uafhængighed:** genereringsudbyderen kan skiftes (ADR-0008) uden at re-indeksere
  alt.

Konkret model, dimensionalitet og normalisering fastlægges ved en **målt** sammenligning
på dansk kontrakttekst før første kunde — og bidflow ADR-0063's lærdom står: **skift
aldrig embeddings uden en målt gevinst**, fordi et skift kræver fuld re-indeksering.
Indtil målingen er kørt, er valget "en flersproget open-weight model på CPU", ikke et
navn i denne ADR.

### 3. Reproducerbarhed uden temperature

Determinisme kan ikke længere købes med `temperature=0`. Erstatningen er tre ting:

1. **Strukturerede outputs med `strict`** på alt maskinlæsbart (alle `ai_suggestions`,
   ADR-0004). Skemaet garanterer formen; modellen må ikke improvisere felter.
2. **Lokal verifikation af indhold:** citater lokaliseres i dokumentet (ADR-0005),
   beløb beregnes i kode (N06). Det, der kan tjekkes, tjekkes — i stedet for at man
   håber på et deterministisk kald.
3. **Guldsæt og målte gates** (bidflow ADR-0013/0014): recall og precision pr. opgave
   måles mod et menneskeligt guldsæt af forpligtelser. Et modelskifte, en
   effort-ændring eller en promptændring på `obligation_extract` kræver en grøn
   guldsæt-måling først — ikke en fornemmelse.

### 4. Cache og batch som førsteklasses del af designet

- **Prompt caching:** kontraktens gældende aftalegrundlag er en stabil prefix og
  cachees; spørgsmålet og de varierende uddrag placeres efter sidste cache-brud.
  Copiloten er den store vinder (samme kontrakt, mange spørgsmål). Effekten
  verificeres på `usage.cache_read_input_tokens` — er den nul over gentagne kald, er
  der en stille invalidator, og det er en fejl, ikke en detalje.
- **Batch til nattens agentkørsler:** de planlagte kørsler (N03) er ikke
  latensfølsomme. Kørt gennem Batch API koster de **halv pris**. Det er den enkeltvis
  største omkostningsbesparelse i produktet — og den er kun tilgængelig på ADR-0008's
  `anthropic`-backend, hvilket skal med i prissætningen af EU-backenden.
- **Effort er den første kvalitetsknap, ikke modelvalget.** Før en opgave flyttes op i
  modelklasse, prøves et højere effort-niveau på den nuværende, og forskellen måles.

### 5. Robusthed

- `stop_reason` tjekkes altid før indholdet læses — inklusive `refusal`, som returneres
  med HTTP 200.
- Serverside `fallbacks` slås til på Opus-opgaverne, så en afvist forespørgsel rutes
  videre i stedet for at fejle en agentkørsel.
- Fejl i en agentopgave må aldrig vælte kørslen (bidflow ADR-0054): opgaven markeres
  fejlet i `agent_runs`, de øvrige fortsætter.
- Alle kald streames, når `max_tokens` er stor, så HTTP-timeouts ikke rammer lange svar.

## Diagram — bevidst fravalgt

Beslutningens indhold er en **opslagstabel** fra opgave til model og effort, og den er
allerede tabellen i §1 — den mest læsbare form den findes i. Der er ingen proces at
vise (opslaget er ét trin, og kaldets vej gennem systemet er ADR-0008's
sekvensdiagram), ingen datamodel ud over konfigurationen, og ingen struktur ud over
worker-containeren, som ADR-0007's topologi allerede viser. Et diagram her ville
gentegne en tabel som bokse og gøre den sværere at vedligeholde, ikke lettere at forstå.
Vurderet og fravalgt.

## Konsekvenser

- **K02 er lukket:** bidflow ADR-0003's "én udbyder til alt" overføres ikke; modelvalg
  er konfiguration pr. opgave, og mockuppens hardkodede model findes ikke i koden.
- **Omkostningsprofilen bliver styrbar:** de tre natlige høj-volumen-opgaver på Haiku
  gennem Batch koster en brøkdel af samme opgaver på Opus i realtid. Med ADR-0082's
  måling pr. opgave kan det ses, ikke gættes.
- **Tre agenter uden LLM** betyder, at "12 AI-agenter" i UI'et dækker over agenter,
  hvoraf nogle er regelmotorer. Det er ærligt over for brugeren (de foreslår stadig, og
  et menneske godkender stadig) — men det skal ikke sælges som AI, hvor det ikke er det.
- **Ingen `temperature`** betyder, at eval-disciplinen ikke er valgfri. Uden guldsættet
  har vi hverken determinisme *eller* måling. Guldsæt-ADR'en (bidflow 0013's
  overførsel) rykker dermed frem i rækkefølgen.
- **Selvhostede embeddings koster CPU og RAM** i worker-containeren og gør indeksering
  langsommere end et API-kald. Det er en bevidst byttehandel for residens og
  uafhængighed; skalerer den ikke, er et EU-hostet embeddings-API alternativet — og et
  skift kræver fuld re-indeksering, jf. bidflow 0063.
- **ZDR udelukker Covered Models.** Vil vi senere bruge Fable-klassen, kræver det en
  særlig aftale med udbyderen eller en workspace med 30-dages retention — en beslutning,
  der ændrer ADR-0008, ikke kun denne.
- Tests/tjek: intet model-id uden for konfigurationstabellen (CI-grep); hver opgave har
  et skema, og et svar, der ikke validerer, bliver ikke et forslag; `usage_events`
  bærer opgavenavn og model; cache-hitrate måles i staging; guldsæt-gaten kører i CI for
  `obligation_extract`.

## Alternativer overvejet

- **Én model til alt (mockuppens hardkodning, bidflow ADR-0003).** Afvist: enten
  overbetaling på datoudtræk eller underkvalitet på forpligtelser. Enkelheden var
  argumentet, og bidflow viste, at den ikke holdt et kvartal.
- **Vælg model dynamisk med en router-model.** Afvist: tilføjer et kald, en fejlkilde
  og en uforudsigelig omkostning for at vælge mellem 15 kendte opgaver. En tabel er
  bedre end en model, når mængden af valg er endelig og kendt.
- **Fable-klassen til forpligtelses-ekstraktion.** Fravalgt nu: udelukket af
  ZDR-defaulten (Covered Model, 30 dages retention), og til dobbelt pris af Opus 5 uden
  en målt gevinst på denne opgave. Genovervejes hvis guldsættet viser et loft på Opus 5.
- **Embeddings fra genereringsudbyderen.** Ikke muligt: Anthropic har ingen
  embeddings-endpoint. Et tredje udbyderforhold (OpenAI, Voyage, Cohere) ville sende
  hele dokumentbestanden ud af EU og tilføje en underdatabehandler til DPA'en for den
  mest volumentunge operation i systemet.
- **Ingen prompt caching i v1 ("optimering senere").** Afvist: copiloten stiller mange
  spørgsmål til samme kontrakt; uden caching betaler vi for det samme
  aftalegrundlag hver gang. Det er ikke en optimering, det er designet.
- **Realtidskørsel af de natlige agenter (ingen batch).** Afvist: dobbelt pris for en
  latens, ingen bruger venter på.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **Opus 5 på `contract_intake` og `copilot` i pilotfasen**, og forskellen til Sonnet 5
   *måles* på guldsættet. En nedgradering skal have et tal bag sig, ikke en fornemmelse.
2. **Selvhostede embeddings** frem for et EU-hostet embeddings-API — netop fordi
   indeksering er den operation, der ellers ville sende hele dokumentbestanden ud af EU.
   Konkret model vælges ved en målt sammenligning på dansk kontrakttekst før første
   kunde.
3. **De tre model-frie agenter hedder fortsat "agenter"** i UI'et og bruger samme
   forslags- og godkendelsesflow som de øvrige (ADR-0004), men skriver
   `System · Responsibility Gap` i auditloggen — ikke `AI · …`.
