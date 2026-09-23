#!/usr/bin/env python3
"""Concept deck for cnpg-barman-cloud-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py cnpg-barman-cloud-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('cnpg-barman-cloud', 'cnpg-barman-cloud-example')

d.title("CNPG BARMAN CLOUD PLUGIN\nWITH A MINIO KEEPER",
        "The plugin-based successor — same design, new CNPG syntax")

d.agenda([
    "What changed from the in-tree integration",
    "The pattern, and why barman stays in the namespace",
    "The WAL switch: the detail that makes it restorable",
    "Blueprint actions and PITR range",
    "The recovery procedure",
])

d.hero(
    "SAME DESIGN,", "NEW CNPG SYNTAX",
    body="The pattern and the Kasten integration are identical to the in-tree "
         "blueprint.\nOnly the CloudNativePG side changed — and it now has "
         "prerequisites.",
    pills=["CNPG 1.26 or later", "cert-manager", "Plugin install"],
    icon='loop_left')

d.table_slide(
    "What changed, concern by concern",
    "The in-tree integration is removed in CNPG 1.30.0 — this is the migration target",
    ["Concern", "In-tree (cnpg-barman-example)", "Plugin (this blueprint)"],
    [
        ("Object-store config", "spec.backup.barmanObjectStore on the Cluster",
         "A dedicated ObjectStore CR, barmancloud.cnpg.io/v1"),
        ("WAL archiving", "Built into the operator",
         "spec.plugins barman-cloud.cloudnative-pg.io, plus an injected sidecar"),
        ("Backup CR", "method: barmanObjectStore",
         "method: plugin, plus pluginConfiguration.name"),
        ("Recovery source", "externalClusters[].barmanObjectStore",
         "externalClusters[].plugin"),
        ("Recovery write target", "A separate bucket is required",
         "The same bucket, under a distinct serverName prefix"),
        ("Prerequisites", "None beyond the operator",
         "CNPG 1.26 or later, cert-manager, and the plugin installed"),
    ],
    col_widths=[2.6, 4.6, 5.1],
    row_heights=[0.5, 0.72, 0.85, 0.72, 0.6, 0.72, 0.72],
    body_size=10.5,
    first_col_accent=True)

d.flow(
    "Pattern 5 — a local MinIO keeper",
    "The plugin archives to MinIO inside the namespace; Kasten snapshots only that PVC",
    [
        ('database_storage', "CNPG CLUSTER + PLUGIN",
         "An ObjectStore CR configures\nthe plugin; an injected\nsidecar archives WAL "
         "and\nbase backups to MinIO"),
        ('upload_cloud', "backupPrehook: Backup CR",
         "Creates a Backup CR with\nmethod: plugin, then polls\nuntil it reaches "
         "completed"),
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
          "position PostgreSQL must replay to reach consistency — and it lands in the "
          "currently-active, partly-filled segment. The archiver only ships complete "
          "16 MB segments, so on an idle cluster that record may never leave the pod."))

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
            "Every component lives entirely inside the application namespace.",
            "The same manifests deploy verbatim in a new namespace by changing only the "
            "namespace name, which makes the pattern a good candidate for packaging as "
            "a Helm chart parameterised on little more than that.",
            "The application team needs only MinIO credentials in their own namespace.",
        ]),
    ],
    body_size=11)

d.table_slide(
    "Versions, actions and PITR range",
    "How far back you can recover depends on retentionPolicy and the restore point's age",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.9"),
        ("CNPG operator", "cloudnative-pg 1.29.0"),
        ("Barman Cloud Plugin", "v0.13.0"),
        ("cert-manager", "v1.19.2"),
        ("PostgreSQL", "18.3"),
        ("MinIO", "RELEASE.2025-04-22T22-12-26Z"),
    ],
    col_widths=[2.6, 3.25],
    row_heights=[0.5] + [0.5] * 7,
    side=('upload_cloud', "TWO ACTIONS, AND HOW LONG TO KEEP WAL",
          "backupPrehook — creates the Backup CR with method: plugin, waits for "
          "completion, forces the WAL switch, and outputs backupName and namespace as "
          "restore-point artifacts.\n"
          "delete — on restore-point deletion, removes the CNPG Backup CR from the "
          "application namespace; silently does nothing if it has already gone.\n"
          "Sizing retentionPolicy against the Kasten backup interval: daily backups "
          "need at least 3d, and the default 7d here gives comfortable headroom; every "
          "12 hours needs at least 2d; weekly needs at least 14d.\n"
          "Detect your own Kasten version with helm ls -n kasten-io before matching "
          "image tags."))

d.split_dark(
    "Recovery",
    "Restore the archive.\n", "Then recover into it.",
    [
        ("01", "DELETE THE CNPG CLUSTER",
         "Optionally record your point-in-time target first. Then delete the cluster "
         "and wait for every pod to disappear."),
        ("02", "TRIGGER THE KASTEN RESTORE",
         "Kasten restores the MinIO PVC, bringing back the whole barman archive — base "
         "backups and WALs — as it stood when the snapshot was taken."),
        ("03", "CREATE A CNPG RECOVERY CLUSTER",
         "Point a recovery cluster at the restored archive through "
         "externalClusters[].plugin. With the plugin, recovery writes into the same "
         "bucket under a distinct serverName prefix — no second bucket needed."),
    ],
    left_body="That last difference is the practical win over the in-tree integration: "
              "the in-tree recovery path required a separate bucket, so a recovery "
              "meant provisioning storage before you could start. Here the same MinIO "
              "instance serves both sides.")

d.ending("KASTEN OWNS THE PERIMETER, BARMAN OWNS THE ARCHIVE",
         "The plugin changed the syntax, not the design. Barman never leaves the "
         "namespace.\ngithub.com/kastenhq/blueprint-ai — cnpg-barman-cloud-example")

d.save()
