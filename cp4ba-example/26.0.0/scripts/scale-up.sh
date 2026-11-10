#!/usr/bin/env bash
# Start a CP4BA namespace again after scale-down.sh (and, for a restore, after the Kasten
# restore of the PVCs).
#
# Usage: scale-up.sh [NAMESPACE]          (default: cp4ba-starter)
#
# Order:
#   1. Start ibm-pg-operator, then wake up the Postgres clusters and wait until they are ready.
#      The CP4BA workloads need their databases, so the databases come first.
#   2. Scale every other Deployment and StatefulSet back to its saved count.
#   3. Scale the remaining operators back. They find workloads that already run, so they have
#      nothing to undo. ibm-cp4a-operator re-creates the pods owned by the ICP4ACluster.
#   4. Remove the blueprint-ai/replicas annotations.
#   5. Resume the CronJobs that scale-down.sh suspended (annotation blueprint-ai/suspended).
#      Last, so that no backup Job runs against an application that is still starting.
#
# Only workloads that carry the blueprint-ai/replicas annotation are scaled. A workload that was
# already at 0 before scale-down.sh therefore stays at 0. In the same way, a CronJob that was
# already suspended before scale-down.sh stays suspended.
#
# Works with the bash 3.2 shipped with macOS.
set -euo pipefail

NS="${1:-cp4ba-starter}"
ANN="blueprint-ai/replicas"
HIB="pg.ibm.com/hibernation"
TIMEOUT=900   # seconds to wait for Postgres

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Scale a workload back to the count saved in its annotation.
scale_up_saved() {
  local w="$1" r
  r=$(kubectl get "$w" -n "$NS" -o jsonpath="{.metadata.annotations.blueprint-ai/replicas}")
  if [ -z "$r" ]; then
    log "  $w has no saved count, left as is"
    return
  fi
  kubectl scale "$w" -n "$NS" --replicas="$r" >/dev/null
  log "  $w: -> $r"
}

# Workloads with a saved count, as kind/name. $1 = "operators" or "workloads".
saved() {
  local filter
  if [ "$1" = "operators" ]; then filter='!= null'; else filter='== null'; fi
  kubectl get deploy,sts -n "$NS" -o json | jq -r "
    .items[]
    | select(.metadata.annotations[\"$ANN\"] != null)
    | select(.metadata.labels[\"olm.owner\"] $filter)
    | \"\(.kind | ascii_downcase)/\(.metadata.name)\""
}

# --- 1. Postgres -----------------------------------------------------------------------------
log "1. Start ibm-pg-operator and wake up the Postgres clusters"
scale_up_saved deploy/ibm-pg-operator
kubectl rollout status deploy/ibm-pg-operator -n "$NS" --timeout="${TIMEOUT}s" >/dev/null

CLUSTERS=$(kubectl get clusters.pg.ibm.com -n "$NS" -o name)

# A Kasten restore re-creates the PVCs without their ownerReferences. The operator only counts
# the PVCs owned by the Cluster, so without the reference it sees none and reports "Cluster is
# unrecoverable and needs manual intervention". Put the reference back on every Postgres PVC
# that has lost it. The PVC label pg.ibm.com/cluster names its Cluster. Without a restore, every
# PVC already has its owner and this loop does nothing.
kubectl get pvc -n "$NS" -l pg.ibm.com/cluster -o json \
  | jq -r '.items[] | select((.metadata.ownerReferences // []) | length == 0)
           | "\(.metadata.name) \(.metadata.labels["pg.ibm.com/cluster"])"' \
  | while read -r pvc cluster; do
      uid=$(kubectl get clusters.pg.ibm.com "$cluster" -n "$NS" -o jsonpath='{.metadata.uid}')
      kubectl patch pvc "$pvc" -n "$NS" --type=merge -p "{\"metadata\":{\"ownerReferences\":[{
        \"apiVersion\":\"pg.ibm.com/v1\",\"kind\":\"Cluster\",\"name\":\"$cluster\",
        \"uid\":\"$uid\",\"controller\":true,\"blockOwnerDeletion\":true}]}}" >/dev/null
      log "  pvc/$pvc: ownerReference to cluster $cluster restored"
    done

for c in $CLUSTERS; do
  # Removing the annotation ends the hibernation.
  kubectl annotate "$c" -n "$NS" "$HIB-" >/dev/null 2>&1 || true
  log "  $c: hibernation removed"
done
for c in $CLUSTERS; do
  kubectl wait "$c" -n "$NS" --for=condition=Ready --timeout="${TIMEOUT}s" >/dev/null
  log "  $c is ready"
done

# --- 2. Workloads ----------------------------------------------------------------------------
log "2. Scale up the Deployments and StatefulSets"
for w in $(saved workloads); do
  scale_up_saved "$w"
done

# --- 3. Operators ----------------------------------------------------------------------------
log "3. Scale up the operators"
for w in $(saved operators); do
  [ "$w" = "deployment/ibm-pg-operator" ] && continue   # already started in step 1
  scale_up_saved "$w"
done

# --- 4. Clean up -----------------------------------------------------------------------------
log "4. Remove the saved counts"
for w in $(kubectl get deploy,sts -n "$NS" -o json \
           | jq -r ".items[] | select(.metadata.annotations[\"$ANN\"] != null)
                    | \"\(.kind | ascii_downcase)/\(.metadata.name)\""); do
  kubectl annotate "$w" -n "$NS" "$ANN-" >/dev/null
done

# --- 5. CronJobs -----------------------------------------------------------------------------
log "5. Resume the CronJobs suspended by scale-down.sh"
for cj in $(kubectl get cronjob -n "$NS" -o json \
            | jq -r '.items[] | select(.metadata.annotations["blueprint-ai/suspended"] == "true")
                     | .metadata.name'); do
  kubectl patch cronjob "$cj" -n "$NS" -p '{"spec":{"suspend":false}}' >/dev/null
  kubectl annotate cronjob "$cj" -n "$NS" "blueprint-ai/suspended-" >/dev/null
  log "  cronjob/$cj resumed"
done
log "Done. The operators now reconcile; CPE and Navigator take several minutes to become ready."
