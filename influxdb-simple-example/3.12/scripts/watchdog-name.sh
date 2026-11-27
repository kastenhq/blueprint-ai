#!/usr/bin/env bash
# Prints the deterministic watchdog Job name for <namespace> <deployment>.
echo "influxdb-watchdog-$(printf '%s/%s' "$1" "$2" | sha256sum | cut -c1-10)"
