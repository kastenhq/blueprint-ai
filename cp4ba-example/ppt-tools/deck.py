#!/usr/bin/env python3
"""Concept deck for cp4ba-example. Content only — the slide machinery lives in the
shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py cp4ba-example

Written from 26.0.0/README.md. Update it in the same commit as any material change to
the blueprint, the scripts or the restore procedure.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('cp4ba', 'cp4ba-example')

d.title("CP4BA: FILENET AND ODM\nOFFLINE BACKUP WITH KASTEN",
        "Stop everything, snapshot every PVC, start everything again")

d.agenda([
    "Why FileNet cannot be snapshotted while it runs",
    "What this protects, and what it does not",
    "The pattern: IBM's offline backup as a policy action hook",
    "What the backup does, step by step",
    "Restore, and what the first restore taught us",
])

d.hero(
    "TWO STORES,", "ONE MOMENT",
    body="A FileNet document is a database row plus a file in a storage area. Kasten "
         "snapshots each PVC on its own,\nso if CPE keeps writing, the row and the file "
         "can come from different moments: the document is listed but cannot be opened.",
    pills=["Database row", "Content file", "Same instant, or broken"],
    icon='database', polarity='neg')

d.cards(
    "What this blueprint protects — and what it does not",
    "An example built and validated on one deployment, not a general CP4BA solution",
    [
        ('checkbox', "FILENET AND ODM ON PVCs", [
            "The Content (FileNet) and Decisions (ODM) capabilities.",
            "",
            "Their PostgreSQL databases, storage areas and configuration, all on PVCs "
            "in the CP4BA namespace.",
        ]),
        ('cloud', "NOT EXTERNAL DATA SERVICES", [
            "A storage area on an external S3 bucket, or an external database, is "
            "outside the namespace.",
            "",
            "Kasten does not capture it. Protect it by its own means, at the same "
            "moment.",
        ]),
        ('settings', "NOT OTHER COMPONENTS", [
            "Consistency depends on stopping everything that writes.",
            "",
            "A new capability, workload type or CronJob means updating the "
            "offline/online scripts and validating again.",
        ]),
        ('error_warning', "STARTER ONLY", [
            "Validated on a CP4BA Starter deployment on a test cluster.",
            "",
            "Not validated on a production installation, and disaster recovery is not "
            "validated yet.",
        ]),
    ], style='standard', body_size=11)

d.split_dark(
    "The pattern",
    "IBM's offline backup,\n", "automated.",
    [
        ("01", "NOTHING WRITES, SO ORDER DOES NOT MATTER",
         "Once CPE, Navigator, ODM and their databases are stopped, the PVCs stop "
         "changing. Kasten can snapshot them one by one, and they all describe the same "
         "moment."),
        ("02", "IT COSTS NO EXTRA AVAILABILITY",
         "IBM's own offline procedure already makes CP4BA unavailable, not even "
         "read-only. Stopping the whole namespace instead of a list of names is simpler, "
         "and cannot miss a writer."),
    ],
    left_body="This is the Quiesce pattern, applied to the whole namespace at once. "
              "Online backup would need storage consistency groups and suspended "
              "database writes, which Kasten alone cannot provide.")

d.split_dark(
    "Why a policy action hook",
    "One namespace,\n", "one switch.",
    [
        ("01", "OFFLINE IS ONE OPERATION, NOT ONE PER WORKLOAD",
         "A BlueprintBinding hook runs per workload, in an order Kasten chooses, while "
         "the others keep writing. There is no single workload to bind to: about 40 "
         "Deployments, three PostgreSQL clusters and the operators."),
        ("02", "IT RUNS ONCE, BEFORE ANY SNAPSHOT",
         "The policy preHook runs before discovery and before any snapshot. onSuccess "
         "and onFailure run once, after the backup. That is exactly the shape of IBM's "
         "procedure."),
    ],
    left_body="The cost: the hook is attached to the policy and only knows the "
              "namespace name. That is acceptable because the namespace holds one CP4BA "
              "deployment and nothing else.")

d.flow(
    "What one backup does",
    "Measured on the test cluster: about 2 minutes offline, about 10 minutes before users can work again",
    [
        ('lock_security', "PREHOOK: OFFLINE", "Suspend CronJobs.\nHibernate PostgreSQL.\n"
                                       "Stop operators, then\nworkloads. Wait until no\n"
                                       "pod uses a PVC."),
        ('search', "DISCOVERY", "Kasten captures the\nobjects and finds the\n28 PVCs."),
        ('history', "SNAPSHOTS", "28 CSI snapshots\non Ceph, in about\n25 seconds."),
        ('rocket_launch', "ONSUCCESS: ONLINE", "PostgreSQL first, then\nworkloads, then\n"
                                      "operators, then\nCronJobs."),
        ('upload_cloud', "EXPORT", "To the Location\nprofile, while CP4BA\nis already "
                                   "online."),
    ],
    note="Most of the outage is the restart: CPE, Navigator and Decision Center are "
         "Java servers that take 5 to 7 minutes to become ready.")

d.table_slide(
    "Blueprint actions",
    "The blueprint holds no logic: it runs the same two scripts an operator runs for a restore",
    ["Action (policy hook)", "What it does"],
    [
        ("backupPrehook (preHook)", "scale-down.sh: CP4BA offline, no pod references a PVC"),
        ("backupPosthook (onSuccess)", "scale-up.sh: CP4BA online again"),
        ("backupPosthookOnFailure (onFailure)", "scale-up.sh: safe at any point, it starts "
                                                "only what scale-down.sh recorded"),
    ],
    col_widths=[2.6, 3.25],
    row_heights=[0.5, 0.75, 0.75, 0.95],
    side=('file_list', "HOW THE STATE IS KEPT",
          "Before scaling a workload to 0, scale-down.sh saves its replica count in an "
          "annotation on the workload itself.\n"
          "scale-up.sh reads it back, so only what was running is started again.\n"
          "A workload already at 0 is skipped, so running the script twice never "
          "overwrites a saved count with 0.\n"
          "The scripts reach the hook pod through a ConfigMap, so the backup and the "
          "manual restore can never run different code."))

d.steps_dark(
    "Restore — by hand, in the same namespace",
    "Only the PVCs come back",
    [
        ('lock_security', "1. SCALE-DOWN.SH",
         "Take CP4BA offline. Kasten cannot replace a PVC that a pod still uses."),
        ('history', "2. RESTORE THE PVCs ONLY",
         "A data-only restore. CRs, Secrets and Deployments stay as they are, so the "
         "operators never see a half-restored object."),
        ('rocket_launch', "3. SCALE-UP.SH",
         "Bring CP4BA online. It also repairs the PostgreSQL PVCs (next slide)."),
        ('search_eye', "4. OPEN THE DOCUMENTS",
         "Listing is not enough: download each test document and compare its checksum."),
    ])

d.cards(
    "What the first restore taught us",
    "Each one stopped the restore; the scripts now handle all of them",
    [
        ('time_clock', "A CRONJOB EVERY 5 MINUTES", [
            "icp4adeploy-rr-backup mounts a PVC. Its new pod kept the PVC from being "
            "deleted, and the restore waited.",
            "",
            "Fix: suspend every CronJob first.",
        ]),
        ('checkbox_multiple', "A FINISHED POD STILL BLOCKS", [
            "A pod in phase Succeeded still referenced the PVC, and that was enough to "
            "keep it.",
            "",
            "Fix: delete the Jobs, finished ones too; check pods in every phase.",
        ]),
        ('database', "POSTGRESQL IS NOT A DEPLOYMENT", [
            "IBM's EDB operator owns the pods. Scaling cannot stop them.",
            "",
            "Fix: hibernate each cluster while its operator still runs.",
        ]),
        ('node_tree', "RESTORED PVCs HAVE NO OWNER", [
            "Kasten drops ownerReferences. The operator counted no PVC and called the "
            "cluster unrecoverable. The data was intact.",
            "",
            "Fix: scale-up.sh puts the owner back.",
        ]),
    ], style='negative', body_size=10.5)

d.table_slide(
    "Versions",
    "Validated on 2026-11-10: the deleted test document came back, opened, and matched its checksum",
    ["Component", "Version"],
    [
        ("OpenShift / Kubernetes", "4.21.30 / 1.34.9"),
        ("Kasten", "9.0.10 (OLM operator)"),
        ("Kasten tools image", "kasten-tools:9.0.10"),
        ("CP4BA", "26.0.0-IF003, Starter"),
        ("FileNet (Content Cortex) / ODM", "26.0.0 / 9.6.0.0-IF005"),
        ("PostgreSQL", "16.15, ibm-pg-operator 28.4.3"),
    ],
    col_widths=[2.7, 3.15],
    row_heights=[0.5] + [0.52] * 6,
    side=('error_warning', "NOT VALIDATED YET",
          "Disaster recovery: delete the namespace, install CP4BA again, restore. "
          "This is the next step.\n"
          "The ODM restore check.\n"
          "A production deployment, with external databases, several replicas and "
          "other capabilities."))

d.ending("STOP EVERYTHING, SNAPSHOT EVERYTHING",
         "Then the order of the snapshots no longer matters.\n"
         "github.com/kastenhq/blueprint-ai — cp4ba-example")

d.save()
