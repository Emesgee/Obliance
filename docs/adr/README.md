# Architecture Decision Records — Obliance

Én beslutning pr. fil: `NNNN-kort-kebab-titel.md` (nulpolstret, fortløbende).
Status er `Proposed`, `Accepted`, `Superseded by ADR-NNNN` eller `Deprecated`.
Historik omskrives aldrig — en ændret beslutning er en **ny** ADR, der superseder den gamle.

Skabelon: [`0000-template.md`](0000-template.md).
Planen bag rækkefølgen: [`../adr-plan.md`](../adr-plan.md) — den kortlægger, hvilke
bidflow-ADR'er der overføres, hvilke nye der kræves (N01–N20), og hvor der er konflikt
(K01–K13). Bidflows ADR'er ligger i `C:\Projekter\bidflow-saas\docs\adr`; når en af dem
genbruges, skrives den om her med nyt domænesprog og et link tilbage.

## Index

| ADR | Titel | Status | Plan-ref |
|-----|-------|--------|----------|
| [0001](0001-kontrakt-som-aggregat.md) | Kontrakten som aggregat — udbud er en fase, ikke en anden entitet | Accepted | N01, K12 |
| [0002](0002-tenant-og-fortrolighedsgraense.md) | Tenant- og fortrolighedsgrænse — RLS på to niveauer | Accepted | N08, K06, K07 |
| [0003](0003-rbac-matrix-funktionsadskillelse-beloebsgraenser.md) | RBAC som data — rollematrix, funktionsadskillelse og beløbsgrænser | Accepted | N07, K13 |
| [0004](0004-hitl-ai-forslag.md) | HITL som én mekanisme — AI skriver forslag, mennesker skriver registret | Accepted | N02, K11 |
| [0005](0005-kildehenvisning-klausulniveau.md) | Kildehenvisning på klausulniveau — citatet er sandheden, siden er afledt | Accepted | N04 |
| [0006](0006-dokumentversionering-gaeldende-version.md) | Dokumentversionering — uforanderlige versioner, én gældende ad gangen | Accepted | N05 |
| [0007](0007-hetzner-dedikeret-eu-hosting.md) | Dedikeret Hetzner-miljø i EU — multi-tenant SaaS, ingen delt host, objektstorage | Accepted | K01 |
| [0008](0008-llm-adgang-serverside-residens-databehandling.md) | LLM-adgang — serverside proxy, dataresidens og databehandling | Accepted | N14, K03 |
| [0009](0009-model-pr-opgave.md) | Model pr. opgave bag én abstraktion — og embeddings i egen drift | Accepted | N15, K02 |
| [0010](0010-agentorkestrering.md) | Agentorkestrering — planlagte kørsler, idempotens og ét kørselsspor | Accepted | N03, K08 |
| [0011](0011-uforanderlig-auditlog.md) | Uforanderlig auditlog — append-only, tre aktørtyper, adskilt fra måling | Accepted | N09 |
| [0012](0012-opbevaring-arkivering-legal-hold.md) | Opbevaring, arkivering og legal hold — persondata og kontraktdata skilles ad | Accepted | N10, K04 |
| [0013](0013-deterministisk-bods-og-creditberegning.md) | Bod, service credits og krav beregnes i kode — modellen udtrækker kun parametre | Accepted | N06 |
| [0014](0014-ai-forbrug-og-omkostningsmaaling.md) | AI-forbrug måles fra første kald — én hændelse pr. operation | Accepted | 0082 |
| [0015](0015-designsystem-tokens-fra-mockuppen.md) | Designsystem — tokens trukket ud af mockuppen, med kontrasten rettet | Accepted | N18 |
| [0016](0016-ai-sikkerhed-dokumenter-er-ikke-betroet-input.md) | AI-sikkerhed — kontraktdokumenter er ikke-betroet input | Accepted | N13 |
| [0017](0017-deadline-og-notifikationsmotor.md) | Deadline- og notifikationsmotor — frister afledes, varsler dedupliceres, intet sendes til leverandøren | Accepted | N12 |
| [0018](0018-erp-integration-indgaaende-fakturafeed.md) | ERP-integration — indgående fakturafeed, idempotent, aldrig tilbageskrivning | Accepted | N11 |
| [0019](0019-kpi-og-maaledata.md) | KPI- og måledata — strukturerede mål, målinger som fakta, status som afledning | Accepted | N19 |
| [0020](0020-leverandoerstamdata-og-certifikatovervaagning.md) | Leverandørstamdata — certifikater som data, scorer som afledning med synligt grundlag | Accepted | N16 |
| [0021](0021-raci-model-og-ansvarshuller.md) | RACI som data — funktioner i matricen, personer pr. kontrakt, huller som regler | Accepted | N17 |
| [0022](0022-rapporter-og-eksport.md) | Rapporter og eksport — afledte udtræk, samme forespørgsel som skærmen, hver eksport logget | Accepted | N20 |
| [0023](0023-stak-repo-layout-og-ci-gates.md) | Stak, repo-layout og CI-gates — det, tyve ADR'er har antaget, gjort eksplicit | Accepted | plan §1.1 |
| [0024](0024-auth-increment-1.md) | Auth, increment 1 — stateless login, RBAC som dependency, første datarute | Proposed | plan §1.2, K05 |
