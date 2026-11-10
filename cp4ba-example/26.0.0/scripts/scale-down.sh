#!/usr/bin/env bash
# Stop everything in a CP4BA namespace so that no pod references a PVC.
#
# Usage: scale-down.sh [NAMESPACE]        (default: cp4ba-starter)
#
# Order matters:
#   1. Suspend the CronJobs, so that no new Job starts while the namespace stops. Without this,
#      icp4adeploy-rr-backup (every 5 minutes) creates a pod that mounts icp4adeploy-dba-rr-pvc,
#      and Kasten cannot replace that PVC during a restore.
#   2. Hibernate the Postgres clusters (pg.ibm.com). Only ibm-pg-operator acts on the hibernation
#      annotation, so this must happen while that operator still runs.
#   3. Scale down the operators (Deployments installed by OLM, label olm.owner), so they cannot
#      scale the workloads back up.
#   4. Scale down every other Deployment and StatefulSet.
#   5. Delete the pods that the ICP4ACluster creates directly (no Deployment, for example
#      icp4adeploy-dba-rr-*). ibm-cp4a-operator re-creates them after scale-up.sh.
#   6. Delete the Jobs whose pods mount a PVC, finished ones included: a pod in phase Succeeded
#      still keeps its PVC from being deleted (observed on this cluster).
#   7. Check that no pod at all references a PVC.
#
# Saved state, read back by scale-up.sh:
#   - blueprint-ai/replicas on each Deployment and StatefulSet: its replica count. A workload
#     already at 0 is skipped, so running the script twice does not overwrite a saved count with 0.
#   - blueprint-ai/suspended=true on each CronJob this script suspended. A CronJob that was
#     already suspended gets no annotation and stays suspended after scale-up.sh.
#
# Works with the bash 3.2 shipped with macOS.
set -euo pipefail

NS="${1:-cp4ba-starter}"
ANN="blueprint-ai/replicas"
CJ_ANN="blueprint-ai/suspended"
HIB="pg.ibm.com/hibernation"
TIMEOUT=600   # seconds to wait for pods to stop

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Save the replica count of a workload, then scale it to 0.
save_and_scale_down() {
  local w="$1" r
  r=$(kubectl get "$w" -n "$NS" -o jsonpath='{.spec.replicas}')
  if [ "${r:-0}" = "0" ]; then
    log "  $w already at 0, skipped"
    return
  fi
  kubectl annotate "$w" -n "$NS" "$ANN=$r" --overwrite >/dev/null
  kubectl scale "$w" -n "$NS" --replicas=0 >/dev/null
  log "  $w: $r -> 0"
}

# Wait until no pod matches the label selector.
wait_no_pods() {
  local selector="$1" waited=0
  while [ -n "$(kubectl get pods -n "$NS" -l "$selector" -o name)" ]; do
    if [ "$waited" -ge "$TIMEOUT" ]; then
      log "ERROR: pods with $selector still exist after ${TIMEOUT}s"
      kubectl get pods -n "$NS" -l "$selector"
      exit 1
    fi
    sleep 5; waited=$((waited + 5))
  done
}

# Pods in any phase that reference at least one PVC, as "pod phase owner-kind pvc,pvc".
# Every phase counts: a Pending or Succeeded pod also blocks the deletion of its PVC.
pods_with_pvcs() {
  kubectl get pods -n "$NS" -o json | jq -r '
    .items[]
    | {name: .metadata.name, phase: .status.phase,
       owner: ((.metadata.ownerReferences // [])[0].kind // "none"),
       pvcs: [.spec.volumes[]? | .persistentVolumeClaim.claimName // empty]}
    | select(.pvcs | length > 0)
    | "\(.name) \(.phase) \(.owner) \(.pvcs | join(","))"'
}

# --- 1. CronJobs -----------------------------------------------------------------------------
log "1. Suspend the CronJobs"
for cj in $(kubectl get cronjob -n "$NS" -o json \
            | jq -r '.items[] | select(.spec.suspend != true) | .metadata.name'); do
  kubectl annotate cronjob "$cj" -n "$NS" "$CJ_ANN=true" --overwrite >/dev/null
  kubectl patch cronjob "$cj" -n "$NS" -p '{"spec":{"suspend":true}}' >/dev/null
  log "  cronjob/$cj suspended"
done

# --- 2. Hibernate Postgres -------------------------------------------------------------------
log "2. Hibernate the Postgres clusters"
PG_OP=deploy/ibm-pg-operator
if [ "$(kubectl get "$PG_OP" -n "$NS" -o jsonpath='{.spec.replicas}')" = "0" ]; then
  # A previous run stopped it. It is needed to process the annotation.
  r=$(kubectl get "$PG_OP" -n "$NS" -o jsonpath="{.metadata.annotations.blueprint-ai/replicas}")
  log "  ibm-pg-operator is at 0, starting it with ${r:-1} replica(s)"
  kubectl scale "$PG_OP" -n "$NS" --replicas="${r:-1}" >/dev/null
fi
kubectl rollout status "$PG_OP" -n "$NS" --timeout="${TIMEOUT}s" >/dev/null

for c in $(kubectl get clusters.pg.ibm.com -n "$NS" -o name); do
  kubectl annotate "$c" -n "$NS" "$HIB=on" --overwrite >/dev/null
  log "  $c: hibernation=on"
done
wait_no_pods "pg.ibm.com/cluster"
log "  all Postgres pods are gone"

# --- 3. Operators ----------------------------------------------------------------------------
log "3. Scale down the operators"
for w in $(kubectl get deploy -n "$NS" -l olm.owner -o name); do
  save_and_scale_down "$w"
done

# --- 4. Everything else ----------------------------------------------------------------------
log "4. Scale down the other Deployments and StatefulSets"
for w in $(kubectl get deploy,sts -n "$NS" -o json \
           | jq -r '.items[] | select(.metadata.labels["olm.owner"] == null)
                    | "\(.kind | ascii_downcase)/\(.metadata.name)"'); do
  save_and_scale_down "$w"
done

# --- 5. Pods created directly by the ICP4ACluster ----------------------------------------------
log "5. Delete the pods owned by the ICP4ACluster"
for p in $(kubectl get pods -n "$NS" -o json \
           | jq -r '.items[] | select((.metadata.ownerReferences // [])[0].kind == "ICP4ACluster")
                    | .metadata.name'); do
  kubectl delete pod "$p" -n "$NS" --wait=false >/dev/null
  log "  pod/$p deleted"
done

# --- 6. Jobs that mount a PVC ----------------------------------------------------------------
log "6. Delete the Jobs whose pods mount a PVC"
for j in $(kubectl get jobs -n "$NS" -o json \
           | jq -r '.items[]
                    | select([.spec.template.spec.volumes[]? | .persistentVolumeClaim.claimName // empty]
                             | length > 0)
                    | .metadata.name'); do
  # The default propagation of kubectl delete removes the Job's pods too.
  kubectl delete job "$j" -n "$NS" --wait=false >/dev/null
  log "  job/$j deleted"
done

# --- 7. Check --------------------------------------------------------------------------------
log "7. Wait until no pod references a PVC"
waited=0
while left=$(pods_with_pvcs) && [ -n "$left" ]; do
  if [ "$waited" -ge "$TIMEOUT" ]; then
    log "ERROR: these pods still reference PVCs after ${TIMEOUT}s (pod phase owner pvcs):"
    echo "$left"
    exit 1
  fi
  sleep 5; waited=$((waited + 5))
done
log "Done: no pod in $NS references a PVC"
