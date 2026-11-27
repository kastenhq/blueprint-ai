#!/usr/bin/env bash
# Step 3 only: one test round of the "sync" or "stop" variant against influxdb3-simple-test.
# Usage: snapshot-round.sh <sync|stop> <round-number>   (needs jq; writer.yaml must be running)
# Prints RESULT=OK when the clone starts, seq is 1..N with no gap, and testdb has its 10 points.
# acked_before is read just AFTER the sync, so a few seconds of later writes can show as LOST_ACKED.
set -uo pipefail
NS=influxdb3-simple-test; MODE=$1; R=$2; SN=snap-$MODE-$R
if [ "$MODE" = stop ]; then T0=$(date +%s); kubectl scale deploy influxdb3 -n $NS --replicas=0 >/dev/null; kubectl wait pod -n $NS -l app=influxdb3 --for=delete --timeout=300s >/dev/null; fi
if [ "$MODE" = sync ]; then for t in 1 2 3; do kubectl exec -n $NS deploy/influxdb3 -- sync && break; [ $t = 3 ] && { echo "round=$R sync failed"; exit 1; }; sleep 2; done; fi
ACKED=$(kubectl exec -n $NS deploy/writer -- cat /tmp/acked)
kubectl apply -n $NS -f - >/dev/null <<Y
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata: {name: $SN}
spec: {volumeSnapshotClassName: rook-ceph-block-snapshot, source: {persistentVolumeClaimName: influxdb3}}
Y
kubectl wait volumesnapshot/$SN -n $NS --for=jsonpath='{.status.readyToUse}'=true --timeout=180s >/dev/null
if [ "$MODE" = stop ]; then kubectl scale deploy influxdb3 -n $NS --replicas=1 >/dev/null; kubectl rollout status deploy influxdb3 -n $NS --timeout=300s >/dev/null; echo "downtime=$(($(date +%s)-T0))s"; fi
AFTER=$(kubectl exec -n $NS deploy/writer -- cat /tmp/acked)
kubectl apply -n $NS -f - >/dev/null <<Y
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: check}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ceph-block
  resources: {requests: {storage: 2Gi}}
  dataSource: {name: $SN, kind: VolumeSnapshot, apiGroup: snapshot.storage.k8s.io}
Y
kubectl get deploy influxdb3 -n $NS -o json | jq '
  .metadata = {name: "check", labels: {app: "check"}} | del(.status) |
  .spec.selector.matchLabels = {app: "check"} | .spec.template.metadata.labels = {app: "check"} |
  .spec.template.spec.volumes[0].persistentVolumeClaim.claimName = "check"' | kubectl apply -n $NS -f - >/dev/null
if ! kubectl rollout status deploy/check -n $NS --timeout=120s >/dev/null; then
  echo "round=$R mode=$MODE acked=$ACKED RESULT=START_FAILED"; kubectl logs -n $NS deploy/check --tail=20
else
  Q=$(kubectl exec -n $NS deploy/check -- influxdb3 query --database stress --format csv \
        "SELECT count(*) AS n, min(seq) AS lo, max(seq) AS hi FROM stress" 2>&1 | grep -v "^$" | tail -1)
  T=$(kubectl exec -n $NS deploy/check -- influxdb3 query --database testdb --format csv \
        "SELECT count(*) FROM heart_rate" 2>&1 | grep -v "^$" | tail -1)
  IFS=, read -r N LO HI <<<"$Q"
  WARN=$(kubectl logs -n $NS deploy/check | grep -ciE "corrupt|skipp|failed|panic|error")
  OK=OK; { [ "$N" = "$HI" ] && [ "$LO" = 1 ]; } || OK=GAP
  [ "${HI:-0}" -ge "$ACKED" ] 2>/dev/null || OK="$OK,LOST_ACKED"
  [ "$T" = 10 ] || OK="$OK,TESTDB_BAD"
  [ "$WARN" = 0 ] || OK="$OK,LOG_ERRORS($WARN)"
  echo "round=$R mode=$MODE acked_before=$ACKED acked_after=$AFTER restored: n=$N lo=$LO hi=$HI testdb=$T RESULT=$OK"
  [ "$WARN" = 0 ] || kubectl logs -n $NS deploy/check | grep -iE "corrupt|skipp|failed|panic|error" | head -3 | cut -c1-250
fi
kubectl delete deploy check -n $NS --wait >/dev/null; kubectl wait --for=delete pod -l app=check -n $NS --timeout=120s >/dev/null 2>&1
kubectl delete pvc check -n $NS --wait >/dev/null; kubectl delete volumesnapshot $SN -n $NS >/dev/null
