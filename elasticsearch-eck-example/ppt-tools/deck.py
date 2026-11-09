#!/usr/bin/env python3
"""Concept deck for elasticsearch-eck-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py elasticsearch-eck-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('elasticsearch-eck', 'elasticsearch-eck-example')

d.title("ELASTICSEARCH ON ECK\nWITH KASTEN",
        "A permanent snapshot-repository PVC, snapshotted alongside the data")

d.agenda([
    "Why not a filesystem snapshot of the data volumes",
    "Why a permanent repository PVC, not a temporary one",
    "The RWX storage requirement",
    "Backup and restore flow",
    "Blueprint actions",
])

d.hero(
    "ELASTIC DISCOURAGES", "SNAPSHOTTING THE DATA VOLUMES",
    body="So the restore artefact is an Elasticsearch native snapshot, written to a "
         "repository PVC.\nKasten then snapshots that PVC — and remains the data mover.",
    pills=["Native ES snapshot", "Consistent", "Append-only"],
    icon='error_warning', polarity='neg')

d.cards(
    "Pattern 3, not pattern 8 — and the reason is operational",
    "A temporary repository PVC would be preferable on paper, but not in practice here",
    [
        ('close_circle', "WHAT PATTERN 8 WOULD COST", [
            "The Elasticsearch filesystem repository needs path.repo set in "
            "elasticsearch.yml at pod startup.",
            "",
            "ECK configures that through the config section of the Elasticsearch CR, "
            "and changing it triggers a rolling restart. A per-backup temporary PVC "
            "would therefore mean two rolling restarts every backup cycle — add the "
            "PVC, snapshot, remove the PVC.",
        ]),
        ('checkbox_multiple', "WHAT PATTERN 3 COSTS INSTEAD", [
            "One permanent snapshot-repository PVC, declared once in the Elasticsearch "
            "pod template as a single workload configuration change.",
            "",
            "After that, backups need no restarts at all. Kasten CSI-snapshots the "
            "repository PVC alongside the data PVCs, and the native snapshot inside "
            "the repository is the primary restore artefact.",
        ]),
    ])

d.hero(
    "THE REPOSITORY PVC", "MUST BE READWRITEMANY",
    body="The Elasticsearch filesystem repository requires every master and data node "
         "to reach the same\nshared filesystem path at the same time. For a multi-node "
         "cluster that means RWX.",
    pills=["RWX CSI provisioner", "CSI snapshots supported", "Retain-policy clone class"],
    icon='folder_directory', polarity='neg')

d.cards(
    "What your cluster needs before this works",
    "All three must be true — an RWX class that cannot snapshot is not enough",
    [
        ('global_world', "AN RWX CSI PROVISIONER", [
            "NFS CSI, CephFS, or any other provisioner that can serve ReadWriteMany.",
            "",
            "The manifests in operator/ use nfs-csi-1 as an example — replace it with "
            "the right class for your environment.",
        ]),
        ('scan_target', "A VOLUMESNAPSHOTCLASS FOR IT", [
            "The RWX class must also support CSI snapshots, so Kasten can capture the "
            "repository PVC as part of the restore point.",
            "",
            "An RWX class with no VolumeSnapshotClass leaves the repository out of the "
            "backup entirely.",
        ]),
        ('shield_check', "A RETAIN-POLICY CLONE CLASS", [
            "Kasten needs a Retain-policy clone VolumeSnapshotClass registered for "
            "that provisioner.",
            "",
            "Verify all three before deploying — this is the part of the setup most "
            "likely to be missing on a cluster that has only ever used block storage.",
        ]),
    ])

d.flow(
    "Backup flow",
    "Kasten snapshots every PVC in the namespace — the data volumes and the repository",
    [
        ('settings_gear', "backupPrehook",
         "Registers the filesystem\nrepository at\n/mnt/snapshot-repo, then\ntakes a "
         "native ES snapshot"),
        ('shield_check', "KASTEN SNAPSHOTS ALL PVCs",
         "Both the Elasticsearch data\nPVCs and the snapshot\nrepository PVC"),
        ('file_list', "backupPosthook",
         "Logs the final snapshot\nstatus, once the CSI\nsnapshots have been taken"),
        ('loop_left', "restorePosthook",
         "Resumes ECK, waits for\nhealth, then runs the native\nES restore from "
         "kasten-snapshot"),
    ],
    note=("The prehook deletes the previous snapshot first.",
          "It removes the earlier kasten-snapshot before taking a new one, which makes "
          "the phase idempotent — a retried backup does not accumulate snapshots or "
          "fail on a name collision. The consequence is that the repository holds one "
          "snapshot at a time; see elasticsearch-eck-minio-example for the variant that "
          "keeps one snapshot per restore point."))

d.table_slide(
    "Versions and blueprint actions",
    "Restore needs manual preparation: pause ECK reconciliation and scale the cluster down",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Registers the filesystem snapshot repository at /mnt/snapshot-repo; deletes "
         "the previous kasten-snapshot for idempotency; takes a new Elasticsearch "
         "native snapshot and waits for it to complete."),
        ("backupPosthook",
         "Logs the final snapshot status, after Kasten has taken CSI snapshots of all "
         "PVCs."),
        ("restorePosthook",
         "Removes the pause annotation to resume ECK reconciliation; waits for the "
         "cluster to reach green or yellow health; registers the repository on the "
         "restored repo PVC; closes existing indices; runs the native ES restore from "
         "kasten-snapshot and waits for completion."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.2, 0.65, 1.55],
    body_size=10.5,
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.32 on EKS, Kasten 8.5.2, ECK operator (eck-operator) 2.16.1, "
            "Elasticsearch 8.17.0."]),
        ('error_warning', "MANUAL PRE-RESTORE STEPS", [
            "Pause ECK reconciliation and scale all Elasticsearch nodes down before "
            "triggering the restore, otherwise the operator races Kasten for the PVCs. "
            "The restorePosthook removes the pause annotation again once the volumes "
            "are back."]),
    ])

d.ending("THE NATIVE SNAPSHOT IS THE RESTORE ARTEFACT",
         "Kasten moves the repository PVC. Elasticsearch decides what is inside it.\n"
         "github.com/kastenhq/blueprint-ai — elasticsearch-eck-example")

d.save()
