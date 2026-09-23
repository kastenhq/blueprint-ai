#!/usr/bin/env python3
"""Concept deck for pvc-info-example. Content only — the slide machinery lives in
the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py pvc-info-example     # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('pvc-info', 'pvc-info-example')

d.title("RECORDING PVCs THAT\nARE NOT BACKED UP",
        "A policy preHook that captures volume identity into a ConfigMap")

d.agenda([
    "The problem: excluded PVCs leave no trace",
    "The idea: write the record where the backup will find it",
    "What lands in the ConfigMap",
    "Blueprint action and how to attach it",
])

d.hero(
    "EXCLUDE THE VOLUMES", "AND LOSE THE EVIDENCE",
    body="A policy that skips PVC snapshots also records nothing about which volumes "
         "existed.\nAfter a restore there is no way to tell what to re-provision.",
    pills=["No PVC snapshots", "No volume handles", "Nothing to reconcile"],
    icon='error_warning', polarity='neg')

d.cards(
    "Why a team would exclude PVCs at all",
    "This is a legitimate configuration, not a mistake — but it has a blind spot",
    [
        ('database_storage', "STORAGE-LEVEL REPLICATION", [
            "Some teams rely on the storage array or the cloud provider to replicate "
            "volumes, and configure Kasten policies to exclude PVCs from namespace "
            "backups.",
            "",
            "The namespace objects are still worth protecting; the volume data is "
            "handled elsewhere.",
        ]),
        ('close_circle', "THE BLIND SPOT", [
            "The restore point then contains the workload definitions but no record of "
            "which PVCs existed, what they requested, or which cloud volume each one "
            "was bound to.",
            "",
            "After a restore, matching workloads back to replicated volumes becomes "
            "guesswork.",
        ]),
        ('file_list', "WHAT IS ACTUALLY NEEDED", [
            "Not the data — the identity. The PVC name, its storage class, its "
            "requested and actual capacity, its access modes, and above all the CSI "
            "driver and volume handle of the PV behind it.",
            "",
            "That is enough to re-provision deliberately instead of by guess.",
        ]),
    ],
    style='negative')

d.flow(
    "Write the record where the backup will pick it up",
    "A ConfigMap is a namespace resource, so it is captured even when PVCs are excluded",
    [
        ('loop_left', "POLICY preHOOK FIRES",
         "Kasten calls the blueprint\nbefore the backup starts,\nas a policy preHook"),
        ('scan_target', "QUERY PVCs AND PVs",
         "List every PVC in the\nnamespace and follow each\none to the PV it is\nbound to"),
        ('file_text', "WRITE THE CONFIGMAP",
         "A single YAML document in\nConfigMap pvc-info-snapshot\nin the target namespace"),
        ('shield_check', "CAPTURED IN THE RESTORE POINT",
         "The ConfigMap is a namespace\nresource, not a PVC — so the\nexclusion does "
         "not apply\nto it"),
    ],
    note=("This is the whole trick.",
          "The exclusion filter applies to PVCs. By turning volume identity into an "
          "ordinary namespace object before the backup runs, the record travels inside "
          "the restore point the team already keeps — with no second backup target and "
          "nothing extra to retain."))

d.table_slide(
    "What the ConfigMap holds",
    "One key, pvc-info.yaml, containing a YAML document you can read without tooling",
    ["Field", "Why it matters"],
    [
        ("namespace, capturedAt", "Which namespace, and when the record was taken"),
        ("name, phase", "Which PVC, and whether it was actually bound at the time"),
        ("storageClass", "Which provisioner to re-provision through"),
        ("requestedStorage, capacity", "What was asked for, and what was granted"),
        ("accessModes", "Whether the volume was single- or multi-writer"),
        ("boundPV, reclaimPolicy", "The PV name, and whether deletion would have kept it"),
        ("csi.driver, csi.volumeHandle", "The one field that identifies the real cloud volume"),
    ],
    col_widths=[4.1, 8.2],
    row_heights=[0.5] + [0.62] * 7,
    body_size=11.5)

d.table_slide(
    "Versions, action and how to attach it",
    "Deploy the blueprint in kasten-io, then name it as the preHook on the policy",
    ["Component", "Version"],
    [
        ("Kubernetes", "1.32 (EKS)"),
        ("Kasten", "8.5.2"),
        ("Tools image", "kasten-tools:8.5.2"),
    ],
    col_widths=[3.1, 2.75],
    row_heights=[0.55] * 4,
    side=('loop_left', "ONE ACTION, ATTACHED AS A POLICY PREHOOK",
          "backupPrehook — a KubeTask that lists every PVC and its bound PV, formats "
          "them as YAML, and writes ConfigMap pvc-info-snapshot into the target "
          "namespace.\n"
          "It is a policy preHook, not a resource binding: set it under "
          "backupParameters.hooks.preHook on the policy, naming the blueprint and the "
          "action.\n"
          "In the Kasten UI the same setting is Policies, then Create Policy, then "
          "Advanced Options, then Kanister Actions, then Pre-Hook.\n"
          "The image is the shared kasten-tools image — kanister-tools with kubectl "
          "added — built from images/kasten-tools/Dockerfile at the repository root.\n"
          "The phase is idempotent: it overwrites the ConfigMap each run, so the record "
          "always matches the restore point it travels in."))

d.ending("THE RECORD TRAVELS WITH THE BACKUP",
         "Volume identity becomes a namespace object, so an exclusion filter cannot "
         "drop it.\ngithub.com/kastenhq/blueprint-ai — pvc-info-example")

d.save()
