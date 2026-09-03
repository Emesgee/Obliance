# ADR-0023: Stak, repo-layout og CI-gates — det, tyve ADR'er har antaget, gjort eksplicit

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** arch
- **Deciders:** Project owner
- **Related:** alle ADR-0001–0022 (hver af dem bestiller mindst én test eller gate);
  ADR-0007 (containere: `api`, `worker`), ADR-0008 (`app/llm/` som eneste udgang),
  ADR-0009 (skema for alt maskinlæsbart; officiel SDK), ADR-0015 (`tokens.css`);
  `docs/adr-plan.md` §1.1 (bidflow ADR-0001: "genovervej — samme konklusion sandsynlig");
  bidflow ADR-0001 (Flask + React), ADR-0004 (`rls.py`), ADR-0007 (`storage.py`),
  ADR-0011/0026 (RQ), ADR-0006/0065 (mailer, MFA), ADR-0070 (admin-CLI), ADR-0049/0055/
  0061 (PDF-stak), ADR-0023 (OCR)

## Kontekst

Planen markerede bidflows stakvalg (ADR-0001, Flask + React) som *"genovervej — samme
konklusion sandsynlig"* og pegede på, at bidflows begrundelse — at hoste den auditerede
CrewAI-motor in-process — **ikke findes længere**. Siden er beslutningen sivet ind
gennem tyve ADR'er uden at få sin egen: `app/llm/` (0008), `app/finance/penalties.py`
(0013), `app/reports/registry.py` (0022), en `api`-container med "Flask/gunicorn" (0007),
RQ-workers (0010), `Decimal` (0013), `pg_try_advisory_lock` (0010), `tokens.css` +
Tailwind (0015). Det er Python, Postgres og React — antaget, ikke besluttet.

Samtidig har ADR'erne bestilt mindst femten konkrete tests og gates ("CI fejler hvis…",
"test der beviser…"), som ingen ejer endnu. De er spredt over tyve dokumenter og vil
blive glemt én ad gangen, hvis de ikke samles ét sted og bindes til noget, der kører.

Denne ADR gør tre ting: træffer stakvalget med en begrundelse, der holder *uden*
bidflows motor; fastlægger repo-layoutet, så bidflows genbrugelige kode har et sted at
lande; og samler alle bestilte gates i én liste med ejer og kilde.

## Beslutning

### 1. Backend: Python 3.12, FastAPI, SQLAlchemy 2, Postgres 16 + pgvector

**Python**, fordi de tre tungeste dele af produktet er Python-økosystemer: dokument-
værktøjskæden (PyMuPDF, LibreOffice-orkestrering, OCR-rendering — bidflow 0049/0055/
0061/0023), den officielle Anthropic-SDK med Vertex-klient (ADR-0008's to backends via
`anthropic` og `anthropic[vertex]`), og selvhostede embeddings (ADR-0009 §2 — sentence-
transformer-familien er Python). Dertil ni bidflow-moduler, der porteres næsten uændret
(§4). Begrundelsen står, selv om ingen ekstraktionsmotor skal hostes.

**FastAPI** frem for bidflows Flask — og det er den ene afvigelse fra "samme konklusion":

- **Ét skema, to formål.** ADR-0004 og 0009 kræver et skema for *alt* maskinlæsbart, og
  ADR-0009 §3 bruger strukturerede outputs med `strict`. Pydantic-modeller er både API-
  kontrakten mod frontend'en *og* JSON-skemaet mod modellen. I Flask er det to
  definitioner, der drifter; i FastAPI er det én.
- **Streaming som førsteklasses.** ADR-0008 §1 streamer copilot-svar via SSE; FastAPI's
  async-endpoints og `StreamingResponse` er bygget til det. Flask kan, men mod strømmen.
- **OpenAPI gratis** → typet klient i frontend'en (§2), så en feltmaskering (ADR-0003)
  eller en manglende kolonne (ADR-0022) er en typefejl, ikke en runtime-overraskelse.

Prisen er, at bidflows *HTTP-lag* (blueprints, `auth_and_org`-dekoratøren) skal
skrives om. Domænekoden bag det er rammeværksuafhængig og porteres (§4).

