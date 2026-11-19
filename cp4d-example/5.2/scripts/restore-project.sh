#!/usr/bin/env bash
# Recreate ONE CP4D project from a restored backup PVC (mounted at /backup).
# Runs in a pod started by restore-projects.sh, one pod per restored PVC.
#
#   - The project name is read from the bundle (/backup/current/.cp4d-project-name).
#   - If no project has that name, the project is recreated under the same name.
#   - If a project with that name exists, it is NOT touched: the backup is restored
#     beside it, as "<name>-restored-<RESTORE_STAMP>".
#   - If the import fails, the project this script created is deleted again, so a
#     failed restore does not leave an empty project behind.
#
# Loaded from the cp4d-backup-scripts ConfigMap (see README), not baked into the image.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${RESTORE_STAMP:?RESTORE_STAMP is required}"   # same suffix for every project of one restore
: "${ENCRYPTION_KEY:=}"
B=/backup/current

[ -f "$B/export.zip" ] || { echo "FAILED: no bundle at $B/export.zip — is this a cp4d backup PVC?"; exit 1; }
NAME=$(cat "$B/.cp4d-project-name" 2>/dev/null || true)
OLD_PID=$(cat "$B/.cp4d-project-id" 2>/dev/null || true)
[ -n "$NAME" ] || { echo "FAILED: $B/.cp4d-project-name is missing or empty"; exit 1; }
echo "bundle: project '$NAME' (original id ${OLD_PID:-unknown}), $(stat -c %s "$B/export.zip") bytes"

"$HERE/cpdctl-login.sh"

# Does a project with this exact name exist? (CP4D does not require unique names.)
EXISTING=$(cpdctl project list --name "$NAME" --match exact --output json 2>/dev/null \
             | sed -n '/^{/,$p' | jq -r '.resources[]?.metadata.guid')
if [ -n "$EXISTING" ]; then
  TARGET="${NAME}-restored-${RESTORE_STAMP}"
  echo "a project named '$NAME' exists ($(echo $EXISTING | tr '\n' ' ')): it is left untouched;"
  echo "restoring beside it as '$TARGET'"
else
  TARGET="$NAME"
  echo "no project named '$NAME': recreating it under the same name"
fi

OUT=$(cpdctl project create --name "$TARGET" --type cpd --storage-type assetfiles 2>&1) \
  || { echo "FAILED: could not create project '$TARGET':"; printf '%s\n' "$OUT" | sed 's/^/  /'; exit 1; }
NEWPID=$(printf '%s' "$OUT" | grep -oE '/v2/projects/[a-f0-9-]+' | head -1 | sed 's#.*/##')
[ -n "$NEWPID" ] || { echo "FAILED: project create gave no id:"; printf '%s\n' "$OUT" | sed 's/^/  /'; exit 1; }
echo "created project '$TARGET' ($NEWPID)"

if ! TARGET_PID="$NEWPID" IMPORT_FILE="$B/export.zip" ENCRYPTION_KEY="$ENCRYPTION_KEY" \
       "$HERE/import-project.sh"; then
  echo "FAILED: import into '$TARGET' ($NEWPID) failed; deleting that project again"
  cpdctl project delete --project-id "$NEWPID" >/dev/null 2>&1 \
    || echo "  !! could not delete $NEWPID — delete it by hand"
  exit 1
fi
echo "RESTORED: '$NAME' -> '$TARGET' ($NEWPID)"
