#!/usr/bin/env python3
"""Concept deck for ocp-etcd-backup-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py ocp-etcd-backup-example     # from the repo root

Every claim below comes from this blueprint's README.md. When the README changes
materially, update this file and rebuild.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('ocp-etcd-backup', 'ocp-etcd-backup-example')

# 1 — cover
d.title("OPENSHIFT ETCD BACKUP\nWITH KASTEN",
        "A permanent keeper PVC, and Kasten as the data mover")

# 2 — agenda
d.agenda([
    "Why an etcd backup is not an OpenShift backup",
    "The pattern: a permanent keeper PVC",
    "Architecture, versions and the keeper image",
    "Prerequisites and deployment",
    "Blueprint actions",
    "Restore: two different things",
])

# 3 — the warning, up front
d.hero(
    "AN ETCD SNAPSHOT", "IS NOT A CLUSTER BACKUP",
    body="It captures the cluster's API objects at a point in time — and nothing "
         "else.\nYour databases and files are not in it.",
    pills=["No PVC data", "No granular restore", "Full-cluster only"],
    icon='error_warning', polarity='neg')

# 4 — the three limitations
d.cards(
    "What an etcd snapshot does not give you",
    "Take it for what it is, and do not mistake it for a recovery strategy",
    [
        ('close_circle', "NO APPLICATION DATA", [
            "No PVC contents. No object storage.",
            "Only the cluster's API objects at a point in time.",
            "Your databases and files are not in the snapshot.",
        ]),
        ('file_close', "NO GRANULAR RESTORE", [
            "An etcd restore is all-or-nothing.",
            "The entire cluster rolls back to the snapshot.",
            "You cannot restore a single namespace, workload or PVC.",
        ]),
        ('error_warning', "DISRUPTIVE AND DANGEROUS", [
            "cluster-restore.sh takes the API server down and rebuilds the control "
            "plane on every master.",
            "A full-cluster disaster operation, not routine recovery.",
        ]),
    ],
    style='negative',
    note=("So why this blueprint at all?",
          "Two reasons: legacy practices and compliance requirements that mandate etcd "
          "backups, and forensics — inspecting the cluster state at a past point in "
          "time. The blueprint makes those etcd snapshots safe, scheduled and "
          "snapshot-managed by Kasten."))

# 5 — the recommended recovery model
d.split_dark(
    "The recommended OpenShift recovery model",
    "Rebuild the\nplatform.\n", "Restore the apps.",
    [
        ("01", "REBUILD WITH GITOPS",
         "Rebuild the cluster and its infrastructure operators with ArgoCD or Flux — "
         "declaratively and reproducibly, not from an etcd snapshot."),
        ("02", "RESTORE THE APPS WITH KASTEN",
         "Granular, non-disruptive, and with their PVC data and object storage. This "
         "is exactly what an etcd snapshot can never do."),
    ],
    left_body="Kasten can also back up the infrastructure-operator namespaces "
              "themselves. Treat that as a convenience for fail-over, not the primary "
              "path — GitOps stays the source of truth for the platform.")

# 6 — pattern and architecture
d.flow(
    "Pattern 4 — dump on a permanent keeper PVC",
    "A dedicated keeper Deployment mounts the backup PVC and is pinned to a "
    "control-plane node",
    [
        ('folder_directory', "HOST /etc/kubernetes",
         "cluster-backup.sh, certs and\nstatic-pod resources, mounted\nread-only "
         "through hostPath"),
        ('server_hardware', "KEEPER POD",
         "ocp-etcd-backup image with\netcdctl and etcdutl; runs\ncluster-backup.sh "
         "--force"),
        ('database_storage', "PERMANENT PVC",
         "ocp-etcd-backup, 10Gi\nsnapshot_*.db and\nstatic_kuberesources_*.tar.gz"),
        ('shield_check', "KASTEN RESTORE POINT",
         "Kasten takes a CSI snapshot of\nthe keeper PVC — the PVC\nsnapshot is the "
         "backup"),
    ],
    note=("Why a permanent PVC?",
          "Kasten discovers the PVCs to snapshot before backupPrehook runs, so a PVC "
          "created inside the hook would never reach the restore point. The keeper PVC "
          "already exists at discovery time and is captured correctly — and because the "
          "blueprint binds to the keeper Deployment through a BlueprintBinding, adding "
          "another workload never means editing this blueprint."))

# 7 — versions and the keeper image
d.table_slide(
    "Tested versions and the keeper image",
    "Blueprint behaviour is version-sensitive — these are the versions it was "
    "developed against",
    ["Component", "Version"],
    [
        ("OpenShift", "4.18.6"),
        ("Kubernetes", "1.31.6"),
        ("Kasten", "8.5.12"),
        ("etcd (cluster)", "3.5.18"),
        ("Keeper image", "ocp-etcd-backup:v3.5.18"),
    ],
    col_widths=[3.1, 2.75],
    row_heights=[0.55] * 6,
    side=('instance_hexagon', "THE KEEPER IMAGE",
          "ghcr.io/kastenhq/blueprint-ai/ocp-etcd-backup:v3.5.18\n"
          "Base image: registry.access.redhat.com/ubi9/ubi-minimal\n"
          "etcdctl and etcdutl copied from quay.io/coreos/etcd:v3.5.18, matched to the "
          "cluster's etcd version\n"
          "kubectl v1.31.6, matched to the cluster's Kubernetes version — used by the "
          "operator-progression check\n"
          "bash, tar, gzip, coreutils and util-linux, for sync and for kubectl cp\n"
          "cluster-backup.sh is not baked in: it is invoked from the control-plane node "
          "through the read-only hostPath mount\n"
          "There is no operator — etcd is part of the OpenShift control plane."))

# 8 — prerequisites
d.cards(
    "Three prerequisites before the blueprint works",
    "All three are cluster-specific — check each one against your own environment",
    [
        ('scan_target', "AN ANNOTATED VOLUMESNAPSHOTCLASS", [
            "Kasten only uses a VolumeSnapshotClass carrying the annotation "
            "k10.kasten.io/is-snapshot-class: true",
            "",
            "The manifests use managed-csi with csi-azuredisk-vsc, the values of our "
            "test cluster. Replace them with a CSI storage class that supports "
            "snapshots on yours. Legacy in-tree classes such as gp2 do not work.",
        ]),
        ('lock_security', "THE PRIVILEGED SCC FOR THE KEEPER", [
            "The keeper mounts host /etc/kubernetes, runs as root and uses "
            "seLinuxOptions.type: spc_t",
            "",
            "That needs the privileged SCC on its ServiceAccount and privileged Pod "
            "Security Admission on the namespace. Grant both, then restart the "
            "Deployment so the pod is admitted.",
        ]),
        ('search_eye', "READ ACCESS TO CLUSTEROPERATORS", [
            "backupPrehook verifies that no control-plane operator is mid-rollout, "
            "because a rollout in flight can leave the captured static-pod resources "
            "inconsistent with etcd.",
            "",
            "cluster-backup.sh does this itself only without --force, through an "
            "endpoint an ordinary pod cannot reach. The blueprint replicates the check "
            "with kubectl get co, which needs a cluster-scoped read grant.",
        ]),
    ])

# 9 — blueprint actions
d.table_slide(
    "Blueprint actions",
    "Two actions, both KubeExec into the keeper pod. Deploy the Blueprint and the "
    "BlueprintBinding in kasten-io.",
    ["Action", "Function", "What it does"],
    [
        ("backupPrehook", "KubeExec (keeper)",
         "Checks that kube-apiserver, kube-controller-manager, kube-scheduler and etcd "
         "are not Progressing, and aborts if a rollout is in flight. Then cleans "
         "/backup/etcd-backup, runs cluster-backup.sh --force, normalises "
         "__POSSIBLY_DIRTY__ filenames, and runs sync."),
        ("backupPosthook", "KubeExec (keeper)",
         "Verifies that a non-empty snapshot_*.db exists and runs etcdutl snapshot "
         "status on it. Checks that the static_kuberesources_*.tar.gz is present."),
    ],
    col_widths=[2.3, 2.15, 7.85],
    row_heights=[0.5, 1.35, 0.95],
    cards=[
        ('close_circle', "NO RESTORE ACTION — BY DESIGN", [
            "The only meaningful restore of etcd is the manual control-plane "
            "procedure, which must not run from a hook. Restoring the keeper PVC "
            "itself needs no preparation, so there is nothing for a restorePosthook "
            "to do."]),
        ('close_circle', "NO DELETE ACTION — BY DESIGN", [
            "Deleting the Kasten restore point deletes the underlying PVC snapshot, "
            "which is the only copy of the backup. There is no external object-store "
            "copy to clean up. This is the trade-off for dropping the azcopy upload "
            "of the original repository."]),
    ])

# 10 — deploy and run
d.steps_dark(
    "Deploy and run", "Four steps to a restore point",
    [
        ('rocket_launch', "DEPLOY THE KEEPER",
         "kubectl apply -f keeper.yaml creates the etcd-backup namespace, the "
         "ServiceAccount, the 10Gi permanent PVC and the keeper Deployment. Grant the "
         "privileged SCC, label the namespace, then restart the rollout."),
        ('checkbox_multiple', "VALIDATE BY HAND — OPTIONAL",
         "Exec into the keeper and run cluster-backup.sh --force yourself. You should "
         "get a snapshot_*.db of about 90 MB and a static_kuberesources_*.tar.gz."),
        ('settings_gear', "DEPLOY THE BLUEPRINT",
         "kubectl apply -f blueprint.yaml and blueprintbinding.yaml, both in "
         "kasten-io. The binding matches Deployments labelled etcd-backup-keeper=true."),
        ('upload_cloud', "BACK UP THROUGH KASTEN",
         "Create an on-demand policy selecting the etcd-backup namespace, with a "
         "Location profile so Kasten wires up the Kanister hooks, then trigger a "
         "RunAction."),
    ])

# 11 — the two restores
d.two_dark(
    "Restore", "Two different things are called restore",
    [
        ("01", "RESTORE THE BACKUP ARTIFACT", "What this blueprint and Kasten do.",
         "A Kasten restore of the etcd-backup namespace brings back the keeper PVC and "
         "Deployment with the snapshot_*.db and static_kuberesources_*.tar.gz on it. "
         "Fully automated and tested.\n\n"
         "Create the RestoreAction in the application namespace. On Kasten 8.x, name "
         "the Location profile explicitly in spec.profile — extraction from the "
         "RestorePointContent is unreliable there and was fixed in 9.0."),
        ("02", "RESTORE THE CONTROL-PLANE ETCD", "Manual, disruptive, never automated.",
         "Rolling the cluster back to that snapshot is the OpenShift \"Restoring to a "
         "previous cluster state\" procedure: copy the files onto a recovery master, "
         "run cluster-restore.sh, restart the kubelet, then recover the remaining "
         "control-plane nodes.\n\n"
         "This is never driven from a Kasten hook. It takes the API server down and "
         "must be run by an administrator."),
    ])

# 12 — close
d.ending("KASTEN IS THE DATA MOVER",
         "No object storage client. No cloud identity. The PVC snapshot is the "
         "backup.\ngithub.com/kastenhq/blueprint-ai — ocp-etcd-backup-example")

d.save()
