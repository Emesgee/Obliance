# Obliance

AI-drevet contract management for ordregivere — første kunde Amgros I/S.

Arkitekturen er besluttet i **23 ADR'er** i [`docs/adr/`](docs/adr/README.md); planen bag
dem er [`docs/adr-plan.md`](docs/adr-plan.md). Læs ADR-0023 først — den fastlægger stak,
repo-layout og de CI-gates, alt andet hviler på.

## Layout

```
backend/    FastAPI + SQLAlchemy 2 + Alembic · app/llm er eneste udgang til en model
frontend/   React + Vite + TypeScript · tokens.css er eneste kilde til farver
infra/      Docker Compose, Caddy, Postgres-roller, backup
scripts/    CI-gates (G-01, G-02, G-04, G-14 — ADR-0023 §5)
docs/       ADR'er og plan
```

> **Navn.** Produktet hed *ContractFlow AI* (repo `contractclear`) frem til 2026-09-03 og
> hedder nu **Obliance**. ADR'erne bruger det nye navn; mockup-filen
> `contractflow-ai-amgros.html` beholder sit. Mappen omdøbes til `C:\Projekter\Obliance`,
> når ingen session holder den åben.

## Kom i gang (lokalt, uden Docker — native PostgreSQL)

Lokal udvikling på en maskine uden Docker (Shadow PC) bruger en **native PostgreSQL** på
`localhost:5432` med databasen `obliance` (+ `obliance_test` til suiten). Rollerne er de
samme tre som i Docker-opsætningen — RLS-testene kræver en ikke-superuser-rolle, der ikke
ejer skemaet, ellers beviser de ingenting.

```
# én gang, som postgres-superuser (opretter roller + databaser, idempotent):
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f infra/postgres/native-setup.sql

cd backend && cp .env.example .env && uv sync    # .env peger allerede på localhost:5432/obliance
uv run alembic upgrade head                      # kører som obliance_migrator
uv run pytest                                    # RLS + skema-vagt som obliance_app
python ../scripts/gates.py
```

Redis findes ikke native på Windows; API'et og testene kører uden. Worker'en kræver
Redis — brug [Memurai](https://www.memurai.com) lokalt (bidflow ADR-0011), eller kør
worker-laget kun i Docker/staging.

## Kom i gang (lokalt, med Docker)

```
cp infra/.env.example infra/.env            # sæt POSTGRES_PASSWORD og REDIS_PASSWORD
docker compose -f infra/compose.yml up -d postgres redis
cd backend && cp .env.example .env && uv sync
uv run alembic upgrade head                 # kører som obliance_migrator (ejer)
uv run pytest                               # RLS-tests kører som obliance_app (ikke-superuser)
python ../scripts/gates.py                  # grep-gates
```

Postgres-rollerne (`obliance_migrator`, `obliance_app`, `obliance_worker`) oprettes af
`infra/postgres/init/01-roles.sql` første gang volumen initialiseres. RLS kan ikke
testes på SQLite og ikke som superuser — derfor tre roller og en rigtig database.

## Regler, CI håndhæver

Se ADR-0023 §5. De første, der kører: G-01 (ingen udbydernøgle i `frontend/`), G-02
(ingen hex uden for `tokens.css`), G-04 (kun `app/llm/` importerer `anthropic`), G-05
(RLS mod rigtig Postgres), G-13 (skema-vagt: hver tabel med `organization_id` har sin
policy), G-14 (ingen `temperature` i LLM-kode).
