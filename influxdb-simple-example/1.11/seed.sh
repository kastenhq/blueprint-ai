#!/usr/bin/env bash
# Writes 20 known points into testdb (10 heart_rate, 10 steps), at fixed
# timestamps so the verification output is the same on every run.
set -euo pipefail
NS="${1:-influxdb-simple-test}"
LINES=""
for i in $(seq 1 10); do
  TS=$((1767225600 + i * 60))   # 2026-01-01T00:00:00Z + i minutes
  LINES+="heart_rate,device=watch bpm=$((60 + i)) ${TS}"$'\n'
  LINES+="steps,device=watch count=$((i * 100))i ${TS}"$'\n'
done
kubectl exec -i -n "$NS" deploy/influxdb -- sh -c \
  'influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -database testdb -precision s -import -path /dev/stdin' \
  <<<"# DML
# CONTEXT-DATABASE: testdb
${LINES}"
