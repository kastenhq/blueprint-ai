#!/usr/bin/env bash
# Kasten RESTORE action hook (spec.hooks.onSuccess of a RestoreAction): recreate in CP4D every project
# whose backup PVC Kasten has just restored into RESTORE_NS.
#
# The user restores the restore point of the backup namespace into a NEW namespace, picks
# only the PVCs of the projects they want, and selects this blueprint's restoreProjects
# action as the post-restore hook. Kasten restores the PVCs first, then runs this script.
#
# For each restored project PVC, one pod runs restore-project.sh, which recreates the
# project under its own name, or as "<name>-restored-<date>" when that name is taken.
#
# This script runs in kasten-io (Kasten's ServiceAccount). It refuses to run in a namespace
# that this blueprint backs up: a restore there would bring back EVERY project PVC, and
# importing all of them is never what the user wants.
#
# Exit non-zero (=> the RestoreAction fails) if the namespace is refused, if it holds no
# project PVC, or if any project could not be restored. The details are in this log.
set -euo pipefail
: "${RESTORE_NS:?RESTORE_NS is required}"
: "${IMAGE:?IMAGE (keeper image) is required}"
: "${BLUEPRINT:=cp4d-projects-backup-bp}"
: "${KEEPER_SA:=cp4d-backup-keeper}"
: "${MAX_PARALLEL:=4}"
: "${IMPORT_TIMEOUT:=900}"
: "${SCRIPTS_CM:=cp4d-backup-scripts}"
: "${SCRIPTS_NS:=kasten-io}"
: "${ENCRYPTION_KEY:=}"
: "${CRED_NS:=cpd}"
: "${CRED_SECRET:=cp4d-backup-cpdctl-creds}"

# Environment (all overridable; defaults above):
#   RESTORE_NS      (required) namespace Kasten restored the PVCs into (the hook's namespace)
#   IMAGE           (required) keeper image run by each restore pod
#   BLUEPRINT       name of this blueprint; used to find the policies that back up with it
#   KEEPER_SA       ServiceAccount that exists only in the backup namespace (second check)
#   MAX_PARALLEL    max restore pods running concurrently
#   IMPORT_TIMEOUT  max seconds to wait for one restore pod
#   SCRIPTS_CM      ConfigMap holding these scripts (copied into RESTORE_NS for the pods)
#   SCRIPTS_NS      namespace of that ConfigMap (the blueprint mounts it from there)
#   ENCRYPTION_KEY  must match the key used at backup time, if any
#   CRED_NS         namespace holding the credential secret (read cross-namespace)
#   CRED_SECRET     name of that secret (keys: url, username, apikey)

HERE="$(cd "$(dirname "$0")" && pwd)"
echo "scripts from ConfigMap ${SCRIPTS_NS}/${SCRIPTS_CM}: sha256 $(cat "$HERE"/*.sh | sha256sum | cut -c1-12)"
echo "restore namespace: ${RESTORE_NS}"

