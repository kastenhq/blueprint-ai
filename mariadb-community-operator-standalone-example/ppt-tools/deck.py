#!/usr/bin/env python3
"""Concept deck for mariadb-community-operator-standalone-example. Content only —
the slide machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py mariadb-community-operator-standalone-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('mariadb-standalone', 'mariadb-community-operator-standalone-example')

d.title("STANDALONE MARIADB\nWITH KASTEN",
        "Quiesce and unquiesce around a CSI snapshot — nothing is copied")

d.agenda([
    "Scope: standalone instances only",
    "Why quiesce, and what it rules out",
    "The operator ownership chain",
    "Blueprint actions",
    "The manual step before every restore",
])

d.hero(
    "MARIADB ALREADY HAS", "A QUIESCE PRIMITIVE",
    body="FLUSH TABLES WITH READ LOCK, then FLUSH LOGS. Writes stop, dirty pages and "
         "binary logs\nreach the disk, and InnoDB recovers the result cleanly on "
         "startup.",
    pills=["Incremental", "No extra PVC", "Simple restore"],
    icon='lock_security')

d.cards(
    "Scope, and the patterns that were ruled out",
    "Standalone means replicas: 1 — Galera and replicated topologies are out of scope",
    [
        ('close_circle', "FENCE A REPLICA — NOT POSSIBLE", [
            "A standalone instance has no replica to fence, so the highest-ranked "
            "pattern cannot apply.",
            "",
            "For a Galera cluster the analysis would be different — and that is a "
            "different blueprint.",
        ]),
        ('checkbox_multiple', "QUIESCE — CHOSEN", [
            "Backup time stays constant as the database grows, because Kasten "
            "snapshots only the PVC delta between runs.",
            "",
            "No dump pod, no temporary storage, and the restore is a plain PVC "
            "replacement.",
        ]),
        ('close_circle', "DUMP TO A PVC — RULED OUT", [
            "Feasible, but it buys nothing here: a fresh logical dump every run is not "
            "incremental, and it needs storage the quiesce pattern does not.",
            "",
            "Choose it only if you need granular restore — see the limitation opposite.",
        ]),
    ],
    note=("The limitation you are accepting.",
          "Granular restore is not possible: the whole PVC is restored, so you cannot "
          "recover a single table or database. If that matters more than incrementality, "
          "switch to pattern 9, a logical dump on a temporary PVC."))

d.flow(
    "Pattern 2 — quiesce and unquiesce",
    "The blueprint holds a lock; Kasten does the work in the middle",
    [
        ('lock_security', "backupPrehook",
         "FLUSH TABLES WITH READ LOCK\nFLUSH LOGS\n\nNew writes block; dirty\npages "
         "and binlogs flush"),
        ('shield_check', "KASTEN SNAPSHOTS THE PVC",
         "A CSI snapshot of\nstorage-<name>-0 while the\nlock is held — so the disk\nis "
         "crash-consistent"),
        ('lock_unlock', "backupPosthook",
         "UNLOCK TABLES\n\nNormal operation resumes as\nsoon as the snapshot is\nready"),
        ('checkbox_multiple', "restorePosthook",
         "Wait for the MariaDB CR to\nreach Ready, then SELECT 1\nto confirm the "
         "database\naccepts connections"),
    ],
    note=("Why no unlock is needed on the restore side.",
          "FLUSH TABLES WITH READ LOCK is session-level, in-memory state — it never "
          "reaches the disk. The restored volume therefore shows InnoDB a clean "
          "checkpoint, and MariaDB starts normally with no unlock step. That is what "
          "makes this pattern's restore path so short."))

d.table_slide(
    "Versions and the ownership chain",
    "Kasten discovers the PVC by following ownership down from the MariaDB custom resource",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.2"),
        ("mariadb-operator (Helm)", "25.10.4"),
        ("MariaDB", "11.8.5"),
        ("Tools image", "kasten-tools:8.5.2"),
    ],
    col_widths=[3.4, 2.45],
    row_heights=[0.55] * 6,
    side=('node_tree', "FROM CR TO PVC",
          "MariaDB CR — k8s.mariadb.com/v1alpha1\n"
          "    StatefulSet <name>\n"
          "        Pod <name>-0\n"
          "        PVC storage-<name>-0\n"
          "Kasten walks this chain from the CR, so the data PVC is discovered without "
          "being named anywhere.\n"
          "The blueprint is bound to the MariaDB custom resource through a "
          "BlueprintBinding — not to the StatefulSet — so adding another database type "
          "never means editing this blueprint.\n"
          "The operator creates the root password Secret by default; the blueprint "
          "reads it from there."))

d.table_slide(
    "Blueprint actions",
    "Three actions. Deploy the Blueprint and the BlueprintBinding in kasten-io.",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "FLUSH TABLES WITH READ LOCK; FLUSH LOGS; — quiesces MariaDB so the disk "
         "Kasten captures is crash-consistent."),
        ("backupPosthook",
         "UNLOCK TABLES; — unquiesces once the Kasten PVC snapshot is ready."),
        ("restorePosthook",
         "WaitV2 until the MariaDB CR reports Ready=True, then runs SELECT 1 inside "
         "the pod to confirm the database accepts connections."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 0.95, 0.75, 0.95],
    cards=[
        ('instance_hexagon', "WHY A CUSTOM TOOLS IMAGE", [
            "The KubeTask phases need kubectl, which the stock "
            "gcr.io/kasten-images/kanister-tools image does not include. The blueprint "
            "uses the shared kasten-tools image, built from "
            "images/kasten-tools/Dockerfile at the repository root. Match its tag to "
            "the Kasten version on your cluster."]),
        ('checkbox_multiple', "WHAT YOU NEED IN PLACE", [
            "The mariadb-operator Helm chart installed in the cluster, a Secret holding "
            "the MariaDB root password, and a storage class backed by a CSI driver that "
            "supports snapshots with a matching VolumeSnapshotClass registered with "
            "Kasten."]),
    ])

d.hero(
    "BEFORE EVERY RESTORE", "DELETE THE CR FIRST",
    body="Leave the operator reconciling and it recreates the PVC the moment Kasten "
         "deletes it.\nThe restore then times out — and the cause is not obvious from "
         "the failure.",
    pills=["Delete the MariaDB CR", "Wait for the pod to go", "Then restore"],
    icon='error_warning', polarity='neg')

d.split_dark(
    "The manual pre-restore steps",
    "Stop the operator.\n", "Then let it rebuild.",
    [
        ("01", "DELETE THE MARIADB CR",
         "Deleting the CR stops the operator reconciling the StatefulSet. Wait for the "
         "StatefulSet and the pod to be fully deleted, and allow a short grace period "
         "for the PVC finalizer to release."),
        ("02", "TRIGGER THE KASTEN RESTORE",
         "Kasten restores the PVC and the MariaDB CR manifest. The operator sees the CR "
         "again and recreates the StatefulSet against the restored PVC by itself — "
         "nothing further to do."),
    ],
    left_body="This is the ordinary shape of restoring an operator-managed database: "
              "the operator and Kasten both believe they own the PVC, so one of them "
              "has to be stood down first. Run these steps by hand before triggering "
              "the restore; they are not automated from a hook.")

d.ending("QUIESCE, SNAPSHOT, UNLOCK",
         "The blueprint holds a lock for a moment. Kasten does everything else.\n"
         "github.com/kastenhq/blueprint-ai — mariadb-community-operator-standalone-example")

d.save()
