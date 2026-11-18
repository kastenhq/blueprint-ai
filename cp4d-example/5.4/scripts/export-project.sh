#!/usr/bin/env bash
# Export ONE CP4D project into the per-project backup PVC (mounted at /backup).
# Stores the zip produced by the CP4D export service AS IS, in /backup/current/export.zip.
# We do not extract it: the zip format is IBM's, not ours. CP4D 5.4 was seen to produce a
# zip with the same entry twice, which makes `unzip` stop on an interactive prompt.
# The zip is written atomically (a killed pod never leaves a half-written bundle that could
# be snapshotted), then the filesystem is flushed before the pod exits.
#
# Loaded from the cp4d-backup-scripts ConfigMap (see README), not baked into the image.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/cpdctl-login.sh"
: "${PID:?PID (project guid) is required}"
: "${PROJECT_NAME:?PROJECT_NAME is required}"
: "${ENCRYPTION_KEY:=}"     # optional; empty => plaintext bundle (V1 default, see README §security)

echo "exporting project $PID ($PROJECT_NAME)"
ENC=()
[ -n "$ENCRYPTION_KEY" ] && ENC=(--encryption-key "$ENCRYPTION_KEY")

# An EMPTY project is not an error. cpdctl refuses to export one (SPACES0046E "No assets
# found for export") and exits non-zero, which — since the keeper now covers every project
# in the cluster — would let a single empty project fail the entire backup. Treat it as a
# successful no-op and leave any existing bundle on the PVC untouched, so that emptying a
# project does not also destroy its last good backup.
set +e
OUT=$(cpdctl asset export start --project-id "$PID" --assets-all-assets \
        "${ENC[@]}" --name "kasten-${PID}" --output-file /tmp/export.zip 2>&1)
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  if printf '%s' "$OUT" | grep -q 'SPACES0046E'; then
    echo "SKIP: project $PID ($PROJECT_NAME) contains no assets — nothing to export."
    echo "      Any bundle already on the PVC is left as-is."
    exit 0
  fi
  echo "export FAILED for $PID ($PROJECT_NAME):"
  printf '%s\n' "$OUT" | tail -20 | sed 's/^/  /'
  exit "$RC"
fi
printf '%s\n' "$OUT" | grep -iE 'State:|OK' || true

rm -rf /backup/.staging && mkdir -p /backup/.staging
cp /tmp/export.zip /backup/.staging/export.zip
printf '%s' "$PROJECT_NAME" > /backup/.staging/.cp4d-project-name
printf '%s' "$PID"          > /backup/.staging/.cp4d-project-id
# atomic swap
rm -rf /backup/previous 2>/dev/null || true
[ -d /backup/current ] && mv /backup/current /backup/previous
mv /backup/.staging /backup/current
rm -rf /backup/previous 2>/dev/null || true
# Flush the page cache to the device. Kasten snapshots the block device and does not freeze
# the filesystem: without this, the snapshot can hold export.zip with the right name and 0 bytes.
sync
echo "export complete for $PID: export.zip $(stat -c %s /backup/current/export.zip) bytes on PVC"