# --- 1. refuse a namespace that this blueprint backs up ---------------------------------
# A Kasten policy selects namespaces with a label selector, where the key
# k10.kasten.io/appNamespace means "the namespace name". Every namespace carries the
# label kubernetes.io/metadata.name=<name>, so swapping the key lets kubectl evaluate the
# policy's own selector against the restore namespace. No namespace name is hardcoded.
kubectl get policies.config.kio.kasten.io -n kasten-io -o json \
  | jq -c --arg bp "${BLUEPRINT}" '.items[]
      | select([.spec.actions[]?.backupParameters.hooks.preHook.blueprint] | index($bp))
      | {name: .metadata.name, selector: (.spec.selector // {})}' > /tmp/policies.jsonl
while read -r line; do
  pname=$(jq -r .name <<<"$line")
  sel=$(jq -r '.selector
      | ([(.matchLabels // {}) | to_entries[] | "\(.key)=\(.value)"]
         + [(.matchExpressions // [])[]
            | if   .operator == "In"           then "\(.key) in (\(.values | join(",")))"
              elif .operator == "NotIn"        then "\(.key) notin (\(.values | join(",")))"
              elif .operator == "Exists"       then "\(.key)"
              elif .operator == "DoesNotExist" then "!\(.key)"
              else "UNSUPPORTED:\(.operator)" end])
      | join(",")' <<<"$line" | sed 's#k10\.kasten\.io/appNamespace#kubernetes.io/metadata.name#g')
  case "$sel" in *UNSUPPORTED:*)
    echo "RESTORE HOOK FAILED: policy ${pname} uses a selector this hook cannot evaluate: ${sel}"
    exit 1 ;;
  esac
  if [ -z "$sel" ]; then
    matched=yes     # a policy with no selector protects every namespace
  else
    # kubectl refuses a name and a selector together, so list by selector, then look for
    # the name. A kubectl error must fail the hook, not read as "no match".
    nslist=$(kubectl get ns -l "$sel" -o name) \
      || { echo "RESTORE HOOK FAILED: cannot evaluate the selector of policy ${pname}: ${sel}"; exit 1; }
    if grep -qx "namespace/${RESTORE_NS}" <<<"$nslist"; then matched=yes; else matched=no; fi
  fi
  if [ "$matched" = yes ]; then
    echo "RESTORE HOOK REFUSED: namespace ${RESTORE_NS} is backed up by policy ${pname}"
    echo "  (it runs ${BLUEPRINT}). Restoring here would bring back every project PVC."
    echo "  Restore the restore point into a NEW namespace, keep only the PVCs of the"
    echo "  projects you want, and run this hook there."
    exit 1
  fi
done < /tmp/policies.jsonl
if kubectl get serviceaccount "${KEEPER_SA}" -n "${RESTORE_NS}" >/dev/null 2>&1; then
  echo "RESTORE HOOK REFUSED: namespace ${RESTORE_NS} contains the ServiceAccount ${KEEPER_SA},"
  echo "  which only the backup namespace has. Restore into a NEW namespace, and restore"
  echo "  only the PVCs of the projects you want."
  exit 1
fi

# --- 2. the restored project PVCs -------------------------------------------------------
mapfile -t PVCS < <(kubectl get pvc -n "${RESTORE_NS}" -l app.kubernetes.io/managed-by=cp4d-projects-backup \
                      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
if [ "${#PVCS[@]}" -eq 0 ]; then
  echo "RESTORE HOOK FAILED: no cp4d project PVC in ${RESTORE_NS}"
  echo "  (label app.kubernetes.io/managed-by=cp4d-projects-backup). Did the restore include"
  echo "  at least one project PVC?"
  exit 1
fi
echo "found ${#PVCS[@]} project PVC(s): ${PVCS[*]}"

# --- 3. what the pods need in RESTORE_NS: scripts and credentials -----------------------
# A pod cannot mount a ConfigMap or a Secret from another namespace, so both are copied.
# The credential copy is deleted when this script exits, whatever the outcome.
kubectl get configmap "${SCRIPTS_CM}" -n "${SCRIPTS_NS}" -o json \
  | jq --arg ns "${RESTORE_NS}" '{apiVersion, kind, data,
        metadata: {name: .metadata.name, namespace: $ns,
                   labels: {"app.kubernetes.io/managed-by": "cp4d-projects-backup"}}}' \
  | kubectl apply -f - >/dev/null
TMP_SECRET="cp4d-restore-creds"
cleanup(){ kubectl delete secret "${TMP_SECRET}" -n "${RESTORE_NS}" --ignore-not-found >/dev/null 2>&1 || true; }
trap cleanup EXIT
kubectl get secret "${CRED_SECRET}" -n "${CRED_NS}" -o json \
  | jq --arg ns "${RESTORE_NS}" --arg n "${TMP_SECRET}" '{apiVersion, kind, type,
        data: {url: .data.url, username: .data.username, apikey: .data.apikey},
        metadata: {name: $n, namespace: $ns}}' \
  | kubectl apply -f - >/dev/null

# --- 4. one restore pod per PVC, bounded concurrency -----------------------------------
STAMP=$(date -u +%Y%m%d-%H%M)
# Kasten can run this hook again after a transient error (seen: a Kanister API call that
# failed mid-run). A second run must not import a project twice, so the state is kept on
# the cluster: a PVC already restored carries the annotation cp4d.io/restored-as and is
# skipped, and a pod left by a previous run is reused rather than started again.
key_of(){ echo "$1" | md5sum | cut -c1-8; }
launch(){ # pvc -> pod name on stdout
  local pvc="$1" pod
  pod="restore-$(key_of "$pvc")-$(date +%s)"
  kubectl create -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  namespace: ${RESTORE_NS}
  labels: { cp4d.io/restore-pvc: "$(key_of "$pvc")", app.kubernetes.io/managed-by: cp4d-projects-backup }
  annotations: { cp4d.io/pvc: "${pvc}" }
spec:
  restartPolicy: Never
  # Restricted pod: the pod only reads the bundle and calls the CP4D API. Without this, the
  # pod can get a privileged SCC, because Kasten's ServiceAccount, which creates it, has
  # access to all of them. On OpenShift the restricted SCC then assigns a random non-root uid.
  securityContext:
    runAsNonRoot: true
    seccompProfile: { type: RuntimeDefault }
  containers:
    - name: restore
      image: ${IMAGE}
      imagePullPolicy: Always
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: { drop: [ALL] }
      command: ["/scripts/restore-project.sh"]
      env:
        - { name: HOME,           value: /tmp }   # random uid: HOME=/ is not writable, cpdctl panics on mkdir /.bluemix
        - { name: RESTORE_STAMP,  value: "${STAMP}" }
        - { name: ENCRYPTION_KEY, value: "${ENCRYPTION_KEY}" }
        - { name: CPD_URL,      valueFrom: { secretKeyRef: { name: ${TMP_SECRET}, key: url } } }
        - { name: CPD_USERNAME, valueFrom: { secretKeyRef: { name: ${TMP_SECRET}, key: username } } }
        - { name: CPD_APIKEY,   valueFrom: { secretKeyRef: { name: ${TMP_SECRET}, key: apikey } } }
      volumeMounts:
        - { name: backup,  mountPath: /backup, readOnly: true }
        - { name: scripts, mountPath: /scripts }
  volumes:
    - name: backup
      persistentVolumeClaim: { claimName: ${pvc} }
    - name: scripts
      configMap: { name: ${SCRIPTS_CM}, defaultMode: 0755 }
EOF
  echo "$pod"
}
# pod_for PVC -> the pod to wait on: a pod of a previous run that is still pending or
# running, else a new one. Prints "done <pod>" when a previous pod already succeeded.
pod_for(){
  local pvc="$1" line
  line=$(kubectl get pod -n "${RESTORE_NS}" -l cp4d.io/restore-pvc="$(key_of "$pvc")" \
           --sort-by=.metadata.creationTimestamp \
           -o jsonpath='{range .items[*]}{.metadata.name} {.status.phase} {.metadata.deletionTimestamp}{"\n"}{end}' \
         | awk 'NF==2' | tail -1)
  case "${line#* }" in
    Succeeded)       echo "done ${line%% *}" ;;
    Pending|Running) echo "${line%% *}" ;;
    *)               launch "$pvc" ;;
  esac
}
# wait_pod POD -> 0 if Succeeded. Stops on Failed at once; prints the pod log either way.
wait_pod(){
  local p="$1" ph deadline
  deadline=$(( $(date +%s) + ${IMPORT_TIMEOUT%s} ))
  while :; do
    ph=$(kubectl get pod "$p" -n "${RESTORE_NS}" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)
    case "$ph" in Succeeded|Failed) break ;; esac
    [ "$(date +%s)" -ge "$deadline" ] && { ph="$ph (timeout after ${IMPORT_TIMEOUT%s}s)"; break; }
    sleep 5
  done
  echo "--- $(kubectl get pod "$p" -n "${RESTORE_NS}" -o jsonpath='{.metadata.annotations.cp4d\.io/pvc}') (pod $p, phase $ph)"
  kubectl logs "$p" -n "${RESTORE_NS}" 2>/dev/null | sed 's/^/    /'
  [ "$ph" = Succeeded ]
}
# mark_restored PVC POD: record on the PVC what the pod restored, so a re-run skips it.
mark_restored(){
  local as
  as=$(kubectl logs "$2" -n "${RESTORE_NS}" 2>/dev/null | sed -n "s/^RESTORED: .* -> '\(.*\)' (\(.*\))$/\1 (\2)/p" | tail -1)
  kubectl annotate pvc "$1" -n "${RESTORE_NS}" --overwrite cp4d.io/restored-as="${as:-unknown}" >/dev/null
}

