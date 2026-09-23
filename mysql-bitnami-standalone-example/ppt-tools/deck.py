#!/usr/bin/env python3
"""Concept deck for mysql-bitnami-standalone-example. Content only — the slide
machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py mysql-bitnami-standalone-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('mysql-bitnami-standalone', 'mysql-bitnami-standalone-example')

d.title("BITNAMI MYSQL, STANDALONE,\nWITH KASTEN",
        "Quiesce around a CSI snapshot — and the lock-holding trick that makes it work")

d.agenda([
    "The workload and the pattern",
    "The background session: the detail that decides it",
    "Blueprint actions",
    "Restore, and why there is no pre-restore step",
    "One deployment gotcha worth knowing",
])

d.hero(
    "THE LOCK BELONGS", "TO THE SESSION",
    body="FLUSH TABLES WITH READ LOCK is released the moment the session that took it "
         "ends.\nAn exec that runs it and returns has already given the lock back.",
    pills=["Background session", "Kept alive deliberately", "Killed in the posthook"],
    icon='lock_security')

d.cards(
    "Why quiesce, and why nothing else",
    "The standalone chart is one StatefulSet replica with one RWO data PVC on InnoDB",
    [
        ('close_circle', "NO REPLICA TO FENCE", [
            "Standalone architecture means a single replica, so pattern 1 — fence and "
            "quiesce a replica — has nothing to work with.",
        ]),
        ('close_circle', "DUMPS BUY NOTHING", [
            "InnoDB recovers a crash-consistent snapshot by itself, so the logical-dump "
            "patterns add storage and lose incrementality for no gain in safety.",
        ]),
        ('close_circle', "NO BACKUP API", [
            "Bitnami ships a self-managed StatefulSet with no backup CRD, so the "
            "vendor-data-mover patterns do not apply either.",
        ]),
        ('checkbox_multiple', "SO: QUIESCE", [
            "Incremental through block-level CSI snapshots, so backup time stays "
            "constant as data grows, and it works with a BlueprintBinding — which "
            "keeps each workload type in its own blueprint.",
        ]),
    ],
    body_size=12)

d.flow(
    "Pattern 2 — quiesce, with the lock actually held",
    "Kasten snapshots the /bitnami/mysql data PVC; the blueprint only makes it consistent",
    [
        ('lock_security', "backupPrehook",
         "Opens a background mysql\nsession holding FLUSH TABLES\nWITH READ LOCK, kept "
         "alive\nwith DO SLEEP(3600)"),
        ('shield_check', "KASTEN SNAPSHOTS THE PVC",
         "The lock is genuinely held\nfor the duration, so the\ncaptured disk is "
         "consistent\nrather than merely quiet"),
        ('lock_unlock', "backupPosthook",
         "Kills the background\nsession, which releases\nthe lock"),
        ('loop_left', "RESTORE IS NATIVE KASTEN",
         "Scale down, swap the PVC,\nscale up. InnoDB performs\ncrash recovery on "
         "startup\nby itself"),
    ],
    note=("Why the sleep is not a hack.",
          "MySQL ties the read lock to the session that acquired it. A phase that runs "
          "the lock statement and exits therefore protects nothing — the lock is gone "
          "before Kasten starts. Holding the session open for the snapshot window is "
          "the only way to make the guarantee real, and the posthook is what ends it."))

d.table_slide(
    "Versions and blueprint files",
    "Detect your own Kasten version with helm ls -n kasten-io before matching image tags",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.13"),
        ("Helm chart", "bitnami/mysql 14.0.3"),
        ("Database", "MySQL 9.4.0"),
    ],
    col_widths=[3.3, 2.55],
    row_heights=[0.55] * 5,
    side=('file_list', "WHAT IS IN THE DIRECTORY",
          "blueprint.yaml — the quiesce, unquiesce and restore-verify actions.\n"
          "blueprintbinding.yaml — binds the blueprint to Bitnami MySQL primary "
          "StatefulSets fleet-wide, so one binding covers every instance.\n"
          "Prerequisite: a storage class backed by a CSI driver that supports "
          "snapshots, with a matching VolumeSnapshotClass registered with Kasten. "
          "Legacy in-tree classes such as gp2 on AWS do not support CSI snapshots and "
          "will fail.\n"
          "The root password is exposed by the Bitnami image as a file, not an "
          "environment variable: every command and every blueprint phase reads it with "
          "cat on MYSQL_ROOT_PASSWORD_FILE."))

d.table_slide(
    "Blueprint actions",
    "Three phases, and one deliberate absence",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Opens a background mysql session that holds FLUSH TABLES WITH READ LOCK, kept "
         "alive with DO SLEEP(3600), so the lock is still held while Kasten snapshots "
         "the PVC."),
        ("backupPosthook",
         "Kills the background session, releasing the lock and resuming normal writes."),
        ("restorePosthook",
         "Verifies the restored database accepts connections and holds the expected "
         "data."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.05, 0.75, 0.8],
    cards=[
        ('checkbox_multiple', "NO restorePrehook — AND THAT IS THE POINT", [
            "There is no operator reconciling this StatefulSet, so nothing races Kasten "
            "for ownership of the PVC. Restore is plain Kasten PVC replacement and "
            "needs no manual preparation — unlike every operator-managed database in "
            "this repository."]),
        ('error_warning', "THE BITNAMI IMAGE MOVED", [
            "As of late 2025 Bitnami moved its free container images to the "
            "bitnamilegacy Docker Hub repository, so the chart's default image no "
            "longer pulls. The deployment example redirects the image and sets "
            "global.security.allowInsecureImages=true. Adjust to your own mirror or "
            "Bitnami Secure Images subscription."]),
    ])

d.ending("A LOCK NOBODY HOLDS PROTECTS NOTHING",
         "The background session is what turns a quiesce statement into a real "
         "guarantee.\ngithub.com/kastenhq/blueprint-ai — mysql-bitnami-standalone-example")

d.save()
