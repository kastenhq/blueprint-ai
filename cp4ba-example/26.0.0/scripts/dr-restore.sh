#!/usr/bin/env bash
# Disaster recovery of a CP4BA namespace from a Kasten EXPORTED restore point, after the namespace
# was deleted (for example by IBM's cp4a-clean-up.sh). Do not run IBM's install scripts first:
# the restore point brings back the operators (through OLM) and the whole deployment.
#
# Usage: [FROM_STEP=N] dr-restore.sh NAMESPACE RESTORE_POINT_CONTENT
#   FROM_STEP: resume a run that stopped, from step N (default 1). Every step can be run again.
#   RESTORE_POINT_CONTENT: the exported one, from
#   kubectl get restorepointcontent -l k10.kasten.io/appNamespace=NAMESPACE
#
# Assumes the cluster-scoped prerequisites are still there (CP4BA CRDs, cert-manager, IBM
# Licensing): this is the recovery of a deleted namespace on the same cluster.
#
# Order, and why:
#   1. Create the namespace with the UID range and SELinux label saved in the restore point. The
#      files on the PVCs belong to that UID range; a new range makes PostgreSQL fail on chmod.
#   2. Restore the OLM objects only (CatalogSources, OperatorGroup, Subscriptions). Kasten
#      restores the Subscriptions WITH their status and their olm.generated-by annotation, which
#      name InstallPlans that no longer exist; OLM then installs nothing. Both are removed.
#   3. Wait until OLM has installed every operator of the backup.
#   4. Restore CommonService, OperandConfig and OperandRegistry while ibm-common-service-operator
#      runs: its webhook (failurePolicy Fail) must accept them.
#   5. scale-down.sh stops every operator. ibm-pg-operator is started again: its webhook must
#      accept the PostgreSQL Clusters. It creates nothing by itself.
#   6. Delete what the operators created during the few minutes they ran (a new CA, keys, ...).
#      Otherwise cert-manager compares the backup's Secrets with a fresh CertificateRequest,
#      reissues the CA with a new key, and the restore fails on an immutable CertificateRequest.
#   7. Restore everything else, overwriting. The PVCs come back with their data, the workloads at
#      0 replicas, the Postgres clusters hibernated and the CronJobs suspended: the backup was
#      taken while scale-down.sh had stopped everything.
#   8. scale-up.sh.
#
# Works with the bash 3.2 shipped with macOS.
set -euo pipefail

NS="${1:?namespace}"
RPC="${2:?exported restore point content}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FROM="${FROM_STEP:-1}"
RP="dr-$(date +%Y%m%d%H%M)"
TIMEOUT=1800
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Create a RestoreAction from stdin and wait until it ends. $1 = generateName prefix.
restore() {
  local name state waited=0
  name=$(kubectl create -f - -o jsonpath='{.metadata.name}')
  log "  restoreaction/$name started"
  while :; do
    state=$(kubectl get restoreaction "$name" -n "$NS" -o jsonpath='{.status.state}')
    [ "$state" = Complete ] && { log "  restoreaction/$name Complete"; return 0; }
    if [ "$state" = Failed ]; then
      log "ERROR: restoreaction/$name Failed:"
      kubectl get restoreaction "$name" -n "$NS" -o jsonpath='{.status.error}' | sed 's/\\//g' \
        | grep -oE '"message":"[^"]*"' | sort -u
      exit 1
    fi
    [ "$waited" -ge "$TIMEOUT" ] && { log "ERROR: restoreaction/$name still $state"; exit 1; }
    sleep 15; waited=$((waited + 15))
  done
}

# Header of a RestoreAction on our RestorePoint. $1 = generateName prefix.
action_header() {
  cat <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata:
  generateName: $1-
  namespace: $NS
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: $RP
    namespace: $NS
  targetNamespace: $NS
EOF
}

step() { [ "$FROM" -le "$1" ]; }

if step 1 && kubectl get ns "$NS" >/dev/null 2>&1; then
  echo "Namespace $NS exists. This script recovers a deleted namespace; for a restore into a" >&2
  echo "running deployment, follow the restore procedure of the README instead." >&2
  exit 1
