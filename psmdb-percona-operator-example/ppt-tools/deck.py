#!/usr/bin/env python3
"""Concept deck for psmdb-percona-operator-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py psmdb-percona-operator-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('psmdb-percona-operator', 'psmdb-percona-operator-example')

d.title("PERCONA SERVER FOR MONGODB\nWITH KASTEN",
        "Quiesce a secondary — but only if your storage needs it")

d.agenda([
    "First: do you need this blueprint at all?",
    "When it is genuinely necessary",
    "The pattern: fence and quiesce a secondary",
    "Blueprint actions",
    "Restore, and why only one PVC is used",
])

d.hero(
    "IF YOUR STORAGE IS", "CRASH-CONSISTENT, STOP HERE",
    body="Do not deploy this blueprint. Let Kasten snapshot the three PVCs directly — "
         "WiredTiger\nreplays its journal on restart and recovers by itself. "
         "fsyncLock() adds nothing.",
    pills=["EBS, GCE PD, Azure Disk", "Ceph RBD", "Most enterprise SANs"],
    icon='question_help')

d.cards(
    "Why crash-consistent storage needs no help",
    "This is the standard, documented cloud backup method for MongoDB",
    [
        ('database_storage', "WIREDTIGER RECOVERS ITSELF", [
            "A modern CSI driver captures a point-in-time block image of the volume. "
            "WiredTiger keeps a write-ahead journal on that same volume.",
            "",
            "On restart it replays the journal and reaches a fully consistent state — "
            "exactly as if the server had lost power.",
        ]),
        ('checkbox_multiple', "JOURNALING IS ALWAYS ON", [
            "WiredTiger journaling has been enabled by default since MongoDB 4.0, and "
            "the operator does not turn it off.",
            "",
            "So the recovery mechanism the snapshot relies on is always present.",
        ]),
        ('node_tree', "ONE VOLUME, NOT TWO", [
            "The journal and the data files sit on the same volume: the PSMDB operator "
            "configures a single PVC per member.",
            "",
            "That matters — a journal on a separate volume would be snapshotted at a "
            "different instant, and the replay guarantee would not hold.",
        ]),
    ])

d.hero(
    "FUZZY SNAPSHOTS", "ARE WHY THIS EXISTS",
    body="Some older NFS-backed, CIFS and certain SAN or NAS implementations capture "
         "blocks from different\npoints in time within one volume. WiredTiger cannot "
         "reliably replay such a journal.",
    pills=["MongoDB may fail to start", "fsyncLock() removes the risk"],
    icon='error_warning', polarity='neg')

d.flow(
    "Pattern 1 — fence and quiesce a secondary",
    "Writes continue on the primary throughout: zero primary impact",
    [
        ('scan_target', "PICK A SECONDARY",
         "backupPrehook clears stale\nlabels and chooses a\nsecondary, highest ordinal\n"
         "first"),
        ('lock_security', "db.fsyncLock()",
         "Flushes all pending writes\nto disk and halts new I/O, so\nthe volume image "
         "needs no\njournal replay at all"),
        ('shield_check', "KASTEN SNAPSHOTS ALL THREE PVCs",
         "The quiesced secondary is\nthe clean anchor; the other\ntwo are captured "
         "alongside it"),
        ('lock_unlock', "db.fsyncUnlock()",
         "backupPosthook finds the\nlabelled pod, unlocks it and\nremoves the label"),
    ],
    note=("Why this is safe on any storage backend.",
          "fsyncLock() produces a volume image that is already consistent, rather than "
          "one that becomes consistent after replay. Because no replay is needed, it "
          "does not matter whether the snapshot's blocks all came from the same instant "
          "— which is precisely the guarantee a fuzzy snapshot cannot give you."))

d.table_slide(
    "Versions and blueprint actions",
    "The quiesced PVC name travels as a restore-point artifact, not as a label lookup",
    ["Action", "Function", "What it does"],
    [
        ("backupPrehook", "KubeTask",
         "Removes stale kasten.io/psmdb-quiesced labels, picks a secondary (highest "
         "ordinal first), calls db.fsyncLock(), labels the pod and the PVC, and emits "
         "the quiesced PVC name as a restore-point artifact."),
        ("backupPosthook", "KubeTask",
         "Finds the labelled pod, calls db.fsyncUnlock(), and removes the pod label."),
    ],
    col_widths=[2.4, 1.9, 8.0],
    row_heights=[0.5, 1.3, 0.75],
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.32.9 on EKS, Kasten 8.5.4, with the Percona Operator for "
            "MongoDB. The blueprint targets unsharded replica sets — for a sharded "
            "cluster see psmdb-percona-operator-pbm-example, which delegates "
            "cross-shard consistency to pbm."]),
        ('shield_check', "THREE SNAPSHOTS, ONE RESTORE SOURCE", [
            "Kasten snapshots every PVC it discovers, so all three are in the restore "
            "point. Only the quiesced secondary's PVC is authoritative: the "
            "RestoreAction excludes the other two by name, so fuzzy-snapshot data "
            "cannot contaminate the cluster. The operator then creates fresh empty PVCs "
            "for the excluded members and they resync."]),
    ])

d.split_dark(
    "Restore",
    "Clear the ground.\n", "Restore one volume.",
    [
        ("01", "DELETE THE CLUSTER CR AND ITS PVCs",
         "Deleting the PSMDB cluster CR stops all pods and stops the operator "
         "reconciling. Then delete every PVC for the cluster."),
        ("02", "RESTORE, EXCLUDING THE OTHER PVCs",
         "Trigger a RestoreAction that excludes the non-quiesced PVCs by name, so only "
         "the quiesced secondary's volume is restored."),
        ("03", "LET THE OPERATOR REBUILD THE REST",
         "The operator, restored alongside the CR, creates fresh empty PVCs for the "
         "excluded members. They resync from the restored member through normal replica "
         "set replication."),
    ],
    left_body="All three steps are manual and must happen before the RestoreAction. "
              "This is the usual shape for an operator-managed database: the operator "
              "and Kasten both believe they own the volumes, so the operator has to be "
              "stood down first.")

d.ending("CHECK YOUR STORAGE BEFORE YOU DEPLOY THIS",
         "On crash-consistent storage the right answer is no blueprint at all.\n"
         "github.com/kastenhq/blueprint-ai — psmdb-percona-operator-example")

d.save()
