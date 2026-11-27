#!/usr/bin/env bash
# backupPosthook and restorePosthook: start InfluxDB with the saved replica count,
# wait until it is ready, then delete the watchdog.
# Usage: start.sh <namespace> <deployment>
set -euo pipefail
echo "influxdb-simple scripts $(cat "$(dirname "$0")"/*.sh | sha256sum | cut -c1-12)"
NS="$1"; DEPLOY="$2"
ANN="blueprint-ai/influxdb-replicas"
WD=$("$(dirname "$0")/watchdog-name.sh" "$NS" "$DEPLOY")

SAVED=$(kubectl get deploy "$DEPLOY" -n "$NS" -o json \
  | jq -r --arg a "$ANN" '.metadata.annotations[$a] // empty')
if [ -n "$SAVED" ]; then
  kubectl scale deploy "$DEPLOY" -n "$NS" --replicas="$SAVED"
else
  echo "No $ANN annotation: replicas left as they are"
fi
kubectl rollout status deploy "$DEPLOY" -n "$NS" --timeout=600s
# Remove the annotation only once InfluxDB is ready, so a failed start can be
# retried (by Kasten or by the watchdog) with the same value.
if [ -n "$SAVED" ]; then
  kubectl annotate deploy "$DEPLOY" -n "$NS" "$ANN-"
fi
kubectl delete job "$WD" -n kasten-io --ignore-not-found
echo "InfluxDB $NS/$DEPLOY running"