fi

kubectl get --raw "/apis/apps.kio.kasten.io/v1alpha1/restorepointcontents/$RPC/details" \
  > "$WORK/details.json"
specs() { jq -c '.status.restorePointContentDetails.artifacts[].meta.spec | select(.config != null)' "$WORK/details.json"; }

if ! step 1; then
  RP=$(kubectl get restorepoint -n "$NS" -o json | jq -r --arg c "$RPC" \
       '.items[] | select(.spec.restorePointContentRef.name == $c) | .metadata.name' | head -1)
  [ -n "$RP" ] || { echo "No RestorePoint in $NS points to $RPC" >&2; exit 1; }
fi

# --- 1. Namespace ----------------------------------------------------------------------------
if step 1; then
log "1. Create namespace $NS with the UID range and SELinux label of the backup"
specs | jq -s '.[] | select(.type=="namespace") | .config | fromjson
  | {apiVersion:"v1", kind:"Namespace", metadata:{name:.metadata.name,
     annotations:(.metadata.annotations | with_entries(select(.key|startswith("openshift.io/sa.scc."))))}}' \
  | kubectl create -f - >/dev/null
kubectl get ns "$NS" -o json | jq -r '.metadata.annotations | to_entries[]
  | select(.key|startswith("openshift.io/sa.scc.")) | "  \(.key)=\(.value)"'
kubectl create -f - >/dev/null <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata:
  name: $RP
  namespace: $NS
spec:
  restorePointContentRef:
    name: $RPC
EOF
log "  restorepoint/$RP -> $RPC"

fi

# --- 2. OLM objects --------------------------------------------------------------------------
if step 2; then
log "2. Restore the OLM objects"
{ action_header dr-1-olm; cat <<EOF
  filters:
    includeResources:
    - {group: operators.coreos.com, resource: catalogsources}
    - {group: operators.coreos.com, resource: operatorgroups}
    - {group: operators.coreos.com, resource: subscriptions}
EOF
} | restore
for s in $(kubectl get subscription -n "$NS" -o name); do
  kubectl patch "$s" -n "$NS" --subresource=status --type=json \
    -p '[{"op":"remove","path":"/status"}]' >/dev/null 2>&1 || true
  kubectl annotate "$s" -n "$NS" olm.generated-by- >/dev/null 2>&1 || true
done
log "  stale status and olm.generated-by removed from the Subscriptions"

fi

# --- 3. Operators ----------------------------------------------------------------------------
if step 3; then
want=$(specs | jq -s '[.[] | select(.resource=="clusterserviceversions")] | length')
log "3. Wait until OLM has installed the $want operators of the backup"
waited=0
while n=$(kubectl get csv -n "$NS" -o json | jq '[.items[] | select(.status.phase=="Succeeded")] | length') \
      && [ "$n" -lt "$want" ]; do
  [ "$waited" -ge "$TIMEOUT" ] && { log "ERROR: only $n/$want CSVs Succeeded"; exit 1; }
  sleep 20; waited=$((waited + 20))
done
log "  $n/$want CSVs Succeeded"

fi

# --- 4. CommonService ------------------------------------------------------------------------
if step 4; then
log "4. Restore the CommonService objects while ibm-common-service-operator runs"
kubectl rollout status deploy/ibm-common-service-operator -n "$NS" --timeout=600s >/dev/null
{ action_header dr-2-commonservice; cat <<EOF
  overwriteExisting: true
  filters:
    includeResources:
    - {group: operator.ibm.com, resource: commonservices}
    - {group: operator.ibm.com, resource: operandconfigs}
    - {group: operator.ibm.com, resource: operandregistries}
EOF
} | restore

fi

# --- 5. Stop the operators -------------------------------------------------------------------
if step 5; then
log "5. Stop the operators (scale-down.sh), then start ibm-pg-operator for its webhook"
"$HERE/scale-down.sh" "$NS" | sed 's/^/  /'
kubectl scale deploy/ibm-pg-operator -n "$NS" --replicas=1 >/dev/null
kubectl rollout status deploy/ibm-pg-operator -n "$NS" --timeout=600s >/dev/null