FAILED=(); RESTORED=(); TODO=()
for pvc in "${PVCS[@]}"; do
  as=$(kubectl get pvc "$pvc" -n "${RESTORE_NS}" -o jsonpath='{.metadata.annotations.cp4d\.io/restored-as}')
  if [ -n "$as" ]; then
    echo "--- ${pvc}: already restored by a previous run, as ${as}; skipped"
    RESTORED+=("$pvc")
  else
    TODO+=("$pvc")
  fi
done
for ((i = 0; i < ${#TODO[@]}; i += MAX_PARALLEL)); do
  pods=(); pvcs=("${TODO[@]:i:MAX_PARALLEL}")
  for pvc in "${pvcs[@]}"; do pods+=("$(pod_for "$pvc")"); done
  for j in "${!pods[@]}"; do
    pod="${pods[$j]#done }"
    if wait_pod "$pod"; then
      mark_restored "${pvcs[$j]}" "$pod"; RESTORED+=("${pvcs[$j]}")
    else
      FAILED+=("${pvcs[$j]}")
    fi
  done
done
# --wait=false: a pod stuck on an unreachable node must not block the end of the hook.
kubectl delete pod -n "${RESTORE_NS}" -l cp4d.io/restore-pvc --wait=false >/dev/null 2>&1 || true

echo "restored ${#RESTORED[@]} of ${#PVCS[@]} project(s)"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "RESTORE HOOK FAILED: ${#FAILED[@]} project(s) could not be restored: ${FAILED[*]}"
  exit 1
fi
echo "OK: every restored PVC in ${RESTORE_NS} was imported into CP4D."
echo "    When you have checked the projects, delete the namespace ${RESTORE_NS}."
