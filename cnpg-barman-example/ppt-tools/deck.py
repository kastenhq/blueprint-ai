#!/usr/bin/env python3
"""Concept deck for cnpg-barman-example. Content only — the slide machinery lives
in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py cnpg-barman-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('cnpg-barman', 'cnpg-barman-example')

d.title("CNPG BARMAN WITH A\nLOCAL MINIO KEEPER",
        "Point-in-time recovery from a Kasten restore point — in-tree barman-cloud")

d.agenda([
    "Deprecation: read this before you adopt it",
    "The pattern, and why barman stays inside the namespace",
    "The WAL switch: the detail that makes it restorable",
    "Blueprint actions and PITR range",
    "The recovery procedure",
])

d.hero(
    "THIS USES THE IN-TREE", "BARMAN INTEGRATION",
    body="spec.backup.barmanObjectStore and a Backup with method: barmanObjectStore "
         "are deprecated,\nand removed entirely in CNPG 1.30.0. This blueprint targets "
         "CNPG 1.29.x and earlier.",
    pills=["Deprecated", "Removed in CNPG 1.30.0", "Two successors exist"],
    icon='error_warning', polarity='neg')

d.cards(
    "Where to go instead",
    "Both successors are in this repository — the choice depends on whether you want barman at all",
    [
        ('loop_left', "KEEP BARMAN: THE PLUGIN", [
            "cnpg-barman-cloud-example uses the same pattern 5 MinIO-keeper design, "
            "upgraded to the Barman Cloud Plugin: an ObjectStore CR, spec.plugins, and "
            "a Backup with method: plugin.",
            "",
            "The recommended migration target if you want to keep barman-based "
            "point-in-time recovery. Requires CNPG 1.26 or later, and cert-manager.",
        ]),
        ('shield_check', "DROP BARMAN: QUIESCE A REPLICA", [
            "cnpg-example uses pattern 1 — fence and quiesce a replica — with no barman "
            "anywhere.",
            "",
            "Choose it when crash-consistent PVC snapshots are enough and you would "
            "rather not run an archive at all. Simpler to operate, but no "
            "point-in-time recovery between backups.",
        ]),
    ])

d.flow(
    "Pattern 5 — a local MinIO keeper",
    "Barman writes to MinIO inside the namespace; Kasten snapshots only the MinIO PVC",
    [
        ('database_storage', "CNPG CLUSTER",
         "WAL stream and barman base\nbackups. Its own data PVCs\nare excluded from the\n"
         "snapshot by a policy filter"),
        ('upload_cloud', "backupPrehook: Backup CR",
         "Creates a CNPG Backup CR to\ntrigger a base backup, then\npolls until it "
         "completes"),
        ('loop_left', "FORCE A WAL SWITCH",
         "pg_switch_wal() hands the\ncurrent segment to the\narchiver, then waits 15s\n"
         "for the upload"),
        ('shield_check', "KASTEN SNAPSHOTS THE MINIO PVC",
         "A self-consistent barman\narchive: one base backup plus\nevery WAL up to the "
         "switch"),
    ],
    note=("Why the WAL switch is needed here but not in a normal barman setup.",
          "The Backup CR reports completed once the base backup files are uploaded. But "
          "pg_backup_stop() also writes a WAL record at the stop LSN — the exact "
          "position PostgreSQL must replay to reach consistency. That record lands in "
          "the currently-active, partly-filled segment, and the archiver only ships "
          "complete 16 MB segments, so on an idle cluster it may never leave the pod."))

d.hero(
    "THE SNAPSHOT FREEZES", "AT THAT INSTANT",
    body="Kasten snapshots the MinIO PVC as soon as the phase returns — no further WAL "
         "will ever join it.\nWithout the switch, the archive is missing the one WAL "
         "piece that makes the base backup usable.",
    pills=["pg_switch_wal()", "then a 15 second wait"],
    icon='time_clock', polarity='neg')

d.cards(
    "Why put barman behind Kasten at all",
    "Barman stays strictly local to the namespace; Kasten takes over everything past that boundary",
    [
        ('lock_security', "SECURITY AND IMMUTABILITY", [
            "Kasten encrypts snapshot data at the storage layer — barman has no "
            "equivalent.",
            "Kasten enforces immutability at the backup target; barman retention "
            "policies alone cannot guarantee it.",
            "Kasten handles authenticated egress, so the application team never holds "
            "credentials for the external location profile.",
        ]),
        ('global_world', "REACH AND EFFICIENCY", [
            "Kasten can protect to NFS, Veeam Vault, VBR and other targets "
            "barman-cloud does not support.",
            "Kasten's PVC snapshots are incremental; a plain barman export to an "
            "external store is a full transfer every time.",
            "Because MinIO is in the namespace, a restore from the newest restore point "
            "reads the local PVC snapshot instead of fetching from the remote target.",
        ]),
        ('rocket_launch', "SELF-CONTAINED AND REPEATABLE", [
            "Every component — MinIO Deployment, PVC, CNPG Cluster, Secrets, blueprint "
            "— lives entirely inside the application namespace.",
            "The same manifests deploy verbatim in a new namespace by changing only the "
            "namespace name, which makes the pattern a good candidate for packaging as "
            "a Helm chart parameterised on little more than that.",
        ]),
    ],
    body_size=11)

d.table_slide(
    "Blueprint actions and PITR range",
    "Two actions. How far back you can recover depends on retentionPolicy and restore-point age.",
    ["Kasten backup frequency", "Minimum recommended retentionPolicy"],
    [
        ("Daily", "3d — the blueprint's default of 7d gives comfortable headroom"),
        ("Every 12 hours", "2d"),
        ("Weekly", "14d"),
    ],
    col_widths=[4.0, 8.3],
    row_heights=[0.5, 0.62, 0.62, 0.62],
    cards=[
        ('upload_cloud', "backupPrehook", [
            "Creates the CNPG Backup CR, waits for completion, and forces the WAL "
            "switch. Outputs backupName and namespace as restore-point artifacts, so "
            "the restore side knows which base backup it is dealing with."]),
        ('close_circle', "delete", [
            "On restore-point deletion, removes the CNPG Backup CR from the application "
            "namespace so the operator clears the matching barman data out of MinIO. "
            "Silently does nothing if the namespace or the Backup CR has already gone."]),
    ])

d.split_dark(
    "Recovery",
    "Restore the archive.\n", "Then recover into it.",
    [
        ("01", "DELETE THE CNPG CLUSTER",
         "Optionally record your target time first, and list the available base backups "
         "to see the archive range. Then delete the cluster and wait for every pod to "
         "disappear."),
        ("02", "TRIGGER THE KASTEN RESTORE",
         "Kasten restores the MinIO PVC, which brings back the whole barman archive — "
         "base backups and WALs — as it stood when the snapshot was taken."),
        ("03", "CREATE A CNPG RECOVERY CLUSTER",
         "Point a recovery cluster at the restored archive through externalClusters to "
         "replay to your chosen point in time. Recovery needs a separate bucket with "
         "the in-tree integration."),
    ],
    left_body="You can recover to any moment from the start of the oldest base backup "
              "in the restored MinIO snapshot onwards. Two things bound that window: "
              "the barman retentionPolicy, which decides how much archive exists, and "
              "the age of the Kasten restore point you restore from.")

d.ending("KASTEN OWNS THE PERIMETER, BARMAN OWNS THE ARCHIVE",
         "Barman never leaves the namespace. Kasten handles everything beyond it.\n"
         "github.com/kastenhq/blueprint-ai — cnpg-barman-example")

d.save()
