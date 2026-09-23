#!/usr/bin/env python3
"""Concept deck for cnpg-example. Content only — the slide machinery lives in the
shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py cnpg-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('cnpg', 'cnpg-example')

d.title("CLOUDNATIVEPG WITH KASTEN",
        "Fence and quiesce a replica — zero impact on the primary")

d.agenda([
    "The pattern: pause a replica, not the primary",
    "How the backup works",
    "The policy filter that makes it correct",
    "How the restore works, and why no initdb runs",
    "Blueprint actions",
])

d.hero(
    "PAUSE A REPLICA,", "NOT THE PRIMARY",
    body="WAL replay is paused on a standby immediately before Kasten snapshots the "
         "PVCs.\nThe result is a guaranteed-consistent checkpoint with zero impact on "
         "the primary.",
    pills=["Zero primary impact", "Incremental", "Operator-driven restore"],
    icon='loop_left')

d.cards(
    "Why pattern 1 is the first choice whenever a replica exists",
    "It is the highest-ranked pattern in the repository, and CNPG makes it easy",
    [
        ('checkbox_multiple', "THE PRIMARY NEVER PAUSES", [
            "pg_wal_replay_pause() stops replay on a standby only. The primary keeps "
            "accepting writes for the whole backup window.",
            "",
            "No lock, no checkpoint stall, no connection backlog on the instance that "
            "serves the application.",
        ]),
        ('speed_up', "IT STAYS INCREMENTAL", [
            "Kasten snapshots the replica PVC at block level, so backup time stays "
            "constant as the database grows.",
            "",
            "Nothing is dumped and nothing is copied by the blueprint.",
        ]),
        ('node_tree', "THE OPERATOR DOES THE RESTORE", [
            "CNPG owns the PVC lifecycle, and that turns out to be an advantage: given "
            "a replica PVC with data, it promotes it to primary by itself.",
            "",
            "The blueprint only has to wait for the cluster to report Ready.",
        ]),
    ])

d.flow(
    "Pattern 1 — fence and quiesce a replica",
    "Three steps, and only the middle one moves any data",
    [
        ('lock_security', "backupPrehook",
         "Finds a replica pod and calls\npg_wal_replay_pause() — the\nreplica stops "
         "applying WAL\nand holds a fixed state"),
        ('shield_check', "KASTEN SNAPSHOTS THE REPLICA PVC",
         "Only the replica PVC. The\nprimary PVC is excluded by a\npolicy resource "
         "filter"),
        ('lock_unlock', "backupPosthook",
         "pg_wal_replay_resume() on\nthe same replica — it catches\nup with the primary "
         "from the\nWAL stream"),
        ('checkbox_multiple', "restorePosthook",
         "Waits for the cluster\ncondition Ready. Primary\npromotion is handled by the\n"
         "operator"),
    ],
    note=("The quiesced replica snapshot is the sole source of truth for restore.",
          "That is why the policy filter is not optional: if the primary PVC is also "
          "snapshotted, the restore point contains two disagreeing copies of the "
          "database and no rule says which one CNPG will promote."))

d.hero(
    "THE POLICY MUST EXCLUDE", "THE PRIMARY PVC",
    body="Set excludeResources under spec.actions[].backupParameters.filters so only "
         "the quiesced\nreplica PVC is captured. Without it the backup is not what the "
         "blueprint intends.",
    pills=["excludeResources is required"],
    icon='error_warning', polarity='neg')

d.split_dark(
    "Restore",
    "Delete the CR.\n", "The operator rebuilds.",
    [
        ("01", "DELETE THE CLUSTER CR BY HAND",
         "CNPG owns the PVC lifecycle, so deleting the Cluster CR makes the operator "
         "stop all pods and garbage-collect the PVCs. Confirm the pods and PVCs are "
         "gone before triggering the restore."),
        ("02", "KASTEN RESTORES, CNPG PROMOTES",
         "Kasten restores the replica PVC with its original CNPG labels and recreates "
         "the Cluster CR. The operator sees an existing replica PVC holding data — "
         "latestGeneratedNode greater than zero — and promotes it to primary."),
        ("03", "A FRESH REPLICA FOLLOWS",
         "CNPG creates a new replica by streaming replication from the promoted "
         "primary, and restorePosthook waits for the cluster condition Ready."),
    ],
    left_body="No initdb is run at any point. That is the detail that makes this "
              "restore trustworthy: the operator recognises real data on the volume "
              "and adopts it, rather than initialising an empty cluster over the top "
              "of it.")

d.table_slide(
    "Versions and blueprint actions",
    "Nothing in blueprint.yaml is platform-specific — only the operator install needs an OpenShift adjustment",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Finds a replica pod and pauses WAL replay with pg_wal_replay_pause()."),
        ("backupPosthook",
         "Resumes WAL replay on the same replica with pg_wal_replay_resume()."),
        ("restorePosthook",
         "Waits for the cluster condition Ready — the operator handles primary "
         "promotion by itself."),
    ],
    col_widths=[2.9, 9.4],
    row_heights=[0.5, 0.7, 0.7, 0.8],
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.31, OpenShift 4.18.6, Kasten 9.0.5, CNPG Helm chart "
            "cloudnative-pg 0.28.3, CNPG operator 1.29.1, PostgreSQL 18.3, Azure Disk "
            "CSI storage class managed-csi, tool image kasten-tools:9.0.5."]),
        ('history_clock', "DEVELOPED TWICE OVER", [
            "First developed on EKS 1.32 with Kasten 8.5.4 and CNPG 1.29.0, then "
            "re-validated end to end on the OpenShift cluster above. Re-validating on "
            "a second platform is what confirmed the blueprint itself carries no "
            "platform assumptions."]),
    ])

d.ending("THE REPLICA HOLDS STILL SO THE PRIMARY NEED NOT",
         "One pause, one snapshot, one resume. CNPG rebuilds the cluster around the "
         "restored volume.\ngithub.com/kastenhq/blueprint-ai — cnpg-example")

d.save()
