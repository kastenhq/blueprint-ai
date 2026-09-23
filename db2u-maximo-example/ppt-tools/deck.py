#!/usr/bin/env python3
"""Concept deck for db2u-maximo-example. Content only — the slide machinery lives
in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py db2u-maximo-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('db2u-maximo', 'db2u-maximo-example')

d.title("DB2 UNDER THE DB2U OPERATOR\nWITH KASTEN",
        "An online DB2 backup onto the backup PVC — and the key that makes it readable")

d.agenda([
    "How a Db2uCluster is laid out",
    "The pattern: online backup to the permanent backup PVC",
    "The encryption keystore — the part that is easy to lose",
    "One-time prerequisite: archive logging",
    "Blueprint actions, and what stays manual",
])

d.hero(
    "A DB2 BACKUP IMAGE", "IS USELESS WITHOUT ITS KEY",
    body="DB2 encrypts the database and the backup image with a PKCS12 keystore. The "
         "image cannot be\nrestored without the label and encryption master key held "
         "inside it.",
    pills=["keystore.p12", "keystore.sth", "Copied beside the image"],
    icon='lock_password', polarity='neg')

d.table_slide(
    "The five PVCs of a Db2uCluster",
    "The operator creates all of them; Kasten snapshots all of them",
    ["PVC", "Mount", "Access", "Snapshotted"],
    [
        ("c-<name>-meta", "/mnt/blumeta0", "RWX", "Yes"),
        ("data-<name>-db2u-0", "/mnt/bludata0", "RWO", "Yes"),
        ("c-<name>-backup", "/mnt/backup", "RWX", "Yes"),
        ("activelogs-<name>-db2u-0", "/mnt/blulogs0", "RWO", "Yes"),
        ("tempts-<name>-db2u-0", "/mnt/blutempts0", "RWO", "Yes"),
    ],
    col_widths=[3.6, 2.5, 1.5, 1.8],
    row_heights=[0.5] + [0.55] * 5,
    side=('node_tree', "BOUND TO THE TOP OF THE CHAIN",
          "The DB2u ownership chain runs Db2uCluster, then Formation, then "
          "StatefulSet. The blueprint binds to the Db2uCluster CR — the top-level "
          "resource.\n"
          "A BlueprintBinding targeting db2uclusters is provided for fleet automation, "
          "because most installations run several instances on the same cluster.\n"
          "The storage classes in the README are from an OpenShift Data Foundation "
          "environment — CephFS for the RWX volumes, Ceph RBD for the RWO ones. Yours "
          "will differ; check them before deploying.\n"
          "The backup image is written to the backup PVC at /mnt/backup/backup/, which "
          "is permanent and therefore already present when Kasten runs PVC discovery."))

d.flow(
    "Pattern 3 — database snapshot on a permanent workload PVC",
    "The workload itself mounts the backup PVC, so no keeper deployment is needed",
    [
        ('database_storage', "backupPrehook: DB2 BACKUP",
         "Removes the previous image,\nthen runs db2 backup db BLUDB\nonline ... "
         "include logs inside\nthe DB2u engine pod"),
        ('lock_password', "COPY THE KEYSTORE",
         "keystore.p12 and\nkeystore.sth are copied\nalongside the backup image,\n"
         "before the snapshot"),
        ('loop_left', "SYNC",
         "The last thing the prehook\ndoes, so the written bytes\nare really on the "
         "device\nKasten is about to capture"),
        ('shield_check', "KASTEN SNAPSHOTS ALL PVCs",
         "Including the backup PVC —\nwhich now holds both the\nimage and the key "
         "needed to\nread it"),
    ],
    note=("Why the keystore copy is the whole point of the design.",
          "The keystore normally lives on the meta PVC. Copying it next to the backup "
          "image means a single restored backup PVC is self-sufficient: image plus key "
          "in one place. Restoring the image without the key produces a file that looks "
          "complete and cannot be read."))

d.cards(
    "Two deliberate scoping decisions",
    "Both follow from wanting a cross-instance restore to work",
    [
        ('lock_security', "THE SSL KEYSTORE IS NOT BACKED UP", [
            "The DB2 SSL keystore, bludb_ssl.kdb, lives on the meta PVC and is "
            "deliberately not copied to the backup PVC.",
            "",
            "For a cross-instance restore the client — in the Maximo manage namespace — "
            "must keep trusting the certificate the destination DB2 already presents. "
            "Only the data is restored, so the destination keeps its own identity.",
        ]),
        ('error_warning', "ARCHIVE LOGGING MUST BE ENABLED FIRST", [
            "DB2 defaults to circular logging, with LOGARCHMETH1 off, and circular "
            "logging blocks online backups outright.",
            "",
            "Enable archive logging through the Db2uCluster CR, not inside the pod — "
            "otherwise the operator reverts it on the next reconciliation and the next "
            "backup fails. This is a one-time prerequisite.",
        ]),
    ])

d.table_slide(
    "Versions and blueprint actions",
    "The blueprint does not implement restore — the procedure is documented and manual",
    ["Action", "Trigger", "What it does"],
    [
        ("backupPrehook", "Before the PVC snapshots",
         "Removes the previous backup image; runs an online db2 backup including logs "
         "to /mnt/backup/backup/; copies keystore.p12 and keystore.sth alongside the "
         "backup image; calls sync."),
        ("backupPosthook", "After the PVC snapshots",
         "Verifies the backup image and keystore.p12 are present on the backup PVC."),
    ],
    col_widths=[2.4, 2.7, 7.2],
    row_heights=[0.5, 1.3, 0.8],
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.31.10 on OpenShift with OpenShift Data Foundation, Kasten "
            "8.5.5, DB2u operator API db2u.databases.ibm.com/v1, DB2 11.5.9.0-cn6, IBM "
            "Maximo Application Suite 9.x."]),
        ('error_warning', "RESTORE IS MANUAL, ON PURPOSE", [
            "There is no restorePosthook yet. The restore procedure — reinstalling the "
            "label and encryption master key into the destination instance, then "
            "restoring the image — is written out step by step in the README and must "
            "be run by an operator."]),
    ])

d.ending("THE IMAGE AND ITS KEY TRAVEL TOGETHER",
         "One backup PVC holds both, so a restored volume is self-sufficient.\n"
         "github.com/kastenhq/blueprint-ai — db2u-maximo-example")

d.save()
