#!/usr/bin/env bash
# Expected: heart_rate count=10 sum=655, steps count=10 sum=5500.
set -euo pipefail
NS="${1:-influxdb-simple-test}"
kubectl exec -n "$NS" deploy/influxdb -- sh -c \
  'influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -database testdb \
   -execute "SELECT count(bpm), sum(bpm) FROM heart_rate; SELECT count(count), sum(count) FROM steps"'
