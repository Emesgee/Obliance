# infra — ADR-0007 i praksis

Ét dedikeret Hetzner Cloud-projekt, én server pr. miljø, én Compose-stak, én
systemd-unit. Ingen naboer, ingen Cloudflare i datastien, Postgres og Redis kun på
loopback, Caddy som eneste offentlige port.

## Første deploy (prod)

1. Server: Hetzner Cloud, Falkenstein eller Nürnberg, dedikeret vCPU-linje. Cloud
   Firewall: ind 443 (alle), 22 (allowlist). Ubuntu LTS, `fail2ban`,
   `unattended-upgrades`, SSH-nøgler only, en `deploy`-bruger.
2. Volume til Postgres, **LUKS-krypteret**, monteret på `/mnt/pgdata`; peg `pgdata`
   i `compose.yml` derhen (bind mount) før første start.
3. `cp .env.example .env && chmod 600 .env` — udfyld alt. Læg kopien i
   password-manageren.
4. Første start: `docker compose --profile app up -d --build`. Init-scriptet opretter
   rollerne med **dev-passwords**. Rotér straks:
   ```
   docker compose exec postgres psql -U postgres -c "ALTER ROLE obliance_migrator PASSWORD '...';"
   docker compose exec postgres psql -U postgres -c "ALTER ROLE obliance_app PASSWORD '...';"
   docker compose exec postgres psql -U postgres -c "ALTER ROLE obliance_worker PASSWORD '...';"
   ```
   og opdatér `CC_*_PASSWORD` i `.env`, `docker compose --profile app up -d`.
5. Migrér: `docker compose exec api alembic upgrade head` (kører som obliance_migrator).
6. systemd-unit, der ejer stakken (`Restart=always`):
   ```
   [Unit]  Description=obliance  After=docker.service  Requires=docker.service
   [Service] Type=oneshot RemainAfterExit=yes WorkingDirectory=/opt/obliance/infra
   ExecStart=/usr/bin/docker compose --profile app up -d
   ExecStop=/usr/bin/docker compose --profile app down
   [Install] WantedBy=multi-user.target
   ```
7. Backup **før** første kundedata: `backup/backup.sh` i cron `30 3 * * *`, restore-test
   ind i staging, skriv resultatet i `dr-log.md`.

## Staging

Samme opsætning, mindre server, **kun syntetiske data** (ADR-0007 afklaring 3).
Aldrig en kopi af prod.

## Hvad forlader miljøet

Præcis to udgående dataflows med kundedata (ADR-0007 §6): LLM-udbyderen (ADR-0008) og
transaktionel mail med metadata (ADR-0017). Begge står i DPA'en.
