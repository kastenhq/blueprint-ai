#!/usr/bin/env python3
"""Concept deck for mongodb-logical-late-capture-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py mongodb-logical-late-capture-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('mongodb-logical-late-capture', 'mongodb-logical-late-capture-example')

d.title("LOGICAL MONGODB BACKUP\nWITH LATE CAPTURE",
        "A dump on a PVC that exists only during the backup — and Kasten stays the data mover")

d.agenda([
    "The problem late capture solves",
    "What changes from the documentation example",
    "The pattern and its architecture",
    "Blueprint actions",
    "The staging PVC must disappear — and why",
    "Restore, and what is not protected",
])

d.hero(
    "DISCOVERY RUNS", "BEFORE THE HOOK",
    body="Kasten lists the PVCs to snapshot before backupPrehook runs.\nA PVC the hook "
         "creates was never discovered, so it never reached the restore point.",
    pills=["Until Kasten 9.0.10", "Action hook was the only way", "One blueprint per namespace"],
    icon='search', polarity='neg')

d.cards(
    "Late capture: the hook declares what it created",
    "Kasten 9.0.10 or later, with features.lateCapture=true",
    [
        ('file_list', "A DECLARATION, NOT A SCAN", [
            "backupPrehook emits an output artifact whose name starts with lateCapture: "
            "apiVersion, group, resource, name, namespace. KubeOps returns exactly these "
            "five fields when it creates the PVC.",
        ]),
        ('shield_check', "ADDED BEFORE THE SNAPSHOTS", [
            "After the hook, Kasten reads the artifact and adds the PVC to the same "
            "snapshot set as the workload's PVCs. ConfigMaps and custom resources are "
            "captured as specs.",
        ]),
        ('checkbox_multiple', "RESOURCE-BOUND AGAIN", [
            "The blueprint is bound to the StatefulSet: BlueprintBinding works, each "
            "workload type keeps its own blueprint, and several types can share a "
            "namespace.",
        ]),
        ('error_warning', "FLAG OFF = NO DUMP", [
            "Without the flag Kasten only logs a warning and ignores the declaration. "
            "The PVC is created, never snapshotted, deleted — and the backup is green.",
        ]),
    ],
    body_size=12)

d.table_slide(
    "From the documentation example to late capture",
    "Same mongodump, same mongorestore — the data path is what changes",
    ["", "Documentation example", "This blueprint"],
    [
        ("Dump goes to", "kando location push to object storage", "A staging PVC Kasten snapshots"),
        ("Data mover", "The blueprint", "Kasten"),
        ("Actions", "backup, restore, delete", "backupPrehook, backupPosthook, restorePosthook"),
        ("Deleting old backups", "A custom delete action", "Kasten, when the restore point expires"),
        ("Password", "Rendered into the pod command", "Env from the Secret (podOverride)"),
        ("Binding", "Annotation", "BlueprintBinding"),
    ],
    col_widths=[2.6, 4.6, 5.1],
    row_heights=[0.5] + [0.6] * 6)

d.flow(
    "Pattern 6 — logical dump on a staging PVC",
    "Kasten snapshots the dump, not the MongoDB data volumes (the policy excludes them)",
    [
        ('database', "backupPrehook",
         "KubeOps creates <sts>-dump.\nPrepareData mounts it, runs\nmongodump --oplog, sync.\n"
         "Declares it: lateCapture"),
        ('shield_check', "KASTEN SNAPSHOTS IT",
         "Late capture adds the PVC\nto the snapshot set, after\nthe hook, before the\n"
         "snapshots"),
        ('delete', "backupPosthook",
         "Deletes the PVC.\nKasten checks it is gone\nand fails the backup if not"),
        ('loop_left', "restorePosthook",
         "Mounts the restored PVC,\nmongorestore --oplogReplay\n--drop, deletes the PVC"),
    ],
    note=("Why not a keeper PVC (pattern 4)?",
          "A mongodump archive is a full dump: it never needs the previous backup on the "
          "volume. So nothing has to persist between backups — no keeper Deployment to run, "
          "no PVC to keep and label. A tool that builds an incremental chain would need "
          "patterns 3 to 5, because the staging PVC is empty at every backup."))

d.table_slide(
    "Blueprint actions",
    "Every PrepareData phase sets failOnError: true — the default ignores the exit code",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Creates the PVC <sts>-dump. PrepareData (fsGroup 1001) runs mongodump against "
         "the replica set, sync, checks the archive is not empty. Declares the PVC in the "
         "lateCapture artifact."),
        ("backupPosthook",
         "Deletes the staging PVC. Kasten then checks it is gone or terminating."),
        ("restorePosthook",
         "Mounts the restored PVC (name from the artifact), runs mongorestore "
         "--oplogReplay --drop with retries while a primary is elected, deletes the PVC."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.05, 0.7, 1.0],
    cards=[
        ('error_warning', "A FAILED DUMP MUST FAIL THE BACKUP", [
            "With failOnError false, PrepareData returns success whatever mongodump "
            "returned. Kasten would snapshot an empty archive and mark the restore point "
            "green."]),
        ('lock_security', "THE PASSWORD STAYS IN THE SECRET", [
            "podOverride injects it as an environment variable from the Secret, so it is "
            "never written into the pod's command."]),
    ])

d.two_dark(
    "THE STAGING PVC", "MUST NOT SURVIVE A BACKUP",
    [
        ("1", "WHY KASTEN ENFORCES IT", "A stale dump would look valid",
         "A PVC still present at the next backup is discovered and snapshotted before the "
         "new dump is written. The restore point would hold the old dump. So Kasten fails "
         "the backup if the PVC exists after backupPosthook, and fails it again if "
         "discovery already saw the PVC."),
        ("2", "WHAT IT COSTS YOU", "One lost backup after a crash",
         "A crashed or cancelled backup skips backupPosthook and leaves the PVC behind. The "
         "next backup fails, its own backupPosthook deletes the PVC, and the one after "
         "succeeds. A deterministic name (<sts>-dump) is what lets the posthook find it."),
    ])

d.cards(
    "What the restore point holds, and what it does not",
    "The policy excludes the MongoDB data PVCs, like the documentation example did",
    [
        ('checkbox_multiple', "PROTECTED", [
            "Every database, as of the end of the dump (--oplog gives one point in time). "
            "Every Kubernetes object of the namespace, including the Secret with the root "
            "password.",
        ]),
        ('close_circle', "NOT PROTECTED", [
            "The MongoDB data volumes. Restored into an empty namespace, MongoDB starts on "
            "new volumes, initialises a new replica set, and the dump rebuilds the data.",
        ]),
        ('time', "INCREMENTALITY", [
            "Each local snapshot is a full copy: the volume is new. The export deduplicates "
            "only grouped changes: 1% of records added exported 12% of the dump, 1% updated "
            "at random exported 100%.",
        ]),
    ],
    body_size=12)

d.table_slide(
    "Versions",
    "Late capture needs Kasten 9.0.10 or later — and the feature flag",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.35.8 (EKS)"),
        ("Kasten", "9.0.10, lateCapture=true"),
        ("Helm chart", "bitnami/mongodb 20.0.17"),
        ("Database", "MongoDB 8.0.11, replica set rs0"),
    ],
    col_widths=[3.3, 2.55],
    row_heights=[0.55] * 5,
    side=('settings', "BEFORE YOU USE IT",
          "Check the flag: kubectl get cm k10-features -n kasten-io must show "
          "lateCapture: true.\n"
          "The staging PVC needs a CSI storage class that supports snapshots, in "
          "Filesystem mode.\n"
          "The policy needs a Location profile, and must export: local snapshots are "
          "deleted with the namespace.\n"
          "The policy filter must not match the staging PVC, or Kasten fails the backup."))

d.ending("A PVC FOR THE BACKUP, ONLY DURING THE BACKUP",
         "Late capture gives back what kando location push offered, with Kasten as the "
         "data mover.\ngithub.com/kastenhq/blueprint-ai — mongodb-logical-late-capture-example")

d.save()
