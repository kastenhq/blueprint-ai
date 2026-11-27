#!/usr/bin/env bash
# Expected: heart_rate 10 points, sum 655; steps 10 points, sum 5500.
set -euo pipefail
NS="${1:-influxdb3-simple-test}"; DEPLOY="${2:-influxdb3}"
kubectl exec -n "$NS" deploy/"$DEPLOY" -- influxdb3 query --database testdb \
  "SELECT 'heart_rate' AS m, count(*) AS points, sum(bpm) AS total FROM heart_rate
   UNION ALL SELECT 'steps', count(*), sum(count) FROM steps"
