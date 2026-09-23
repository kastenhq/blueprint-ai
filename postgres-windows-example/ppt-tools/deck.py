#!/usr/bin/env python3
"""Concept deck for postgres-windows-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py postgres-windows-example     # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('postgres-windows', 'postgres-windows-example')

d.title("POSTGRESQL IN A WINDOWS VM\nON OPENSHIFT VIRTUALIZATION",
        "Quiesce only — Kasten snapshots the VM disk and owns the filesystem freeze")

d.agenda([
    "The workload, and why it constrains the pattern",
    "Quiesce: what the blueprint does and does not do",
    "Driving a Windows guest from a blueprint",
    "Blueprint actions",
    "Restore, and what is still manual",
])

d.hero(
    "ONE DISK, ONE VM,", "NO SECOND COPY",
    body="The database lives on the single Windows OS disk — one DataVolume. There is "
         "no replica\nand no separate mountable data PVC, so most patterns simply do "
         "not apply here.",
    pills=["One DataVolume", "No replica", "No extra PVC"],
    icon='macbook_laptop')

d.cards(
    "Why quiesce, and why not the alternatives",
    "The workload's shape rules out the higher-ranked patterns before preference enters",
    [
        ('checkbox_multiple', "QUIESCE — CHOSEN", [
            "Kasten snapshots the VM's DataVolume PVC and owns the guest-filesystem "
            "freeze and thaw.",
            "",
            "Block-level CSI snapshots, so it stays incremental, and Kasten remains the "
            "data mover. It is the highest-ranked pattern that is feasible here.",
        ]),
        ('close_circle', "FENCE A REPLICA — NOT POSSIBLE", [
            "There is no replica to fence. A single VM holds the only copy of the "
            "database.",
            "",
            "The same reasoning rules out the keeper-PVC patterns: there is no second "
            "mountable volume to write a dump to.",
        ]),
        ('close_circle', "LOGICAL DUMP — REJECTED", [
            "A pg_dump onto the OS disk would bloat the single disk the VM boots from, "
            "and a fresh dump every run is not incremental.",
            "",
            "Kasten would then be snapshotting a disk that grows with every backup.",
        ]),
    ],
    style='standard',
    note=("What the blueprint actually contributes.",
          "Only a Postgres-aware CHECKPOINT in backupPrehook, so the captured disk is "
          "clean and fast to recover. Postgres replays its write-ahead log as ordinary "
          "crash recovery when the restored VM boots. The blueprint copies nothing."))

d.flow(
    "Pattern 2 — quiesce",
    "The blueprint issues a CHECKPOINT; every other step belongs to Kasten",
    [
        ('database_storage', "backupPrehook: CHECKPOINT",
         "A Postgres CHECKPOINT inside\nthe guest flushes dirty pages\nto the disk "
         "Kasten is about\nto capture"),
        ('lock_security', "KASTEN FREEZES THE GUEST FS",
         "Kasten, not the blueprint,\nowns the guest-filesystem\nfreeze through the "
         "VM's\nguest agent"),
        ('shield_check', "SNAPSHOT THE VM PVC",
         "A CSI snapshot of the single\nDataVolume — block level,\nso it stays "
         "incremental"),
        ('loop_left', "KASTEN THAWS",
         "The guest filesystem is\nreleased. On restore,\nPostgres performs normal\n"
         "crash recovery on boot"),
    ],
    note=("Why the CHECKPOINT still matters when Kasten freezes the filesystem.",
          "A frozen filesystem gives a crash-consistent disk, which Postgres can always "
          "recover — but recovery has to replay whatever WAL had accumulated. The "
          "CHECKPOINT shortens that replay, so the restored VM comes up quickly instead "
          "of grinding through a long recovery."))

d.split_dark(
    "Driving a Windows guest",
    "No guest-exec API.\n", "So go through libvirt.",
    [
        ("01", "VIA THE VIRT-LAUNCHER POD",
         "KubeVirt exposes no direct guest-exec API. The blueprint reaches the guest by "
         "running virsh qemu-agent-command in the VM's virt-launcher pod, in the "
         "compute container."),
        ("02", "WITH NO EXTRA RBAC",
         "The KubeTask pods run in kasten-io as the kanister-svc service account, which "
         "already holds get pods and pods/exec in the application namespace. Nothing "
         "further to grant."),
    ],
    left_body="The QEMU guest agent must be connected before any of this works — check "
              "the AgentConnected condition on the VMI. Postgres must also be reachable "
              "on 127.0.0.1:5432 inside the guest, with a known superuser password "
              "using scram-sha-256 authentication.")

d.cards(
    "Two details worth copying",
    "Both are the kind of thing that only shows up once it has already bitten you",
    [
        ('lock_password', "THE PASSWORD IS NOT PASSED AS ENV", [
            "The Postgres password is read from a Kubernetes Secret and embedded in the "
            "PowerShell command as a single-quoted literal.",
            "",
            "It is deliberately not passed through the guest-exec env array: doing that "
            "wipes the guest PATH, and the command then fails for a reason that looks "
            "nothing like its cause. The password is never echoed to logs.",
        ]),
        ('function_add', "ONE WORKER SCRIPT, TWO PHASES", [
            "Both actions share a single worker script through a YAML anchor, "
            "dispatched by a per-phase argument — checkpoint or verify-restore.",
            "",
            "The guest-exec logic therefore exists in exactly one place, so a fix to "
            "the awkward part of the blueprint cannot be applied to one phase and "
            "forgotten in the other.",
        ]),
    ])

d.table_slide(
    "Versions and blueprint actions",
    "The blueprint is bound either by annotation on a single VM, or by label for a fleet",
    ["Action", "Function", "What it does"],
    [
        ("backupPrehook", "KubeTask (guest-exec)",
         "Issues a Postgres CHECKPOINT inside the Windows guest through the QEMU guest "
         "agent, so the disk Kasten is about to snapshot is clean and fast to recover."),
        ("restorePosthook", "KubeTask (guest-exec)",
         "Verifies the restored guest: the Postgres service is running and the expected "
         "rows are readable. Depends on Kasten triggering restore hooks for this "
         "resource type."),
    ],
    col_widths=[2.5, 2.6, 7.2],
    row_heights=[0.5, 1.2, 1.2],
    cards=[
        ('instance_hexagon', "TOOLS IMAGE", [
            "The KubeTask phases need kubectl and jq, plus kando from the "
            "kanister-tools base. The blueprint uses the shared kasten-tools image, "
            "built from images/kasten-tools/Dockerfile at the repository root."]),
        ('error_warning', "NO restorePrehook — BY DESIGN", [
            "For this quiesce pattern the restore replaces the VM DataVolume and "
            "Postgres performs crash recovery on boot, so there is nothing to prepare "
            "beforehand. On Kasten 8.5.x and earlier, verify the restore by hand once "
            "the guest agent reconnects."]),
    ])

d.ending("THE BLUEPRINT ONLY MAKES THE DISK CLEAN",
         "Kasten snapshots the DataVolume and owns the freeze. Postgres does the rest "
         "on boot.\ngithub.com/kastenhq/blueprint-ai — postgres-windows-example")

d.save()
