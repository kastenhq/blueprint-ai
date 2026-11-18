#!/usr/bin/env bash
# Import a CP4D project from a restored backup PVC (mounted at /backup) into an
# EXISTING target project. The target project must already be created by the caller
# (restore.sh handles create + the delete-and-replace confirmation).
#
# The bundle is the zip produced by the CP4D export service, stored as is by
# export-project.sh. It goes back to CP4D unchanged (--import-file).
# Loaded from the cp4d-backup-scripts ConfigMap (see README), not baked into the image.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/cpdctl-login.sh"
: "${TARGET_PID:?TARGET_PID (target project guid) is required}"
: "${IMPORT_FILE:=/backup/current/export.zip}"
: "${ENCRYPTION_KEY:=}"     # must match the key used at export time, if any

[ -f "$IMPORT_FILE" ] || { echo "no bundle at $IMPORT_FILE"; exit 1; }
echo "importing $IMPORT_FILE ($(stat -c %s "$IMPORT_FILE") bytes) into project $TARGET_PID"
ENC=()
[ -n "$ENCRYPTION_KEY" ] && ENC=(--encryption-key "$ENCRYPTION_KEY")
cpdctl asset import start --project-id "$TARGET_PID" --import-file "$IMPORT_FILE" \
  "${ENC[@]}" | grep -iE 'State:|OK'
echo "import complete into $TARGET_PID"
