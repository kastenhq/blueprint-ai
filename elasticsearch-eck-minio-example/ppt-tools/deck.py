#!/usr/bin/env python3
"""Concept deck for elasticsearch-eck-minio-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py elasticsearch-eck-minio-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('elasticsearch-eck-minio', 'elasticsearch-eck-minio-example')

d.title("ELASTICSEARCH ON ECK\nWITH AN S3 KEEPER",
        "The native S3 snapshot repository, kept local to the namespace")

d.agenda([
    "How this differs from the filesystem-repository blueprint",
    "The pattern and the keeper's tools sidecar",
    "Snapshot lifecycle: one per restore point",
    "Blueprint actions",
])

d.hero(
    "NO RWX PVC,", "NO SHARED FILESYSTEM",
    body="Elasticsearch writes its native snapshots to a RustFS keeper over S3 "
         "instead of to a shared path.\nKasten snapshots the keeper PVC, capturing the "
         "whole archive in every restore point.",
    pills=["No RWX requirement", "Per-restore-point snapshots", "Block storage is enough"],
    icon='cloud_storage')

d.cards(
    "When to choose this over the filesystem-repository blueprint",
    "Both protect the same workload — the difference is what they ask of your storage",
    [
        ('close_circle', "WHAT THE OTHER ONE NEEDS", [
            "elasticsearch-eck-example uses pattern 3: a permanent repository PVC "
            "mounted by every Elasticsearch node.",
            "",
            "The Elasticsearch filesystem repository requires all master and data nodes "
            "to reach the same path at once, so that PVC must be ReadWriteMany — and "
            "the RWX class must also support CSI snapshots.",
        ]),
        ('checkbox_multiple', "WHAT THIS ONE NEEDS", [
            "Only ordinary block storage for a single keeper PVC. Nothing is mounted "
            "across the Elasticsearch nodes at all.",
            "",
            "Choose it when you do not want an RWX repository PVC spanning every node, "
            "or when you want each restore point to carry its own Elasticsearch "
            "snapshot rather than sharing one.",
        ]),
    ])

d.flow(
    "Pattern 5 — a local S3 keeper (RustFS)",
    "Elasticsearch talks S3 to RustFS in the same namespace; Kasten snapshots only the keeper PVC",
    [
        ('database_storage', "ELASTICSEARCH, 3 NODES",
         "Its own data PVCs are not\nthe restore source and are\nexcluded from the "
         "snapshot"),
        ('upload_cloud', "NATIVE S3 SNAPSHOT",
         "Elasticsearch writes to the\nkasten-repo S3 repository,\nbacked by the RustFS "
         "keeper"),
        ('server_hardware', "RUSTFS KEEPER + TOOLS SIDECAR",
         "es-cluster-minio, with a\npermanent PVC holding\n/data/es-snapshots/"),
        ('shield_check', "KASTEN SNAPSHOTS THE KEEPER PVC",
         "One snapshot holding the\nfull Elasticsearch snapshot\narchive"),
    ],
    note=("Why the keeper carries a tools sidecar.",
          "The blueprint calls the Elasticsearch HTTP API and must emit artifacts with "
          "kando, which the S3 image lacks. A kanister-tools sidecar to the keeper pod gives KubeExec "
          "somewhere to run from, with no custom image to build and no tooling added to "
          "Elasticsearch itself. Workload identity — the ES cluster name and kind — is "
          "carried as labels on the keeper."))

d.hero(
    "NO CLEANUP DURING BACKUP", "SO SNAPSHOTS SURVIVE",
    body="Each backup creates a new, timestamped Elasticsearch snapshot and deletes "
         "nothing.\nThe delete action retires one only when Kasten retires the restore "
         "point that owns it.",
    pills=["kasten-<timestamp>", "One per restore point", "Retired with the restore point"],
    icon='history_clock')

d.table_slide(
    "Versions and blueprint actions",
    "Four actions — and the real work happens in restorePosthook, not in the backup",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "KubeExec into the keeper's tools sidecar; PUT a new native snapshot "
         "kasten-<timestamp> on the Elasticsearch API, check state=SUCCESS, run sync, "
         "then emit the snapshot name as the kastenSnapshot artifact."),
        ("backupPosthook",
         "A no-op, kept as a placeholder for symmetry."),
        ("restorePosthook",
         "Where recovery actually happens. Waits for the cluster to be reachable after "
         "Kasten restores the keeper PVC, re-registers the repository idempotently, "
         "deletes the live copies of the snapshot's user indices, then POSTs the "
         "restore with include_global_state: false and polls to completion."),
        ("delete",
         "Called when Kasten retires a restore point. Receives the kastenSnapshot "
         "artifact and issues DELETE for that snapshot. Idempotent — treats "
         "snapshot_missing_exception as success."),
    ],
    col_widths=[2.6, 9.7],
    row_heights=[0.5, 1.15, 0.5, 1.4, 0.95],
    body_size=10.5,
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.35 on kind, Kasten 9.0.11, ECK operator 2.16.1, Elasticsearch "
            "8.17.0, RustFS 1.0.7 (first tested on EKS with MinIO)."]),
        ('checkbox_multiple', "ONE RESTORE PATH, TWO SITUATIONS", [
            "The restorePosthook works identically whether the cluster is still live — "
            "an in-place restore — or has just been rebuilt from scratch by ECK in a "
            "disaster-recovery scenario. That is worth knowing before you rehearse it: "
            "you only have one path to test."]),
    ])

d.ending("THE ARCHIVE IS AN OBJECT STORE, NOT A SHARED DISK",
         "Elasticsearch speaks S3 to a keeper next door. Kasten snapshots one PVC.\n"
         "github.com/kastenhq/blueprint-ai — elasticsearch-eck-minio-example")

d.save()
