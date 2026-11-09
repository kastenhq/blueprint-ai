#!/usr/bin/env python3
"""Concept deck for mysql-logical-late-capture-openshift-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py mysql-logical-late-capture-openshift-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('mysql-logical-late-capture-openshift', 'mysql-logical-late-capture-openshift-example')

d.title("LOGICAL MYSQL BACKUP\nWITH LATE CAPTURE — OPENSHIFT",
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
            "The blueprint is bound to the DeploymentConfig, which Kasten treats as a "
            "workload: BlueprintBinding works, and several workload types can share a "
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
    "Same mysqldump, same mysql client — the data path is what changes",
    ["", "Documentation example", "This blueprint"],
    [
        ("Dump goes to", "kando location push to object storage", "A staging PVC Kasten snapshots"),
        ("Data mover", "The blueprint", "Kasten"),
        ("Actions", "backup, restore, delete", "backupPrehook, backupPosthook, restorePosthook"),
        ("Deleting old backups", "A custom delete action", "Kasten, when the restore point expires"),
        ("Password", "Rendered into the pod command", "MYSQL_PWD from the Secret (podOverride)"),
        ("Tools image", "mysql:8 + kanister-tools", "The image of the DeploymentConfig itself"),
        ("Binding", "Annotation", "BlueprintBinding on the DeploymentConfig"),
    ],
    col_widths=[2.6, 4.6, 5.1],
    row_heights=[0.5] + [0.55] * 7)

d.flow(
    "Pattern 6 — logical dump on a staging PVC",
    "Kasten snapshots the dump — the ephemeral template has no data volume",
    [
        ('database', "backupPrehook",
         "KubeOps creates <dc>-dump.\nPrepareData mounts it, runs\nmysqldump | gzip, sync.\n"
         "Declares it: lateCapture"),
        ('shield_check', "KASTEN SNAPSHOTS IT",
         "Late capture adds the PVC\nto the snapshot set, after\nthe hook, before the\n"
         "snapshots"),
        ('delete', "backupPosthook",
         "Deletes the PVC.\nKasten checks it is gone\nand fails the backup if not"),
        ('loop_left', "restorePosthook",
         "Mounts the restored PVC,\nwaits for MySQL, replays\nthe dump, deletes the PVC"),
    ],
    note=("Why not a keeper PVC (pattern 4)?",
          "A mysqldump output is a full dump: it never needs the previous backup on the "
          "volume. So nothing has to persist between backups — no keeper Deployment to run, "
          "no PVC to keep and label. A tool that builds an incremental chain would need "
          "patterns 3 to 5, because the staging PVC is empty at every backup."))

d.table_slide(
    "Blueprint actions",
    "Every PrepareData phase sets failOnError: true — the default ignores the exit code",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Creates the PVC <dc>-dump. PrepareData (the server's own image) runs mysqldump "
         "--single-transaction --all-databases --routines --events | gzip, sync, "
         "checks it with gzip -t. Declares the PVC in the lateCapture artifact."),
        ("backupPosthook",
         "Deletes the staging PVC. Kasten then checks it is gone or terminating."),
        ("restorePosthook",
         "Mounts the restored PVC (name from the artifact), waits up to 5 minutes "
         "for MySQL to answer, replays the dump with the mysql client, deletes the PVC."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.05, 0.7, 1.0],
    cards=[
        ('error_warning', "A FAILED DUMP MUST FAIL THE BACKUP", [
            "With failOnError false, PrepareData returns success whatever mysqldump "
            "returned. pipefail matters too: without it mysqldump | gzip returns "
            "gzip's exit code."]),
        ('lock_security', "THE PASSWORD STAYS IN THE SECRET", [
            "podOverride injects it as MYSQL_PWD from the Secret, so it is never "
            "written into the pod's command or onto a command line."]),
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
    "The ephemeral template has no data PVC: the dump is the backup",
    [
        ('checkbox_multiple', "PROTECTED", [
            "Every database, user and grant, routines and events. --single-transaction gives "
            "one consistent view of InnoDB tables. Every Kubernetes object of the "
            "namespace, including the Secret.",
        ]),
        ('close_circle', "NOT PROTECTED", [
            "There is no data volume: the template is ephemeral (emptyDir). The dump is the "
            "only copy, and rebuilds the data after every restore. Non-InnoDB tables are "
            "not made consistent.",
        ]),
        ('time', "INCREMENTALITY", [
            "Each local snapshot is a full copy: the volume is new. The export deduplicates "
            "only grouped changes — measured on MongoDB: 1% added exported 12%, 1% updated "
            "at random 100%. Not measured on MySQL.",
        ]),
    ],
    body_size=12)

d.table_slide(
    "Versions",
    "Late capture needs Kasten 9.0.10 or later — and the feature flag",
    ["Component", "Version"],
    [
        ("OpenShift", "4.21.30 (bare metal, ODF)"),
        ("Kasten", "9.0.10 operator, lateCapture=true"),
        ("Template", "mysql-ephemeral, 8.0-el8"),
        ("Database", "MySQL 8.0.45 (RHEL 8)"),
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
          "Do not pin the dump pod to restricted-v2: Kasten runs Kanister pods as root, "
          "and the SCC would reject them."))

d.ending("A PVC FOR THE BACKUP, ONLY DURING THE BACKUP",
         "Late capture gives back what kando location push offered, with Kasten as the "
         "data mover.\ngithub.com/kastenhq/blueprint-ai — mysql-logical-late-capture-openshift-example")

d.save()
