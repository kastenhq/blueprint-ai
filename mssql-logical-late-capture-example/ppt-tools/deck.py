#!/usr/bin/env python3
"""Concept deck for mssql-logical-late-capture-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py mssql-logical-late-capture-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('mssql-logical-late-capture', 'mssql-logical-late-capture-example')

d.title("LOGICAL SQL SERVER BACKUP\nWITH LATE CAPTURE",
        "Full .bak files on a PVC that exists only during the backup — and Kasten stays the data mover")

d.agenda([
    "The problem late capture solves",
    "What changes from the documentation example",
    "The pattern and its architecture",
    "The backup passes through the node's disk",
    "Blueprint actions",
    "Restore, and what is not protected",
])

d.hero(
    "DISCOVERY RUNS", "BEFORE THE HOOK",
    body="Kasten lists the PVCs to snapshot before backupPrehook runs.\nA PVC the hook "
         "creates was never discovered, so it never reached the restore point.",
    pills=["Until Kasten 9.0.10", "Action hook was the only way", "One blueprint per namespace"],
    icon='search', polarity='neg')

d.table_slide(
    "From the documentation example to late capture",
    "Same BACKUP DATABASE, same RESTORE DATABASE — the data path is what changes",
    ["", "Documentation example", "This blueprint"],
    [
        ("Backup goes to", "kando location push to object storage", "A staging PVC Kasten snapshots"),
        ("Data mover", "The blueprint", "Kasten"),
        ("Actions", "backup, restore, delete", "backupPrehook, backupPosthook, restorePosthook"),
        ("Where sqlcmd runs", "Tools image (kubectl 1.18)", "Inside the SQL Server container"),
        ("Password", "Rendered into the pod command", "Never leaves the SQL Server pod"),
        ("Permissions", "Kanister pod account", "pods/exec in this namespace only"),
        ("Binding", "Annotation", "BlueprintBinding on the Deployment"),
    ],
    col_widths=[2.6, 4.6, 5.1],
    row_heights=[0.5] + [0.55] * 7)

d.flow(
    "Pattern 6 — database backup on a staging PVC",
    "Kasten snapshots the .bak files, not the SQL Server data volume (the policy excludes it)",
    [
        ('database', "backupPrehook",
         "KubeOps creates the PVC.\nBACKUP DATABASE to /tmp\nin SQL Server, tar over\n"
         "kubectl exec to the PVC"),
        ('shield_check', "KASTEN SNAPSHOTS IT",
         "Late capture adds the PVC\nto the snapshot set, after\nthe hook, before the\n"
         "snapshots"),
        ('delete', "backupPosthook",
         "Deletes the PVC.\nKasten checks it is gone\nand fails the backup if not"),
        ('loop_left', "restorePosthook",
         "Streams the .bak files\nback, RESTORE DATABASE\nWITH REPLACE, deletes\nthe PVC"),
    ],
    note=("Why the copy through kubectl exec?",
          "BACKUP DATABASE TO DISK writes on SQL Server's own filesystem, and a running pod "
          "cannot get a new volume. So the files are written inside the SQL Server container "
          "first, then streamed to the staging PVC by a pod that mounts it."))

d.two_dark(
    "THE BACKUP PASSES", "THROUGH THE NODE'S DISK",
    [
        ("1", "THE RISK", "/tmp is the container's writable layer",
         "The .bak files land on the node's own disk, not on a PVC. A large database can fill "
         "it, and the kubelet then evicts pods — possibly SQL Server itself. The database "
         "needs twice its size free for a while, and a crash leaves files in /tmp."),
        ("2", "WHAT THE BLUEPRINT DOES", "And the better design",
         "Before the first BACKUP it compares the data files' size with the free space in /tmp "
         "and fails early. For SQL Server, a permanent backup PVC mounted by the Deployment "
         "(pattern 3) avoids the copy, the node disk and the exec permission."),
    ])

d.table_slide(
    "Blueprint actions",
    "Every PrepareData phase sets failOnError: true, every sqlcmd call uses -b",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Creates the PVC <deployment>-dump. PrepareData (kasten-tools, ServiceAccount with "
         "pods/exec) checks free space, runs BACKUP DATABASE … CHECKSUM for each online user "
         "database in SQL Server, streams the files to the PVC, cleans /tmp, sync. Declares "
         "the PVC in the lateCapture artifact."),
        ("backupPosthook",
         "Deletes the staging PVC. Kasten then checks it is gone or terminating."),
        ("restorePosthook",
         "Waits for SQL Server, streams the .bak files from the restored PVC into the "
         "container, RESTORE DATABASE … WITH REPLACE, CHECKSUM, cleans /tmp, deletes the PVC."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.3, 0.7, 1.0],
    cards=[
        ('error_warning', "A FAILED BACKUP MUST FAIL", [
            "With failOnError false, PrepareData returns success whatever BACKUP returned. "
            "Kasten would snapshot an empty PVC and mark the restore point green."]),
        ('lock_security', "THE PASSWORD STAYS IN SQL SERVER", [
            "sqlcmd runs inside the SQL Server container and reads its SA_PASSWORD variable: "
            "the blueprint never reads the Secret."]),
    ])

d.cards(
    "What the restore point holds, and what it does not",
    "The policy excludes the SQL Server data PVC",
    [
        ('checkbox_multiple', "PROTECTED", [
            "Every online user database, as a full backup with CHECKSUM. Every Kubernetes "
            "object of the namespace: Deployment, Secret, Service, the RBAC of the blueprint.",
        ]),
        ('close_circle', "NOT PROTECTED", [
            "The system databases (master, msdb): logins and SQL Agent jobs are not in the "
            "backup. The data volume. No point-in-time recovery.",
        ]),
        ('error_warning', "MANUAL STEP AFTER LOSING THE NAMESPACE", [
            "A Deployment does not recreate its PVC. Create the empty mssql-data PVC before "
            "the restore, or the SQL Server pod stays Pending.",
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
        ("Database", "SQL Server 2019 CU27"),
        ("Tools image", "kasten-tools:9.0.10"),
    ],
    col_widths=[3.3, 2.55],
    row_heights=[0.55] * 5,
    side=('settings', "BEFORE YOU USE IT",
          "Check the flag: kubectl get cm k10-features -n kasten-io must show "
          "lateCapture: true.\n"
          "Apply rbac.yaml in the application namespace.\n"
          "Check the node has free disk for the .bak files.\n"
          "The policy needs a Location profile, and must export: local snapshots are "
          "deleted with the namespace."))

d.ending("A PVC FOR THE BACKUP, ONLY DURING THE BACKUP",
         "It works for SQL Server, at the cost of a copy through the node's disk.\n"
         "github.com/kastenhq/blueprint-ai — mssql-logical-late-capture-example")

d.save()
