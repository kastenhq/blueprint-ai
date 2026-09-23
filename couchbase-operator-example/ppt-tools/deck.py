#!/usr/bin/env python3
"""Concept deck for couchbase-operator-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py couchbase-operator-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('couchbase-operator', 'couchbase-operator-example')

d.title("COUCHBASE WITH KASTEN\nAND A KEEPER PVC",
        "cbbackupmgr writes to a permanent PVC; Kasten snapshots it")

d.agenda([
    "Why a keeper, and not the pods themselves",
    "The pattern and the data path",
    "One keeper per cluster: the naming convention",
    "Blueprint actions",
    "Restore, and the manual pause it needs",
])

d.hero(
    "THE OPERATOR OWNS THE PODS,", "SO OWN A KEEPER INSTEAD",
    body="Couchbase pods are managed directly by the operator — there is no StatefulSet "
         "to modify.\nA dedicated keeper Deployment is the natural isolation boundary.",
    pills=["No custom image", "PVC pre-exists", "Stable binding target"],
    icon='server_hardware')

d.cards(
    "Why pattern 4 fits this operator so well",
    "Four separate reasons point the same way",
    [
        ('node_tree', "NO STATEFULSET TO HOOK", [
            "The operator manages Couchbase pods directly, so there is no StatefulSet "
            "to annotate or bind to. A keeper Deployment gives the blueprint something "
            "stable to attach to.",
        ]),
        ('instance_hexagon', "THE TOOL IS ALREADY THERE", [
            "cbbackupmgr ships inside the couchbase/server image the cluster already "
            "runs, so the keeper needs no custom image at all — a rare luxury among "
            "these blueprints.",
        ]),
        ('shield_check', "DISCOVERY SEES THE PVC", [
            "The backup PVC is permanent, so it already exists when Kasten runs PVC "
            "discovery — before backupPrehook. No BackupAction preHook is needed to "
            "get it into the restore point.",
        ]),
        ('checkbox_multiple', "SRP AND OCP HOLD", [
            "The keeper Deployment is a BlueprintBinding target, so each workload type "
            "keeps its own blueprint and adding a new one never means editing this one.",
        ]),
    ],
    body_size=12)

d.flow(
    "Pattern 4 — dump on a permanent keeper PVC",
    "The keeper connects over the network; Kasten snapshots both PVCs",
    [
        ('server_hardware', "KEEPER DEPLOYMENT",
         "cb-example-keeper mounts\ncb-example-keeper PVC at\n/backup — permanently"),
        ('upload_cloud', "backupPrehook: cbbackupmgr",
         "KubeExec runs cbbackupmgr\nbackup in the keeper, which\nreaches the cluster "
         "over the\nnetwork"),
        ('database_storage', "INCREMENTAL ARCHIVE",
         "Written to\n/backup/data/kasten-repo —\nfull on the first run,\nincremental "
         "after that"),
        ('shield_check', "KASTEN SNAPSHOTS BOTH PVCs",
         "The data PVC and the backup\nPVC. On restore the backup\nPVC comes back and "
         "is\nreplayed into Couchbase"),
    ],
    note=("Incrementality, and what it costs at restore time.",
          "cbbackupmgr takes a full backup on the first run and incrementals afterwards. "
          "Each restore replays the whole chain from the archive, which gives a "
          "consistent point-in-time view — so the restore reads more than the last "
          "backup wrote."))

d.hero(
    "WITHOUT A SYNC", "THE INDEX IS ZERO BYTES",
    body="cbbackupmgr keeps sqlite index files. Acknowledged but unflushed, they appear "
         "in the snapshot\nwith the right names and no content — and the restore fails "
         "with \"invalid Rift index\".",
    pills=["sync is the last thing the prehook does"],
    icon='error_warning', polarity='neg')

d.table_slide(
    "Versions and the naming convention",
    "The convention is required — the blueprint derives the cluster name from the keeper name",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.4"),
        ("Operator Helm chart", "couchbase-operator-2.90.0"),
        ("Couchbase Operator", "2.9.0"),
        ("Couchbase Server", "7.6.6"),
    ],
    col_widths=[3.5, 2.35],
    row_heights=[0.55] * 6,
    side=('node_tree', "ONE KEEPER PER CLUSTER",
          "The keeper Deployment and its PVC are named after the cluster they serve, "
          "with a -keeper suffix: cb-example-keeper for the cb-example cluster.\n"
          "When several Couchbase clusters share a namespace, deploy one keeper per "
          "cluster following the same convention.\n"
          "The BlueprintBinding targets the label couchbase-keeper: \"true\" rather than "
          "a deployment name, so one binding covers every keeper in the fleet.\n"
          "Each keeper's env block carries USERNAME, PASSWORD and CLUSTER for its own "
          "cluster. KubeExec phases inherit those from the pod environment, so the "
          "blueprint needs no objects block and no per-cluster ConfigMap or Secret "
          "reference at all."))

d.table_slide(
    "Blueprint actions",
    "Four phases across three actions. Deploy the Blueprint and BlueprintBinding in kasten-io.",
    ["Action / phase", "Function", "What it does"],
    [
        ("backupPrehook\ncbBackup", "KubeExec",
         "Creates the backup repository if absent, then runs cbbackupmgr backup in the "
         "keeper — full on the first run, incremental after. Calls sync last, to flush "
         "the page cache to the volume before Kasten snapshots it."),
        ("backupPosthook\ncbVerify", "KubeExec",
         "Runs cbbackupmgr info to confirm the backup really reached the PVC before the "
         "snapshot is taken."),
        ("restorePosthook\nresumeCluster", "KubeTask",
         "Derives the cluster name from the keeper Deployment name, patches "
         "spec.paused=false, then polls until the cluster reports Available."),
        ("restorePosthook\ncbRestore", "KubeExec",
         "Polls the Couchbase REST API until it accepts connections — Available does "
         "not mean the API is ready — clears stale archive locks, then runs cbbackupmgr "
         "restore with --force-updates, --restore-partial-backups and --purge."),
    ],
    col_widths=[2.6, 1.7, 8.0],
    row_heights=[0.5, 1.4, 0.8, 1.0, 1.55],
    body_size=10.5)

d.split_dark(
    "Restore",
    "Pause the cluster.\n", "Then let Kasten work.",
    [
        ("01", "PAUSE THE COUCHBASECLUSTER BY HAND",
         "If the operator keeps reconciling, it races Kasten's PVC deletion and the "
         "restore times out. Pause the CouchbaseCluster manually before triggering the "
         "restore — this is not automated from a hook."),
        ("02", "THE POSTHOOK PUTS IT BACK",
         "Once the backup PVC is restored, restorePosthook unpauses the cluster, waits "
         "for it to become Available, waits again for the REST API, and replays the "
         "whole backup chain with cbbackupmgr restore."),
    ],
    left_body="Two waits, not one: the cluster reporting Available does not guarantee "
              "the REST API accepts connections yet. The blueprint polls the API "
              "separately, because a restore that starts too early fails in a way that "
              "looks like a data problem rather than a timing one.")

d.ending("THE KEEPER IS THE BOUNDARY",
         "The operator owns the pods. The keeper owns the backup PVC. Kasten snapshots "
         "it.\ngithub.com/kastenhq/blueprint-ai — couchbase-operator-example")

d.save()
