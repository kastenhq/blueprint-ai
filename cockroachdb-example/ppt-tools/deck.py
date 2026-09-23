#!/usr/bin/env python3
"""Concept deck for cockroachdb-example. Content only — the slide machinery lives
in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py cockroachdb-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('cockroachdb', 'cockroachdb-example')

d.title("COCKROACHDB WITH KASTEN\nAND A LOCAL MINIO KEEPER",
        "The database's own BACKUP writes to MinIO; Kasten snapshots the MinIO PVC")

d.agenda([
    "Why snapshotting the node PVCs does not work",
    "The pattern: a local S3 endpoint in the namespace",
    "The naming convention the blueprint depends on",
    "Blueprint actions",
    "Restore",
])

d.hero(
    "A PER-NODE SNAPSHOT", "IS NOT A RESTORABLE COPY",
    body="CockroachDB splits data into ranges and replicates them across nodes with "
         "Raft.\nEach node's PVC holds only a partial, replicated slice of the cluster.",
    pills=["Distributed SQL", "Raft replication", "No consistent disk state"],
    icon='error_warning', polarity='neg')

d.cards(
    "Patterns considered and rejected",
    "The workload's architecture eliminates most of the list before preference applies",
    [
        ('close_circle', "QUIESCE OR FENCE (1-2)", [
            "There is no way to freeze CockroachDB's distributed Raft state into a "
            "disk snapshot that can be restored.",
            "",
            "Freezing one node does not give you a consistent view of the cluster.",
        ]),
        ('close_circle', "PERMANENT PVC DUMP (3-4)", [
            "BACKUP targets an S3 object API, not a filesystem path.",
            "",
            "The nodelocal:// scheme does write to disk, but it scatters files across "
            "all nodes — so no single PVC holds a complete backup.",
        ]),
        ('close_circle', "TEMPORARY PVC (7-9)", [
            "Those patterns break the single-responsibility and open/closed principles "
            "and cannot use a BlueprintBinding.",
            "",
            "Here they are also strictly worse than a permanent MinIO PVC, which costs "
            "nothing extra.",
        ]),
        ('close_circle', "VENDOR DATA MOVER (11)", [
            "The CockroachDB Kubernetes operator exposes no backup CRD.",
            "",
            "Backups are driven entirely through SQL, so there is no operator API to "
            "delegate to.",
        ]),
    ],
    style='negative', body_size=11)

d.flow(
    "Pattern 5 — a local MinIO keeper",
    "Kasten snapshots only the MinIO PVC; the CockroachDB data PVCs are filtered out",
    [
        ('database_storage', "COCKROACHDB STATEFULSET",
         "Three nodes, each with its\nown datadir PVC — all\nexcluded from the snapshot\n"
         "by a policy filter"),
        ('upload_cloud', "backupPrehook: BACKUP INTO",
         "KubeExec into a CockroachDB\npod runs a transactionally\nconsistent BACKUP to "
         "an\ns3:// URL"),
        ('server_hardware', "MINIO KEEPER",
         "A MinIO Deployment in the\napplication namespace is the\nlocal S3 endpoint, "
         "backed by\na permanent PVC"),
        ('shield_check', "KASTEN SNAPSHOTS THE MINIO PVC",
         "That one snapshot holds a\nself-consistent CockroachDB\nbackup collection"),
    ],
    note=("Incrementality comes for free, twice over.",
          "BACKUP INTO an existing collection produces an incremental backup rather "
          "than a full one, and Kasten's PVC snapshots are themselves incremental. The "
          "blueprint binds to the MinIO Deployment — the keeper — not to CockroachDB."))

d.table_slide(
    "Versions and the naming convention",
    "The convention is required: the blueprint derives every name from the keeper Deployment",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.9"),
        ("CockroachDB", "v24.3.33"),
        ("MinIO", "RELEASE.2025-04-22T22-12-26Z"),
        ("Tool image", "kasten-tools:8.5.9"),
    ],
    col_widths=[3.0, 2.85],
    row_heights=[0.55] * 6,
    side=('node_tree', "NAMES DERIVED FROM THE KEEPER",
          "<cluster> is the keeper Deployment name with the -minio suffix removed.\n"
          "CockroachDB StatefulSet: <cluster>, for example cockroachdb\n"
          "CockroachDB pod label: app=<cluster>\n"
          "MinIO Deployment, Service and PVC: <cluster>-minio\n"
          "MinIO credentials Secret: <cluster>-minio-creds, keys rootUser and "
          "rootPassword\n"
          "MinIO bucket: <cluster>-backups, created by the blueprint if absent\n"
          "MinIO credentials must be URL-safe — alphanumeric only. They are embedded in "
          "the s3:// URL query string, so special characters would need URL-encoding."))

d.table_slide(
    "Blueprint actions",
    "The BACKUP and RESTORE SQL runs inside a CockroachDB pod, so the tool image needs no database client",
    ["Action", "When called", "What it does"],
    [
        ("backupPrehook", "Before the PVC snapshot",
         "Ensures the MinIO bucket exists; discovers user databases, excluding system, "
         "defaultdb and postgres; runs BACKUP DATABASE ... INTO the MinIO collection; "
         "syncs the MinIO PVC; outputs the databases, bucket and namespace artifacts."),
        ("restorePosthook", "After the MinIO PVC is restored",
         "Waits for the MinIO keeper and for a ready CockroachDB pod; discovers the "
         "databases in the LATEST backup; DROP DATABASE IF EXISTS each one, then "
         "RESTORE DATABASE ... FROM LATEST IN the collection."),
        ("delete", "On restore-point deletion",
         "An informational no-op. The backup collection lives on the MinIO PVC, whose "
         "lifecycle is governed by Kasten snapshot retention."),
    ],
    col_widths=[2.3, 2.7, 7.3],
    row_heights=[0.5, 1.5, 1.5, 0.95],
    body_size=10.5)

d.steps_dark(
    "Deploy and run", "Four steps to a restore point",
    [
        ('rocket_launch', "DEPLOY THE MINIO KEEPER",
         "kubectl apply -f minio-keeper.yaml creates the credentials Secret, a 10Gi PVC, "
         "the Deployment labelled cockroachdb-minio, and the Service."),
        ('database_storage', "DEPLOY COCKROACHDB",
         "A three-node StatefulSet named after the keeper, minus the -minio suffix, with "
         "pods labelled app=<cluster>."),
        ('settings_gear', "DEPLOY THE BLUEPRINT",
         "The Blueprint and BlueprintBinding go in kasten-io. The binding targets the "
         "MinIO Deployment, not CockroachDB."),
        ('upload_cloud', "BACK UP, EXCLUDING THE NODE PVCs",
         "The policy must filter out the CockroachDB datadir PVCs so only the MinIO PVC "
         "is snapshotted — snapshotting the node volumes would capture unrestorable "
         "partial state."),
    ])

d.ending("THE BACKUP IS AN OBJECT, NOT A DISK",
         "CockroachDB writes a consistent collection to MinIO. Kasten snapshots that "
         "one PVC.\ngithub.com/kastenhq/blueprint-ai — cockroachdb-example")

d.save()
