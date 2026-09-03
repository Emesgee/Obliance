# ADR-0024: Auth, increment 1 — stateless login, RBAC som dependency, første datarute

- **Status:** Proposed
- **Date:** 2026-09-03
- **Area:** auth-access
- **Deciders:** Project owner
- **Related:** ADR-0002 (tenant-kontekst er dependency-grafen), ADR-0003 (matrix som
  data), ADR-0023 (FastAPI, pydantic-skemaer); `docs/adr-plan.md` §1.2 (overførsler:
  bidflow 0006, 0009, 0065, 0067) og K05 (SSO); bidflow ADR-0006 (auth-hærdning),
  ADR-0009 (`limits` direkte), ADR-0065 (MFA obligatorisk, stateless step-up),
  ADR-0067 (deaktivering), ADR-0068 (superadmin), ADR-0036 (dansk fejltekst)

## Kontekst

Planen sagde: de rene overførsler fra bidflow skrives som ADR'er, *når koden bygges*.
Auth er den første, fordi hver datarute skal kunne resolve en `TenantContext` fra en
bruger — uden det kan ingen skærm bygges rigtigt (ADR-0002). Bidflows auth er kendt og
driftsprøvet: stateless JWT, e-mailverifikation, reset, ratelimit, MFA med TOTP,
invitationer, deaktivering. Det er for meget til ét increment og for lidt at springe
over.

Rammen er sat af tre ADR'er: ADR-0002 (en session uden tenant-kontekst findes ikke),
ADR-0003 (tilladelser er data, håndhævet server-side) og ADR-0023 (FastAPI-dependencies
frem for Flask-dekoratører; pydantic-skemaer er API-kontrakten). Én del af bidflows
model står i konflikt med mockuppen: K05 — mockuppen logger ind via organisationens
SSO, mens bidflow tvinger TOTP i appen.

## Beslutning

### Increment 1 (denne ADR) — det, der er bygget

1. **Stateless adgangstoken.** HS256-JWT via PyJWT, signeret med `SECRET_KEY`, claims
   `sub` (profil), `org`, `role`, `scope`, `exp` (8 timer). Ingen serversession —
   samme design som bidflow 0065, så et MFA-step-up-token senere blot er et kortlivet
   JWT med `scope = "mfa"`, som `current_principal` allerede afviser.
2. **argon2id** via `pwdlib` frem for bidflows passlib+bcrypt (og dens 72-byte-pin).
   Minimum 12 tegn (bidflow 0006).
3. **`current_principal`** (FastAPI-dependency): Bearer → token → profil → **medlemskab
   er sandheden** (rollen i tokenet er et hint; en rolleændring virker ved næste
   request, ikke ved næste login) → deaktiveret konto afvises også på et allerede
   udstedt token (bidflow 0067) → lukket org afvises. Første medlemskab vælges (bidflows
   v1-regel); org-switcher kommer med superadmin.
4. **`tenant_session`**: en `Session` *inde i* `tenant(org, user, role)`. Der findes
   ingen dependency, der giver en session uden en principal — tenant-konteksten er
   dependency-grafen, ikke en konvention.
5. **`require(permission)`**: RBAC-gate mod ADR-0003's matrix, som nu er data i
   `role_permissions` (migration 0002) *og* kode i `app/core/access.py`; en test
   fejler, hvis de to nogensinde afviger.
6. **Ratelimit på login** med `limits` direkte (bidflow 0009), pr. klient-IP,
   `memory://` lokalt og Redis i prod, så grænsen holder på tværs af api-replikaer.
7. **Fejl er danske med maskinkode** (`{detail: {error, code}}`, bidflow 0036), og
   forkert e-mail og forkert adgangskode svarer ens — ingen brugerenumeration.
