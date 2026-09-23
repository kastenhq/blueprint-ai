#!/usr/bin/env python3
"""Concept deck for psmdb-percona-operator-pbm-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py psmdb-percona-operator-pbm-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('psmdb-pbm', 'psmdb-percona-operator-pbm-example')

d.title("SHARDED PERCONA MONGODB\nWITH PBM AND A MINIO KEEPER",
        "Let the operator own cross-shard consistency; Kasten owns the archive")

d.agenda([
    "Why sharding changes the answer",
    "The pattern: pbm to a local MinIO keeper",
    "Why raw PVC snapshots fail across shards",
    "Blueprint actions",
    "Restore",
])

d.hero(
    "SHARDING BREAKS", "THE QUIESCE APPROACH",
    body="Quiescing every shard's secondary at the same instant, tracking each PVC name, "
         "and excluding\nthe non-quiesced volumes per shard at restore time is "
         "prohibitively complex.",
    pills=["Simultaneous quiesce", "Many PVC names", "Per-shard exclusions"],
    icon='error_warning', polarity='neg')

d.cards(
    "Two blueprints, two topologies",
    "Pick by whether the cluster is sharded — not by preference",
    [
        ('node_tree', "UNSHARDED: QUIESCE A REPLICA", [
            "psmdb-percona-operator-example uses pattern 1 — quiesce a secondary with "
            "db.fsyncLock() — and targets unsharded replica sets.",
            "",
            "That blueprint also starts by telling you not to deploy it if your storage "
            "already gives crash-consistent snapshots.",
        ]),
        ('checkbox_multiple', "SHARDED: DELEGATE TO PBM", [
            "This blueprint hands the whole problem to the PSMDB operator. Percona "
            "Backup for MongoDB takes a cluster-wide consistent snapshot covering every "
            "shard and the config server replica set atomically.",
            "",
            "The blueprint becomes a thin orchestrator — create a CR, wait, emit the "
            "name. Restore is just as short.",
        ]),
    ])

d.flow(
    "Pattern 5 — pbm backup via a local MinIO keeper",
    "Kasten snapshots only the MinIO PVC; the PSMDB data PVCs are excluded by policy filter",
    [
        ('database_storage', "SHARDED PSMDB CLUSTER",
         "Config replica set, shard\nreplica sets and mongos, each\nwith its own PVCs — "
         "all\nexcluded from the snapshot"),
        ('upload_cloud', "backupPrehook: BACKUP CR",
         "Creates a\nPerconaServerMongoDBBackup CR\nto trigger a pbm logical\nbackup "
         "across all shards"),
        ('server_hardware', "MINIO KEEPER",
         "A MinIO Deployment with a\npermanent PVC holds the pbm\narchive, inside the "
         "namespace"),
        ('shield_check', "KASTEN SNAPSHOTS THE MINIO PVC",
         "A self-consistent pbm\narchive: a full logical backup\nof every shard and the\n"
         "config server"),
    ],
    note=("Why this beats snapshotting the data PVCs directly.",
          "Snapshotting the raw MongoDB volumes across shards captures each shard at a "
          "slightly different instant. The restored cluster is then inconsistent: "
          "cross-shard transactions go missing and the config server's chunk metadata "
          "disagrees with the shards. pbm produces one point-in-time view spanning every "
          "replica set instead."))

d.table_slide(
    "Versions",
    "The blueprint is a thin orchestrator — the operator does the difficult part",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.4"),
        ("PSMDB Operator / Helm chart", "1.22.0 / psmdb-db 1.22.2"),
        ("Percona Server for MongoDB", "8.0.19-7"),
        ("Percona Backup for MongoDB", "2.12.0"),
        ("MinIO", "RELEASE.2025-04-22T22-12-26Z"),
    ],
    col_widths=[3.4, 2.45],
    row_heights=[0.55] * 7,
    side=('checkbox_multiple', "WHAT DELEGATION BUYS",
          "pbm performs a cluster-wide consistent snapshot that covers all shards and "
          "the config server replica set atomically. That is the hard part of protecting "
          "a sharded cluster, and the operator already solves it.\n"
          "The blueprint therefore does very little: it creates a CR, polls a status "
          "field, and emits a name. There is no per-shard bookkeeping, no simultaneous "
          "quiesce, and no PVC exclusion list to keep in step with the topology.\n"
          "That also means the blueprint does not have to change when you add a shard. "
          "The operator's backup covers the new shard automatically, and Kasten still "
          "snapshots exactly one PVC.\n"
          "Kasten contributes what pbm does not: encryption at rest, immutability at the "
          "backup target, incremental transfer, and reach to targets pbm cannot write to."))

d.table_slide(
    "Blueprint actions",
    "Create a CR, poll a status, emit an artifact — and the same shape again on restore",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Creates a PerconaServerMongoDBBackup CR to trigger a pbm logical backup "
         "across all shards, polls until status.state reaches ready, and emits the "
         "backup CR name, the pbm internal name, the cluster name and the namespace as "
         "restore-point artifacts."),
        ("restorePosthook",
         "Waits for the PSMDB cluster to reach ready after Kasten restores the CR and "
         "the MinIO PVC, creates a PerconaServerMongoDBRestore CR referencing the "
         "artifact backup name, then polls until status.state reaches ready."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.5, 1.3],
    cards=[
        ('upload_cloud', "WHY THE ARTIFACTS MATTER", [
            "The restore side must name the exact pbm backup to replay. Carrying the "
            "backup name as a restore-point artifact is what ties a Kasten restore "
            "point to the pbm archive inside it — rather than guessing at the newest "
            "backup in the repository, which may not be the one that was snapshotted."]),
        ('scan_target', "POLICY FILTER REQUIRED", [
            "The backup policy must exclude the PSMDB data PVCs so that only the MinIO "
            "PVC is snapshotted. Snapshotting the shard volumes as well would put "
            "inconsistent per-shard state into the restore point alongside the good "
            "archive."]),
    ])

d.ending("THE OPERATOR MAKES IT CONSISTENT, KASTEN MAKES IT SAFE",
         "pbm spans the shards. Kasten snapshots the one PVC that holds the result.\n"
         "github.com/kastenhq/blueprint-ai — psmdb-percona-operator-pbm-example")

d.save()
