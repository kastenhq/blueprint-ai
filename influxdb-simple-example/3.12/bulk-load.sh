#!/usr/bin/env bash
# Step 6 only: loads random points into the database "bench".
# Usage: bulk-load.sh <namespace> <points> <start-epoch-ms> <step-ms>
# 8 series (device=d0..d7); point i is at start + floor(i/8) * step. Random float
# values, so the data does not compress or deduplicate artificially. Sent in
# batches of 100,000 lines.
set -euo pipefail
NS="$1"; POINTS="$2"; START="$3"; STEP="$4"
kubectl exec -n "$NS" deploy/influxdb3 -- influxdb3 create database bench 2>/dev/null || true
kubectl exec -n "$NS" deploy/influxdb3 -- bash -c '
  set -euo pipefail
  N='"$POINTS"'; START='"$START"'; STEP='"$STEP"'; B=100000
  for ((off = 0; off < N; off += B)); do
    awk -v off=$off -v n=$(( N - off < B ? N - off : B )) -v start=$START -v step=$STEP "BEGIN {
      srand(off)
      for (k = 0; k < n; k++) { i = off + k
        printf \"metrics,device=d%d hr=%.6f,spo2=%.6f,stress=%di %.0f\n\", i % 8, 40 + rand() * 140, 85 + rand() * 15, int(rand() * 100), start + int(i / 8) * step
      }
    }" > /tmp/bench.lp
    influxdb3 write --database bench --precision ms --file /tmp/bench.lp >/dev/null
  done
  rm -f /tmp/bench.lp
  influxdb3 query --database bench "SELECT count(*) AS points FROM metrics"
'