**Resten af backend-stakken:** SQLAlchemy 2.x + Alembic (RLS via `ContextVar` +
`after_begin`-listener, bidflow 0004's `rls.py` porteret), Postgres 16 + pgvector
(ADR-0002), Redis + RQ + rq-scheduler (ADR-0010), `uv` til miljø og låste afhængigheder,
`ruff` + `mypy --strict` på ny kode, `pytest`. Gunicorn erstattes af **uvicorn** bag
Caddy; ADR-0007's containertabel opdateres tilsvarende.

### 2. Frontend: React 18, Vite, TypeScript, Tailwind på tokens

Som mockuppen (React) og som bidflow (React/Vite/TS). Nyt i forhold til begge:
**inline-styles erstattes af `tokens.css` + Tailwind** (ADR-0015 — mockuppens
`style={{…}}` overlever ikke), TanStack Query til serverstate, React Router, og en
**genereret typet API-klient** fra backend'ens OpenAPI (`openapi-typescript`), så
frontend og backend deler kontrakten uden håndskrevne typer. `pnpm`, `eslint`, `vitest`;
Playwright til e2e, når der er et flow at teste.

### 3. Repo-layout: ét repo, tre rødder

```
obliance/
  backend/
    app/
      api/            routers pr. modul (contracts, documents, copilot, reports …)
      core/           config, rls, auth, access (ADR-0002/0003), audit (ADR-0011)
      domain/         modeller + servicelag: contracts, documents, obligations, kpis …
      llm/            ENESTE udgang til en model (ADR-0008); tasks + skemaer (ADR-0009)
      agents/         agentdefinitioner + scheduler (ADR-0010); gap-/workload-regler (ADR-0021)
      finance/        penalties.py, claims (ADR-0013); invoice matching (ADR-0018)
      documents/      ingest, pages, clauses, citations (ADR-0005/0006), pdf, ocr, scanner (ADR-0016)
      reports/        registry.py (ADR-0022)
      jobs/           RQ-jobs, ingest, retention (ADR-0012)
      cli/            admin-CLI (bidflow 0070)
    migrations/       Alembic; hver kundetabel med organization_id + policy (gate G-13)
    tests/
      unit/  integration/  llm_contract/  adversarial/  gold/
  frontend/
    src/  tokens.css  tailwind.config.ts  api/ (genereret)
  infra/
    compose.yml  compose.staging.yml  caddy/  backup/  dr-log.md  README.md (ADR-0007)
  docs/
    adr/  adr-plan.md
```

Grænserne er ikke pynt: **`app/llm/` er det eneste modul, der importerer `anthropic`**
(gate G-04), **`app/agents/` skriver kun til `ai_suggestions`** (gate G-07), og
**`frontend/` kender ingen udbyder** (gate G-01). Et brud på en grænse er en CI-fejl.

### 4. Portering fra bidflow — hvad der lander hvor

| Bidflow-modul | ADR | Lander i | Ændring |
|---|---|---|---|
| `app/rls.py` (ContextVar + `after_begin`) | 0004 | `core/rls.py` | + `app.current_user_id`, `app.current_role` (ADR-0002) |
| `app/storage.py` (facade, `materialize`) | 0007 | `core/storage.py` | + `s3`-backend mod Hetzner Object Storage (ADR-0007) |
| `dispatch.py`, `worker.py` | 0011/0026 | `jobs/` | + scheduler, advisory lock, budget (ADR-0010) |
| `app/mailer.py` (console/SMTP/Resend) | 0006 | `core/mailer.py` | + kun-metadata-regel (ADR-0017 §4) |
| `app/mfa.py`, `app/tokens.py` | 0065/0071 | `core/auth/` | uændret; SSO-org springer over (K05) |
| `app/admin_cli.py` | 0070 | `cli/` | kører via `docker compose exec` (ADR-0007) |
| `services/pdf_convert.py`, `pdf_pages.py`, `locateCitation` | 0049/0055/0061 | `documents/` | + klausulindeks, verifikation (ADR-0005) |
| `parsing/ocr.py` (vision-OCR, opt-in) | 0023 | `documents/ocr.py` | gennem `app/llm/` som `ocr_page` (ADR-0009) |
| `services/usage.py` (recorder, pristabel) | 0082 | `llm/usage.py` | pris gemmes ved skrivning (ADR-0014) |
| `lib/useRowKeyboardNav.ts`, `InfoCard`, `labels.ts` | 0032/0027/0036 | `frontend/src/lib/` | uændret |

**Ikke porteret:** blueprints og `auth_and_org` (skrives om til FastAPI-dependencies),
alt under `extraction/` og `crews/` (bidflows motor), ESPD/VI-info/go-no-go (K12).

### 5. Gates — alt, ADR'erne har bestilt, ét sted

Hver gate kører i CI på hver PR mod `main`; en rød gate blokerer merge. Ejer er den ADR,
der bestilte den.

| # | Gate | Kilde | Mekanisme |
|---|---|---|---|
| G-01 | Ingen `api.anthropic.com`, `x-api-key`, `ANTHROPIC_` under `frontend/` | 0008 | grep i CI |
| G-02 | Ingen hex-farve uden for `tokens.css` | 0015 | grep i CI |
| G-03 | Alle token-par i ADR-0015 §2 ≥ 4,5:1 (tekst) / 3:1 (markering) | 0015 | kontrastscript over `tokens.css` |
| G-04 | Kun `app/llm/` importerer `anthropic`; intet model-id uden for `llm/config` | 0009 | import-linter + grep |
| G-05 | RLS: cross-org læsning = 0 rækker; cross-org skrivning fejler; Business User uden adgang ser ikke fortrolig kontrakt — heller ikke via copilot-kontekst | 0002 | integrationstest mod **rigtig Postgres** (service-container), **ikke-superuser-rolle** |
| G-06 | App-rollen kan ikke `UPDATE`/`DELETE` i `audit_log` | 0011 | rå SQL-test |
| G-07 | Worker-rollen kan ikke skrive i `obligations`, `risks`, `invoices`, `contracts`, `tasks`, `raci_*` | 0004 | rå SQL-test |
| G-08 | Modstander-korpus: de fire dokumenter i ADR-0016 §6 giver flag + lav sikkerhed + ingen registerændring | 0016 | integrationstest med optagede modelsvar |
| G-09 | Ét LLM-kald i test-scope giver præcis én `usage_events`-række med pris | 0014 | integrationstest |
| G-10 | Bodsformler rammer mockuppens tal: 30.625 og 96.512; loft gemmer begge beløb; `data_mangler` giver intet krav | 0013 | unit |
| G-11 | Guldsæt-gate for `obligation_extract`: recall ≥ baseline | 0009 | **aktiveres når guldsættet findes** — indtil da en manuel tjekliste i PR-skabelonen |
| G-12 | CSV: BOM, semikolon, komma-decimal, RFC 4180-citering — byte-test | 0022 | unit |
| G-13 | **Skema-vagt:** hver tabel med `organization_id` har `tenant_isolation`-policy og `FORCE`; hver børnetabel har `contract_id IN (SELECT …)`-policy | 0002 | test der læser `pg_policies` efter migration |
| G-14 | Ingen `temperature`, `top_p`, `top_k`, `budget_tokens` i `app/llm/` | 0009 | grep |
| G-15 | Funktionsadskillelse: `CHECK (approved_by <> created_by)` findes på hver godkendbar tabel | 0003 | skema-test |
| G-16 | Én `gaeldende` pr. dokument; samme sha256 afvises | 0006 | integrationstest |
| G-17 | Hemmeligheder: ingen nøgler i repo | — | secret-scan |
| G-18 | `prev_hash`/`row_hash` sættes på hver auditrække | 0011 | integrationstest |

G-05, G-06, G-07, G-13, G-15, G-16 og G-18 kræver Postgres i CI. Det er ikke valgfrit:
RLS kan ikke testes på SQLite (bidflow 0004), og halvdelen af ADR'ernes garantier bor i
databasen.

### 6. Teststrategi i fire lag

- **Unit** (`tests/unit/`): rene funktioner — bodsberegning, statusafledning (ADR-0019),
  citatlokalisering (ADR-0005), CSV-formatering, gap-regler (ADR-0021). Ingen DB, ingen
  model. Hurtige, mange.
- **Integration** (`tests/integration/`): rigtig Postgres, RLS, migrations, jobs,
  auditlog. Kører på hver PR.
- **LLM-kontrakt** (`tests/llm_contract/`): `app/llm/` mod **optagede** svar pr. opgave
  (fixtures), så skemavalidering og fejlhåndtering testes uden netværk og uden pris.
  Én natlig kørsel mod den rigtige udbyder på staging som røgtest — aldrig på PR.
- **Modstander** (`tests/adversarial/`) og **guldsæt** (`tests/gold/`): ADR-0016's korpus
  og ADR-0009's recall-måling. Guldsættet er data, der vokser; testen er den samme.

### 7. To ting, der bevidst ikke besluttes her

- **Auth-metode for lokale konti vs. SSO** (K05): mekanismen porteres fra bidflow;
  SSO-integrationen (OIDC) er en egen ADR, når første SSO-kunde er kendt.
- **Frontend-tilstandsarkitektur ud over TanStack Query**: ingen global store, før et
  behov viser sig. Mockuppen er ét `useState`-træ; det er ikke en model at kopiere, men
  det viser, at behovet er lille.

## Diagram — bevidst fravalgt

Beslutningens tre dele har hver sin rette form, og ingen af dem er et diagram: stakken
er en liste med begrundelser, repo-layoutet er et **træ** (§3 — et træ *er* diagrammet
for en mappestruktur), og gates er en **tabel** med kilde og mekanisme, som netop skal
kunne sammenlignes række for række. Deployment-topologien, som stakken kører i, er
allerede ADR-0007's flowchart. En graf over "hvilket modul må importere hvilket" ville
være det ene diagram med selvstændig værdi — og den er udtrykt præcist som gate G-04 og
G-07, som CI håndhæver, hvilket er stærkere end en tegning. Vurderet og fravalgt.

## Konsekvenser

- **Antagelsen er nu en beslutning**, og ADR-0007's "Flask/gunicorn" rettes til
  "FastAPI/uvicorn" med henvisning hertil. Ingen anden ADR ændres — de antog Python og
  Postgres, og det holder.
- **Porteringen af HTTP-laget koster tid**, som Flask ikke ville. Gevinsten er én
  skemadefinition for API og model, og streaming uden omveje. Domænekoden — ni moduler —
  flytter uændret.
- **Atten gates før første feature** er en høj tærskel, og det er meningen: hver gate er
  en garanti, en ADR har givet. Bygges de efter featuren, bygges de ikke.
- Postgres i CI gør pipelinen langsommere (minutter, ikke sekunder). Unit-laget holdes
  hurtigt, så udvikleren ikke venter på RLS-tests for at rette en formatfejl.
- G-11 (guldsæt) starter som en tjekliste, ikke en test. Det er en bevidst svaghed,
  navngivet, med en betingelse for, hvornår den lukkes.
- `mypy --strict` på ny kode og ikke på porteret kode: porteret kode får typer, når den
  røres. En "alt eller intet"-regel ville forsinke porteringen med uger.
- Frontend-klienten genereres — det betyder, at backend'en skal kunne producere et
  gyldigt OpenAPI-dokument fra dag 1, og at en API-ændring uden frontend-opdatering er
  en byggefejl. Det er en gate mere, uden nummer: `pnpm build` fejler.

## Alternativer overvejet

- **Flask (bidflow uændret).** Overvejet seriøst: kendt stak, HTTP-laget porteres 1:1.
  Fravalgt, fordi ADR-0004/0009's skema-overalt-design ellers får to definitioner pr.
  skema, og fordi SSE-streaming (ADR-0008) er en omvej. Hvis project owner vægter
  familiaritet højere end det, er Flask et forsvarligt valg — se åbne spørgsmål.
- **Next.js fullstack (bidflows fravalgte blueprint).** Afvist: dokumentværktøjskæden,
  embeddings og ni porterbare moduler er Python; en Node-backend ville betyde at skrive
  det hele forfra og køre Python ved siden af alligevel.
- **Django.** Afvist: ORM-låsning og admin-panel, vi ikke bruger (ADR-0007: admin bag
  SSH), og en tungere ramme om et system, hvis kerne er services og jobs.
- **Separat repo pr. lag.** Afvist: én operatør, én pipeline, én version. Grænserne
  håndhæves af gates, ikke af repo-skel.
- **Gates som "tech debt"-liste efter første feature.** Afvist: bidflow ADR-0082 viste,
  hvad der sker med det, der bygges "senere".
- **Håndskrevne frontend-typer.** Afvist: to kontrakter, der drifter; genereret klient er
  den billigste måde at gøre ADR-0003's feltmaskering synlig i typerne.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **FastAPI.** De tre grunde i §1 — ét skema til API og model, streaming som
   førsteklasses, genereret typet klient — vejer tungere end familiariteten med Flask.
   HTTP-laget skrives om; domænekoden porteres uændret (§4).
2. **`mypy --strict` kun på ny kode.** Porteret kode typeres, når den røres.
3. **Postgres i CI på hver PR** via service-container. En RLS-fejl, der først ses efter
   merge, er den dyreste fejl i sættet.
