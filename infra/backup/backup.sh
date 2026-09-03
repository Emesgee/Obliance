#!/usr/bin/env bash
# Daily, complete, encrypted, off-site backup — ADR-0007 §4 (from bidflow ADR-0084).
#
# A restore needs three things, not one: the database dump, the object-storage
# bucket (documents), and infra/.env (secrets). This bundles all three, encrypts
# with GPG AES256, rotates locally (7 daily / 4 weekly / 12 monthly) and mirrors
# to a Hetzner Storage Box in ANOTHER location than the server.
#
# Cron (root):  30 3 * * *  /opt/obliance/infra/backup/backup.sh >> /var/log/obliance-backup.log 2>&1
#
# CRITICAL: the passphrase file and infra/.env must ALSO live in the password
# manager. Without them an off-site copy cannot be decrypted (ADR-0007 §4.6).
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$INFRA_DIR/.env"

: "${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE in infra/.env}"
: "${STORAGEBOX_TARGET:?set STORAGEBOX_TARGET in infra/.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/obliance}"
STORAGEBOX_KEY="${STORAGEBOX_KEY:-/root/.ssh/storagebox_ed25519}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}

# 1. database — custom format, from inside the container, as the owner role
docker compose -f "$INFRA_DIR/compose.yml" exec -T postgres \
  pg_dump -U obliance_migrator -Fc --no-owner obliance > "$WORK/db.dump"

# 2. documents — mirror the bucket if object storage is configured (ADR-0007 §3)
if [[ -n "${S3_BUCKET:-}" ]] && command -v rclone >/dev/null; then
  rclone sync "obliance-s3:${S3_BUCKET}" "$WORK/bucket" --fast-list -q
fi

# 3. secrets
cp "$INFRA_DIR/.env" "$WORK/env"

# bundle + encrypt
tar -C "$WORK" -czf "$WORK/bundle.tar.gz" db.dump env $( [[ -d "$WORK/bucket" ]] && echo bucket )
OUT="$BACKUP_DIR/daily/obliance-${TS}.tar.gz.gpg"
gpg --batch --yes --symmetric --cipher-algo AES256 \
    --passphrase-file "$BACKUP_PASSPHRASE_FILE" -o "$OUT" "$WORK/bundle.tar.gz"

# rotation: promote Sundays to weekly, the 1st to monthly
dow="$(date -u +%u)"; dom="$(date -u +%d)"
[[ "$dow" == "7" ]] && cp "$OUT" "$BACKUP_DIR/weekly/"
[[ "$dom" == "01" ]] && cp "$OUT" "$BACKUP_DIR/monthly/"
ls -1t "$BACKUP_DIR/daily"   | tail -n +8  | xargs -r -I{} rm -f "$BACKUP_DIR/daily/{}"
ls -1t "$BACKUP_DIR/weekly"  | tail -n +5  | xargs -r -I{} rm -f "$BACKUP_DIR/weekly/{}"
ls -1t "$BACKUP_DIR/monthly" | tail -n +13 | xargs -r -I{} rm -f "$BACKUP_DIR/monthly/{}"

# off-site mirror (Storage Box: port 23, restricted shell — rsync/scp/sftp only)
rsync -rt --delete -e "ssh -p 23 -i $STORAGEBOX_KEY -o StrictHostKeyChecking=accept-new" \
  "$BACKUP_DIR/" "$STORAGEBOX_TARGET"

# freshness marker for monitoring (ADR-0007 §4.4: alarm if > 30 h old or < half yesterday's size)
stat -c '%s %Y' "$OUT" > "$BACKUP_DIR/LAST_OK"
echo "[$TS] ok $(du -h "$OUT" | cut -f1) $OUT"
