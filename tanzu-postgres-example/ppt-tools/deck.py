#!/usr/bin/env python3
"""Concept deck for tanzu-postgres-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py tanzu-postgres-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('tanzu-postgres', 'tanzu-postgres-example')

d.title("TANZU FOR POSTGRES\nWITH A MINIO KEEPER",
        "Aim the operator's own pgBackRest at a local MinIO, and snapshot that PVC")

d.agenda([
    "The pattern: redirect the vendor's backup engine locally",
    "Why the data and WAL volumes are never snapshotted",
    "Verifying at the destination, not at the source",
    "Blueprint actions and artifacts",
    "What was validated",
])

d.hero(
    "THE OPERATOR ALREADY HAS", "A BACKUP ENGINE",
    body="Tanzu for Postgres ships pgBackRest, driven by four CRDs. This blueprint "
         "aims it at a MinIO\nDeployment inside the application namespace instead of a "
         "remote bucket.",
    pills=["pgBackRest", "Four CRDs", "Kept in the namespace"],
    icon='handshake_agreement')

d.cards(
    "What Kasten protects, and what it deliberately does not",
    "One PVC, and it is enough for a complete recovery including point-in-time",
    [
        ('close_circle', "NOT THE DATA OR WAL VOLUMES", [
            "The Postgres data and WAL volumes are never snapshotted.",
            "",
            "Snapshotting them alongside the repository would put a second, disagreeing "
            "copy of the database into the same restore point — with no rule saying "
            "which one wins.",
        ]),
        ('shield_check', "ONLY THE PGBACKREST REPOSITORY", [
            "The repository on MinIO is the single thing Kasten protects, and it is "
            "sufficient for a complete recovery — including point-in-time recovery.",
            "",
            "pgBackRest already solves consistency and PITR; the blueprint does not "
            "try to do it again.",
        ]),
        ('checkbox_multiple', "VALIDATED END TO END", [
            "Against a real operator: backup, restore, data recovery, and restore-point "
            "retirement.",
            "",
            "Retirement matters as much as the rest — a delete action that does not "
            "actually reclaim space turns a working backup into a storage leak.",
        ]),
    ])

d.flow(
    "Pattern 5 — vendor data mover into a local keeper",
    "The blueprint orchestrates the operator's CRDs; Kasten snapshots the keeper PVC",
    [
        ('database_storage', "TANZU POSTGRES, HA",
         "highAvailability enabled\nwith a read replica. Its data\nand WAL volumes are "
         "not in\nthe restore point"),
        ('upload_cloud', "backupPrehook: PostgresBackup CR",
         "Verifies the instance is\nRunning, creates the CR, and\nwaits for "
         "status.phase:\nSucceeded"),
        ('loop_left', "SWITCH THE WAL SEGMENT",
         "Then wait for it to actually\nreach the repository — not\nfor a source-side "
         "counter\nto move"),
        ('shield_check', "KASTEN SNAPSHOTS THE MINIO PVC",
         "One snapshot holding the\nwhole pgBackRest repository,\nafter a sync of the "
         "keeper\nfilesystem"),
    ],
    note=("Verify at the destination, not at the source.",
          "A source-side statistic saying \"sent\" is not evidence the bytes arrived. "
          "The blueprint asks pgBackRest what is actually in MinIO, because that answer "
          "is monotonic — and the obvious source-side alternative is not."))

d.cards(
    "Two traps in the WAL wait, both found by running it",
    "Either one makes the wait block for its whole timeout, or skip when it should not",
    [
        ('scan_target', "THE SWITCH CAN BE A NO-OP", [
            "If the segment name is unchanged after the switch, no new segment was "
            "created — so waiting for one to be archived can never succeed.",
            "",
            "The blueprint compares the segment name before and after the switch and "
            "skips the wait when they match.",
        ]),
        ('error_warning', "last_archived_wal IS NOT A HIGH-WATER MARK", [
            "With archive-async=y and process-max=2, pgBackRest pushes segments in "
            "parallel, so that column holds whichever archive_command returned last — "
            "not the highest segment. It can also hold a .backup label or a .history "
            "file.",
            "",
            "Comparing it with >= gives wrong answers. On one run the repository "
            "already held ...1B and ...1C while the column still read a .backup label "
            "sorting below ...1B, and the wait blocked for its whole timeout.",
        ]),
    ])

d.table_slide(
    "Versions",
    "The kasten-tools tag must equal the Kasten version on your cluster",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.34 (EKS)"),
        ("Kasten", "9.0.4"),
        ("Tanzu for Postgres operator", "4.5.0"),
        ("PostgreSQL", "18.4 (VMware Postgres 18.4.0)"),
        ("cert-manager", "v1.21.1"),
        ("MinIO", "RELEASE.2025-04-22T22-12-26Z"),
        ("kasten-tools image", "9.0.4"),
    ],
    col_widths=[3.0, 2.85],
    row_heights=[0.5] + [0.5] * 7,
    side=('file_list', "WHAT THE BLUEPRINT ASSUMES",
          "The operator and the instance are already installed. Installing them needs "
          "a Broadcom Support Portal account attached to a Tanzu Data Solutions "
          "contract, and there is no trial tier, so the install is out of scope.\n"
          "Developed against a 3-node EKS cluster with ebs-sc and ebs-snapshot-class; "
          "the operator in its own namespace, separate from the application, so Kasten "
          "never has to restore the operator itself.\n"
          "The instance is pg-ha with high availability enabled and one read replica. "
          "Images were relocated into a private registry, as in an air-gapped install.\n"
          "Kasten needs an S3 Location profile — Kanister phases require one, and an "
          "Infra profile will not do.\n"
          "The operator's expected image tag is not free-form: the chart builds it as "
          "v<db-version>-v<operator-version>."))

d.table_slide(
    "Blueprint actions and their artifacts",
    "Two actions — and the delete action is what keeps the repository from growing forever",
    ["Action", "Hook", "What it does"],
    [
        ("backupPrehook", "Before the PVC snapshot",
         "Verifies the instance is Running; creates a PostgresBackup CR; waits for "
         "status.phase: Succeeded; switches the WAL segment and waits for it to reach "
         "the repository; syncs the keeper filesystem; outputs backupName, namespace, "
         "instance, stanzaName and restoreLabel as restore-point artifacts."),
        ("delete", "When a restore point is retired",
         "Sets spec.expire: true on the PostgresBackup CR so the operator runs "
         "pgbackrest expire and removes the backup from the live MinIO repository, then "
         "deletes the CR. Does nothing if the namespace or the CR has already gone."),
    ],
    col_widths=[2.2, 2.8, 7.3],
    row_heights=[0.5, 1.6, 1.3],
    body_size=10.5,
    cards=[
        ('upload_cloud', "WHY THE ARTIFACTS ARE REQUIRED", [
            "backupName is how delete finds the PostgresBackup CR to expire. namespace "
            "is required because delete runs as a KubeTask in kasten-io and has no "
            "other way to know which namespace a retired restore point belonged to."]),
        ('search_eye', "READ pg_stat_archiver FOR FAILURES ONLY", [
            "It stays useful for spotting a persistent archiving failure — read "
            "last_failed_wal and last_failed_time. A .history failure right after a "
            "restore is benign: the timeline switch races the archiver, and it was "
            "observed on the validated run with archiving otherwise healthy."]),
    ])

d.ending("ASK THE DESTINATION, NOT THE SENDER",
         "pgBackRest owns consistency. Kasten owns the repository. The wait checks "
         "MinIO itself.\ngithub.com/kastenhq/blueprint-ai — tanzu-postgres-example")

d.save()