8. **Første datarute:** `GET/POST /api/contracts`. Listen har **ingen** org-filter i
   forespørgslen — databasen filtrerer (ADR-0002), og en test beviser det over HTTP.
   Beløbsfelter er `null` (ikke 0) uden `okonomi` (ADR-0003 §2). En fortrolig kontrakt,
   der oprettes uden ejer/manager, får opretteren som manager — ellers fejler
   `INSERT … RETURNING` på SELECT-policyen (migration 0001's docstring).
9. **Ingen offentlig signup.** Første bruger oprettes af operatøren med
   `python -m app.cli bootstrap` (bidflow 0070's mønster, bag SSH). Resten inviteres
   (increment 2).
10. **Frontend:** login-side, auth-store (token i hukommelse, spejlet i localStorage,
    `/api/me` genhentes ved load), beskyttede ruter, kontraktliste med feltmaskering
    vist som "—".

### Increment 2 — besluttet, ikke bygget

- **MFA (TOTP) obligatorisk for lokale konti** — bidflow 0065/0071 porteres uændret,
  med step-up-tokenet ovenfor. **Gate: ingen kunde-login før dette er bygget.**
  Increment 1 er en dev-/pilotstak.
- **Invitationer** (bidflow 0066) og **deaktivering via UI** (0067) — API'et afviser
  allerede deaktiverede.
- **E-mailverifikation og adgangskode-reset** (bidflow 0006's itsdangerous-tokens).
- **Superadmin med org-switcher** (bidflow 0068 + K07's stramninger) — egen ADR, før
  nogen får cross-tenant-adgang.
- **SSO/OIDC** (K05): når første SSO-kunde er kendt. Skemaet er klar: `password_hash`
  er nullable, og en SSO-org springer TOTP over, fordi IdP'en asserterer MFA.

## Diagram — bevidst fravalgt

Kæden Bearer → principal → tenant → RLS er allerede tegnet i ADR-0002's flowchart
(fra kontekst og ned) og ADR-0008's sekvensdiagram (fra request og ned); denne ADR
tilføjer kun *hvordan* konteksten opstår, og det er tre dependencies i én lige linje
(§3–5). Matricen er en tabel. Et diagram ville gentegne 0002 med ét felt foran.

## Konsekvenser

- Enhver datarute, der bygges herefter, *skal* tage `Depends(tenant_session)` — der
  findes ikke en anden vej til en session med kundedata. Det gør ADR-0002 til noget,
  en udvikler ikke kan glemme.
- Rollen læses fra medlemskabet pr. request: ét ekstra opslag (identitetstabel, ingen
  RLS), til gengæld ingen "log ud og ind igen" efter en rolleændring eller
  deaktivering.
- `SECRET_KEY`-rotation logger alle ud. Bevidst.
- **Increment 1 må ikke møde en kunde:** uden MFA og invitationer er det en pilotstak.
  Det står som gate i §Increment 2, og `bootstrap`-kommandoen er den eneste vej ind.
- Tests: 7 unit (hash, token, matrix = ADR-0003) + 10 integration (login-varianter,
  deaktivering rammer eksisterende token, ratelimit 429, RLS over HTTP, RBAC 403,
  feltmaskering, 409 ved dublet-reference, opretteren ser sin fortrolige kontrakt,
  tabel = kode-matrix).

## Alternativer overvejet

- **Portere bidflows Flask-dekoratør 1:1.** Afvist: ADR-0023 valgte FastAPI; en
  dependency-graf er den rigtige form, fordi den gør tenant-konteksten obligatorisk.
- **Auth-BaaS (Clerk/Auth0/Supabase)** — genbrugsvurderingens forslag. Afvist for v1:
  offentlig kunde, DPA og ADR-0007's "to udgående dataflows" — et tredje for login er
  en dyr tilføjelse til DPA'en for noget, bidflow allerede har bygget og driftet.
- **Rolle i tokenet som sandhed** (ingen opslag). Afvist: en rolleændring eller
  deaktivering ville først virke ved næste login.
- **MFA i increment 1.** Fravalgt som scope, ikke som beslutning: det er gaten for
  første kunde, og det står skrevet.
- **Signup-endpoint.** Afvist: produktet har ingen selvoprettelse (mockuppen: "SSO via
  organisationens identitetsløsning"); bootstrap + invitationer er vejen.

## Åbne spørgsmål til beslutning

1. **Token-levetid 8 timer** (en arbejdsdag) frem for bidflows kortere default?
   Anbefaling: 8 timer i pilot, og genovervej sammen med MFA — et step-up-token på 5
   minutter og et adgangstoken på 8 timer er bidflows kombination.
2. **Skal `/api/me` cache'es i frontend'en** eller altid genhentes ved load?
   Anbefaling: genhentes — det er sådan, deaktivering og rolleændring når ud i UI'et.
3. **Skal `bootstrap` også kunne køre uden `--password`** (spørger interaktivt, som nu),
   eller kræve flaget? Anbefaling: interaktivt som default — passwordet skal ikke i
   shell-historikken.