fi

# The webhook serving certificates that OLM generated for this installation, one Secret per
# operator with a webhook (<operator>-service-cert). They carry no label and no ownerReference;
# OLM creates a Role of the same name owned by the CSV. The copies in the backup are signed by
# the CA of the OLM that installed the operators the first time, so restoring them breaks the
# webhooks with "x509: certificate signed by unknown authority".
olm_certs() {
  kubectl get roles -n "$NS" -o json | jq -r '.items[]
    | select([.metadata.ownerReferences[]?.kind] | index("ClusterServiceVersion"))
    | select(.metadata.name | endswith("-service-cert")) | .metadata.name'
}

# --- 6. Delete what the operators created ----------------------------------------------------
if step 6; then
log "6. Delete the objects the operators created by themselves"
# Also run when resuming after a failed step 7: it removes what that attempt restored, so that
# the new attempt does not have to update a CertificateRequest (cert-manager forbids it).
keep=$(olm_certs | jq -R . | jq -s .)
for r in deployments statefulsets services secrets configmaps \
         certificates.cert-manager.io certificaterequests.cert-manager.io issuers.cert-manager.io; do
  kubectl get "$r" -n "$NS" -o json | jq -r --arg r "$r" --argjson keep "$keep" '.items[]
    | select(($r == "secrets" and (.metadata.name as $n | $keep | index($n))) | not)
    | select((.metadata.labels // {}) | has("olm.owner") | not)
    | select(([.metadata.ownerReferences[]?.kind]
              | map(. == "ClusterServiceVersion" or . == "CatalogSource" or . == "InstallPlan"
                    or . == "ServiceAccount") | any) | not)
    | select(.type != "kubernetes.io/service-account-token" and .type != "kubernetes.io/dockercfg")
    | select(.metadata.name | test("^(kube-root-ca\\.crt|openshift-service-ca\\.crt)$") | not)
    | "\($r) \(.metadata.name)"'
done | while read -r r n; do
  # --ignore-not-found: deleting a Certificate also deletes its CertificateRequests.
  kubectl delete "$r" "$n" -n "$NS" --wait=false --ignore-not-found >/dev/null
  log "  $r/$n deleted"
done

fi

# --- 7. Everything else ----------------------------------------------------------------------
if step 7; then
log "7. Restore everything else (PVCs, Secrets, CRs, workloads)"
{ action_header dr-3-all; cat <<EOF
  overwriteExisting: true
  filters:
    excludeResources:
    - {group: operators.coreos.com, resource: catalogsources}
    - {group: operators.coreos.com, resource: operatorgroups}
    - {group: operators.coreos.com, resource: subscriptions}
    - {group: operators.coreos.com, resource: clusterserviceversions}
    - {group: operators.coreos.com, resource: installplans}
    - {group: operators.coreos.com, resource: operatorconditions}
    - {group: operator.ibm.com, resource: commonservices}
    - {group: operator.ibm.com, resource: operandconfigs}
    - {group: operator.ibm.com, resource: operandregistries}
    - matchExpressions:
      - {key: olm.owner, operator: Exists}
EOF
  # Objects owned by a CSV but without the olm.owner label: OLM already made them.
  specs | jq -r 'select(.resource != "operatorconditions") | . as $s | (.config | fromjson) as $o
    | select([$o.metadata.ownerReferences[]?.kind] | index("ClusterServiceVersion"))
    | select(($o.metadata.labels // {}) | has("olm.owner") | not)
    | "    - {group: \"\($s.group // "")\", version: \($s.version), resource: \($s.resource // ($s.type + "s")), name: \($o.metadata.name)}"'
  for c in $(olm_certs); do
    echo "    - {group: \"\", version: v1, resource: secrets, name: $c}"
  done
} | restore

fi

# --- 8. Start ----------------------------------------------------------------------------------
if step 8; then
log "8. Start CP4BA (scale-up.sh)"
"$HERE/scale-up.sh" "$NS" | sed 's/^/  /'
fi
log "Done. Check the routes and certificates, then run the restore check of the README."
