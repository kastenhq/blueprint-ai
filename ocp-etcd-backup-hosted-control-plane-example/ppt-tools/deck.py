#!/usr/bin/env python3
"""Concept deck for ocp-etcd-backup-hosted-control-plane-example. Content only —
the slide machinery lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py ocp-etcd-backup-hosted-control-plane-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('hcp-etcd-backup', 'ocp-etcd-backup-hosted-control-plane-example')

d.title("HOSTED CONTROL PLANE\nFLEET ETCD BACKUP",
        "One keeper on the hub backs up the etcd of every guest cluster")

d.agenda([
    "First: an etcd backup is not an OpenShift backup",
    "Where hosted control planes sit in the architecture",
    "How this differs from the standalone blueprint",
    "The pattern, and zero-touch onboarding",
    "Blueprint actions",
])

d.hero(
    "AN ETCD SNAPSHOT", "IS NOT A CLUSTER BACKUP",
    body="It captures only each guest cluster's API objects at a point in time. No PVC "
         "contents,\nno object storage — and an etcd restore is all-or-nothing.",
    pills=["No application data", "No granular restore", "Disruptive"],
    icon='error_warning', polarity='neg')

d.split_dark(
    "The recommended recovery model for a hosted cluster",
    "Recreate the guest.\n", "Restore its apps.",
    [
        ("01", "RECREATE THE HOSTEDCLUSTER DECLARATIVELY",
         "The HostedCluster and its NodePool are objects. Recreate them through GitOps "
         "— reproducibly — rather than rolling a guest's etcd back to a snapshot."),
        ("02", "RESTORE THE GUEST'S APPLICATIONS WITH KASTEN",
         "Granular, non-disruptive, and with their PVC data. This is exactly what an "
         "etcd snapshot can never do."),
    ],
    left_body="So why this blueprint? The same reasons as the standalone one: "
              "compliance and legacy practices that mandate etcd snapshots, and "
              "forensics — inspecting a guest's cluster state at a past point in time. "
              "It makes fleet-wide etcd snapshots safe, scheduled and managed by "
              "Kasten. It is not a substitute for the model above. The documented "
              "restore pauses reconciliation and scales the etcd StatefulSet, so it is "
              "disruptive by design.")

d.table_slide(
    "Four layers, and which one owns what",
    "Knowing which layer provides which API explains why the substitutions in this guide are safe",
    ["Layer", "Main API resources", "What the object represents"],
    [
        ("ACM",
         "MultiClusterHub, Policy, Application, MultiClusterObservability",
         "The management layer: fleet governance, application lifecycle, observability"),
        ("MCE",
         "ManagedCluster, MultiClusterEngine, Hive ClusterDeployment",
         "One registry entry per managed cluster — hub, hosted or imported"),
        ("HyperShift", "HostedCluster, NodePool",
         "A guest's control plane and its worker-node groups"),
        ("KubeVirt",
         "VirtualMachine, VirtualMachineInstance, HyperConverged",
         "The actual worker-node VMs, running on the hub"),
    ],
    col_widths=[1.9, 5.0, 5.4],
    row_heights=[0.5, 0.78, 0.78, 0.62, 0.78],
    body_size=10.5)

d.table_slide(
    "The same pattern, a different etcd",
    "This is the hosted-control-plane counterpart of the standalone OpenShift etcd blueprint",
    ["", "Standalone OCP etcd", "Hosted control plane (this blueprint)"],
    [
        ("Where etcd lives",
         "Static pods on the master nodes, data under host /etc/kubernetes",
         "An ordinary StatefulSet etcd — pods etcd-0/1/2 — with PVCs in each guest's "
         "control-plane namespace on the hub"),
        ("Backup mechanism",
         "The node script cluster-backup.sh, reached over a hostPath",
         "The documented etcdctl snapshot save, through kubectl exec into an etcd pod "
         "using localhost:2379 and the pod's own client certs"),
        ("Scope", "One cluster",
         "The whole fleet — one keeper loops over every HostedCluster"),
    ],
    col_widths=[2.1, 4.6, 5.6],
    row_heights=[0.5, 1.1, 1.25, 0.6],
    body_size=10.5,
    cards=[
        ('rocket_launch', "ZERO-TOUCH ONBOARDING", [
            "One keeper Deployment and one PVC in the clusters namespace back up every "
            "HostedCluster in that namespace. Add a new guest and it is protected on "
            "the next policy run — no per-guest namespace, no RBAC, no configuration."]),
        ('checkbox_multiple', "THE KEEPER TOUCHES NO HOST", [
            "Unlike the standalone blueprint, this keeper needs no hostPath and no "
            "privileged access to a node. A hosted cluster's etcd is an ordinary "
            "StatefulSet on the hub, so kubectl exec is enough."]),
    ])

d.flow(
    "Pattern 4 — one permanent keeper PVC for the fleet",
    "Per guest: check health, snapshot, stream out, verify",
    [
        ('loop_left', "LOOP OVER HOSTEDCLUSTERS",
         "Every HostedCluster in the\nkeeper's namespace, one at\na time"),
        ('scan_target', "CHECK HEALTH FIRST",
         "Available, not Progressing,\netcd ready — otherwise the\nguest is skipped and "
         "marked\nFAIL"),
        ('database_storage', "SNAPSHOT AND STREAM OUT",
         "etcdctl snapshot save inside\na running etcd pod, streamed\nto "
         "/backup/<name>/snapshot.db"),
        ('shield_check', "KASTEN SNAPSHOTS THE KEEPER PVC",
         "One PVC holding one snapshot\nper guest, verified with\netcdutl"),
    ],
    note=("Best-effort, and deliberately loud about it.",
          "The prehook saves every healthy guest and exits non-zero if any guest "
          "failed. A partial success is therefore reported as a failure you can see, "
          "rather than a green backup that quietly omits one cluster — while the guests "
          "that could be captured still are."))

d.table_slide(
    "Versions and blueprint actions",
    "Procedure source: Red Hat's guide to backing up and restoring etcd on a hosted cluster on-premise",
    ["Action", "Function", "What it does"],
    [
        ("backupPrehook", "KubeExec (keeper)",
         "Loops over all HostedClusters in the keeper's namespace. Per guest: checks "
         "Available, not Progressing and etcd ready, skipping and marking FAIL "
         "otherwise; runs etcdctl snapshot save inside a running etcd pod; streams the "
         ".db to /backup/<name>/snapshot.db; verifies with etcdutl. Prunes stale "
         "directories, then syncs. Exits non-zero if any guest failed."),
        ("backupPosthook", "KubeExec (keeper)",
         "Iterates over /backup/*/snapshot.db and re-verifies each with etcdutl "
         "snapshot status. Fails if none exist or any is invalid."),
    ],
    col_widths=[2.3, 2.0, 8.0],
    row_heights=[0.5, 1.75, 0.85],
    body_size=10.5,
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Hub OpenShift 4.18.6 with Kubernetes 1.31.6, MCE 2.8.7 as the HyperShift "
            "provider, a hosted cluster on OpenShift 4.18.6, hosted etcd 3.5.18, Kasten "
            "8.5.12, keeper image hcp-etcd-backup:v3.5.18 — UBI9-minimal with kubectl "
            "1.31.6 and etcdctl/etcdutl 3.5.18."]),
        ('search_eye', "A SEPARATE VERIFY IMAGE", [
            "The repository also ships a small etcd-verify image for validating a "
            "backup non-destructively: restore a snapshot into a fresh data directory, "
            "start a single-node etcd on it, and read keys back to prove the snapshot "
            "is genuinely restorable. It is not used by the blueprint itself."]),
    ])

d.ending("ONE KEEPER, THE WHOLE FLEET",
         "Add a guest and it is protected on the next run. No object storage client, no "
         "cloud identity.\ngithub.com/kastenhq/blueprint-ai — "
         "ocp-etcd-backup-hosted-control-plane-example")

d.save()
