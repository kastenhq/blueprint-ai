#!/usr/bin/env bash
# Step 6 only: loads random points into the database "bench".
# Usage: bulk-load.sh <namespace> <points> <start-epoch-seconds>
# 8 series (device=d0..d7), one point per series every 10 s from <start>, random
# float values, so the data does not compress or deduplicate artificially.
# InfluxDB 1.x default retention policy uses 7-day shards: 3,000,000 points span
# 3,000,000 / 8 * 10 s = 43 days, about 7 shards.
set -euo pipefail
NS="$1"; POINTS="$2"; START="$3"
kubectl exec -i -n "$NS" deploy/influxdb -- sh -c '
  influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -execute "CREATE DATABASE bench"
  awk -v n="'"$POINTS"'" -v start="'"$START"'" "BEGIN {
    srand(); print \"# DML\"; print \"# CONTEXT-DATABASE: bench\"
    for (i = 0; i < n; i++) {
      printf \"metrics,device=d%d hr=%.6f,spo2=%.6f,stress=%di %d\n\", i % 8, 40 + rand() * 140, 85 + rand() * 15, int(rand() * 100), start + int(i / 8) * 10
    }
  }" > /tmp/bench.lp
  influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -precision s -pps 0 -import -path /tmp/bench.lp 2>&1 | tail -2
  rm -f /tmp/bench.lp
  influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -database bench -execute "SELECT count(hr) FROM metrics"
'
