#!/usr/bin/env bash
# backupPrehook: start the watchdog, then stop InfluxDB and wait until its pod is gone.
# Usage: stop.sh <namespace> <deployment>
set -euo pipefail
echo "influxdb-simple scripts $(cat "$(dirname "$0")"/*.sh | sha256sum | cut -c1-12)"
NS="$1"; DEPLOY="$2"
ANN="blueprint-ai/influxdb-replicas"
WD=$("$(dirname "$0")/watchdog-name.sh" "$NS" "$DEPLOY")

JSON=$(kubectl get deploy "$DEPLOY" -n "$NS" -o json)
CURRENT=$(jq -r '.spec.replicas' <<<"$JSON")
SAVED=$(jq -r --arg a "$ANN" '.metadata.annotations[$a] // empty' <<<"$JSON")
SELECTOR=$(jq -r '.spec.selector.matchLabels | to_entries | map("\(.key)=\(.value)") | join(",")' <<<"$JSON")
# Optional: blueprint-ai/influxdb-watchdog-seconds on the Deployment. Raise it if your
# snapshots take longer than 10 minutes.
WATCHDOG_SECONDS=$(jq -r '.metadata.annotations["blueprint-ai/influxdb-watchdog-seconds"] // "600"' <<<"$JSON")

if [ "$CURRENT" -gt 0 ]; then
  kubectl annotate deploy "$DEPLOY" -n "$NS" --overwrite "$ANN=$CURRENT"
  echo "Saved replicas=$CURRENT in $ANN"
elif [ -n "$SAVED" ]; then
  # A previous run stopped it and did not start it again: keep its value.
  echo "Already at 0, keeping saved replicas=$SAVED"
else
  echo "Deployment is at 0 replicas and was stopped outside this blueprint; it will stay stopped"
fi

# Kasten does not run backupPosthook when a backup is cancelled. The watchdog starts
# InfluxDB again if backupPosthook has not done it within WATCHDOG_SECONDS.
# It runs with the service account of this pod, which can already scale the Deployment.
SA=$(kubectl get pod "$HOSTNAME" -n kasten-io -o jsonpath='{.spec.serviceAccountName}')
IMAGE=$(kubectl get pod "$HOSTNAME" -n kasten-io -o jsonpath='{.spec.containers[0].image}')
kubectl delete job "$WD" -n kasten-io --ignore-not-found --wait=true
kubectl create -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $WD
  namespace: kasten-io
  labels: {blueprint-ai/influxdb-watchdog: "true"}
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      serviceAccountName: $SA
      restartPolicy: OnFailure
      containers:
        - name: watchdog
          image: $IMAGE
          command: [bash, /scripts/watchdog.sh, "$NS", "$DEPLOY", "$WATCHDOG_SECONDS"]
          volumeMounts: [{name: scripts, mountPath: /scripts}]
      volumes:
        - name: scripts
          configMap: {name: influxdb-simple-scripts, defaultMode: 0755}
YAML
echo "Watchdog $WD will start InfluxDB after ${WATCHDOG_SECONDS}s if backupPosthook does not"

kubectl scale deploy "$DEPLOY" -n "$NS" --replicas=0
# The pod object is removed only after its volumes are unmounted, so once it is gone
# every write InfluxDB acknowledged is on the block device.
kubectl wait pod -n "$NS" -l "$SELECTOR" --for=delete --timeout=300s
LEFT=$(kubectl get pod -n "$NS" -l "$SELECTOR" -o name | wc -l)
if [ "$LEFT" -ne 0 ]; then
  echo "ERROR: $LEFT pod(s) still present for $SELECTOR"
  exit 1
fi
echo "InfluxDB $NS/$DEPLOY stopped, PVC ready for snapshot"
