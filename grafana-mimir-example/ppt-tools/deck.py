#!/usr/bin/env python3
"""Concept deck for grafana-mimir-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py grafana-mimir-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('grafana-mimir', 'grafana-mimir-example')

d.title("GRAFANA MIMIR WITH KASTEN",
        "One PVC holds all the durable state — so back up only that one")

d.agenda([
    "Where Mimir's durable state actually lives",
    "The pattern: snapshot the in-cluster MinIO PVC",
    "Why scoping the backup is mandatory, not tidy",
    "What a restore does and does not roll back",
    "Blueprint actions",
])

d.hero(
    "MIMIR KEEPS NOTHING", "DURABLE ON ITS OWN PVCs",
    body="Metrics, recording rules and Alertmanager state all live in object storage. "
         "The workload PVCs\nhold caches and working directories that are rebuilt from "
         "that storage.",
    pills=["TSDB blocks in mimir-tsdb", "Rules in mimir-ruler", "Caches are disposable"],
    icon='cloud_storage')

d.table_slide(
    "Where each kind of state lives",
    "The mimir-distributed chart deploys MinIO in-cluster by default, on a single PVC",
    ["Data", "Where it lives"],
    [
        ("Metrics — TSDB blocks", "Object storage, bucket mimir-tsdb"),
        ("Recording rules", "Object storage, bucket mimir-ruler"),
        ("Alertmanager state", "Object storage, bucket mimir-ruler"),
        ("Samples from roughly the last 2 hours",
         "Ingester in-memory head and WAL, and Kafka with ingest-storage"),
        ("store-gateway, compactor and querier working dirs",
         "Local caches, rebuilt from object storage"),
    ],
    col_widths=[4.6, 7.7],
    row_heights=[0.5, 0.58, 0.58, 0.58, 0.7, 0.7],
    cards=[
        ('database_storage', "BOTH BUCKETS, ONE PVC", [
            "The chart backs MinIO with a single PVC named mimir-minio, and both the "
            "mimir-tsdb and mimir-ruler buckets live on it. So one PVC snapshot "
            "captures every metrics block, every recording rule and the Alertmanager "
            "state. This is pattern 5 — a PVC-backed workload that exposes the S3 "
            "protocol."]),
        ('loop_left', "FLUSH FIRST, TO NARROW THE GAP", [
            "Ingesters hold recent samples in memory and in a WAL, uploading only every "
            "two hours or on shutdown. The prehook calls POST /ingester/flush with "
            "wait=true, which forces those series to object storage without stopping "
            "ingestion — so the snapshot is current to roughly the moment of backup."]),
    ])

d.hero(
    "DO NOT BACK UP", "THE WHOLE NAMESPACE",
    body="A full-namespace restore scales every workload to zero — including the "
         "rollout-operator that backs\nMimir's own fail-closed admission webhooks. The "
         "scale-downs are then rejected and the restore fails.",
    pills=["includeResources: app=minio", "Scoping is mandatory"],
    icon='error_warning', polarity='neg')

d.split_dark(
    "Why the scope is part of the design",
    "Scope it to MinIO.\n", "Everything else rebuilds.",
    [
        ("01", "WHAT THE WEBHOOKS GUARD",
         "mimir-rollout-operator installs fail-closed webhooks — no-downscale, "
         "prepare-downscale, pod-eviction, zpdb-validation — over the ingester and "
         "store-gateway StatefulSets, plus a min-time-between-zones-downscale "
         "annotation of 12 hours."),
        ("02", "SO TOUCH NO GUARDED WORKLOAD",
         "With includeResources: app=minio the restore point holds only the MinIO "
         "Deployment, its PVC, Service, Secret and ConfigMap. Kasten scales MinIO down "
         "and up to swap the PVC data, and the Mimir compute cluster keeps running "
         "untouched."),
        ("03", "AND LET HELM OWN THE COMPUTE",
         "The Mimir cluster is deployed independently through Helm or GitOps. Kasten "
         "restores only the object-storage data — which honours the principle that "
         "Kasten should not be responsible for restoring the operator itself."),
    ],
    left_body="This mirrors the Grafana maintainers' own guidance: point a Mimir "
              "cluster at the same bucket and it discovers all the blocks. The compute "
              "tier is stateless enough that rebuilding it is the cheaper and safer "
              "path.")

d.cards(
    "What a restore actually does",
    "This is a point-in-time recovery of the long-term store, not a rollback of live metrics",
    [
        ('history_clock', "HISTORICAL BLOCKS ARE REPAIRED", [
            "Only the MinIO PVC is restored, and restorePosthook restarts only the "
            "store-gateways and the compactor — never the ingesters.",
            "",
            "So the restore recovers or repairs historical blocks in the long-term "
            "store.",
        ]),
        ('loop_left', "LIVE METRICS KEEP FLOWING", [
            "Whatever the running ingesters hold in memory and in Kafka is not "
            "discarded. It keeps moving forward and is shipped to the restored bucket "
            "on the next flush.",
            "",
            "The compactor then vertically compacts any overlapping ranges, so the new "
            "data layers cleanly on top of the restored blocks.",
        ]),
        ('error_warning', "ONE HARD RULE FROM GRAFANA", [
            "Never run two sets of compactors against the same bucket at the same time.",
            "",
            "A single restored cluster has one compactor set, so the rule is satisfied "
            "here — but it is worth knowing before you improvise a second cluster "
            "pointed at the same bucket to inspect a restore.",
        ]),
    ])

d.table_slide(
    "Versions and blueprint actions",
    "The restore reports Complete only once the restored blocks are actually queryable",
    ["Action", "Runs", "What it does"],
    [
        ("backupPrehook", "Before the PVC snapshots",
         "Enumerates the Running ingester pods in the Mimir namespace and calls POST "
         "/ingester/flush?wait=true on port 8080 for each, forcing in-memory and WAL "
         "series to MinIO. Does not stop ingestion. Fails the backup if any ingester "
         "cannot be flushed."),
        ("restorePosthook", "After the MinIO PVC is restored",
         "Deletes the store-gateway and compactor pods so they re-read the restored "
         "bucket, then waits up to 15 minutes for them to become Ready. A "
         "store-gateway is Ready only after its initial bucket sync, so a completed "
         "wait means the blocks are loaded and queryable. Idempotent."),
    ],
    col_widths=[2.4, 2.6, 7.3],
    row_heights=[0.5, 1.5, 1.6],
    body_size=10.5,
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.32 on EKS, Kasten 8.5.13, Helm chart mimir-distributed 6.1.0, "
            "Grafana Mimir 3.1.2 as the chart appVersion."]),
        ('checkbox_multiple', "WHY THE WAIT MATTERS", [
            "Without it the RestoreAction would report success while the store-gateways "
            "were still syncing, so a user querying immediately after a green restore "
            "would see missing data and conclude the backup was bad. Waiting for Ready "
            "makes the reported status mean what people assume it means."]),
    ])

d.ending("ONE PVC, SCOPED DELIBERATELY",
         "Back up the object store. Let Helm rebuild the compute. Never the whole "
         "namespace.\ngithub.com/kastenhq/blueprint-ai — grafana-mimir-example")

d.save()
