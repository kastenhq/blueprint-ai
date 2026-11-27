#!/usr/bin/env bash
# Runs in a Job created by stop.sh. Starts InfluxDB again if it is still stopped
# after the delay, which means backupPosthook did not run or did not finish
# (observed when a backup is cancelled).
# Usage: watchdog.sh <namespace> <deployment> <seconds>
set -euo pipefail
echo "influxdb-simple scripts $(cat "$(dirname "$0")"/*.sh | sha256sum | cut -c1-12)"
NS="$1"; DEPLOY="$2"; DELAY="$3"
ANN="blueprint-ai/influxdb-replicas"
sleep "$DELAY"
JSON=$(kubectl get deploy "$DEPLOY" -n "$NS" -o json)
CURRENT=$(jq -r '.spec.replicas' <<<"$JSON")
SAVED=$(jq -r --arg a "$ANN" '.metadata.annotations[$a] // empty' <<<"$JSON")
if [ "$CURRENT" = 0 ] && [ -n "$SAVED" ]; then
  echo "WATCHDOG: $NS/$DEPLOY still stopped after ${DELAY}s, starting it with replicas=$SAVED"
  kubectl scale deploy "$DEPLOY" -n "$NS" --replicas="$SAVED"
  kubectl rollout status deploy "$DEPLOY" -n "$NS" --timeout=600s
  kubectl annotate deploy "$DEPLOY" -n "$NS" "$ANN-"
elif [ -n "$SAVED" ]; then
  # backupPosthook scaled it up but was stopped (cancelled) before it removed the annotation.
  echo "WATCHDOG: $NS/$DEPLOY already running (replicas=$CURRENT), removing the stale $ANN"
  kubectl annotate deploy "$DEPLOY" -n "$NS" "$ANN-"
else
  echo "WATCHDOG: $NS/$DEPLOY replicas=$CURRENT, nothing to do"
fi
