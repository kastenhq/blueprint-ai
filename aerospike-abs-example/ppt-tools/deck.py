#!/usr/bin/env python3
"""Concept deck for aerospike-abs-example. Content only — the slide machinery
lives in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py aerospike-abs-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('aerospike-abs', 'aerospike-abs-example')

d.title("AEROSPIKE WITH KASTEN:\nPARTITIONED LOGICAL BACKUP",
        "absctl writes incremental dumps onto one keeper PVC per shard")

d.agenda([
    "Why snapshotting the Aerospike volumes is not an option",
    "The pattern: keeper PVCs and absctl",
    "Incremental chains — measured, not assumed",
    "Partition shards — measured, and where they stop helping",
    "Blueprint actions",
])

d.hero(
    "ON THE CLUSTERS THAT MOST", "NEED A BACKUP, THERE IS NOTHING TO SNAPSHOT",
    body="Aerospike is chosen for latency, so operators put namespace data on local "
         "NVMe attached directly\nto the worker node. Instance store has no snapshot "
         "API and no CSI driver offering VolumeSnapshot.",
    pills=["Not unreliable — unavailable", "Losing the node loses the data"],
    icon='error_warning', polarity='neg')

d.cards(
    "Why a logical backup is the only protection here",
    "This reasoning comes from Aerospike's own solution architects",
    [
        ('close_circle', "LOCAL NVME CANNOT BE SNAPSHOTTED", [
            "Network-attached storage adds latency and caps IOPS, so the deployments "
            "that need protection most are exactly the ones on instance store.",
            "",
            "There is no volume to re-attach elsewhere either: lose the node and the "
            "data is gone. The target deployment for this blueprint is roughly 40 TB "
            "across 30 nodes, all on local NVMe.",
        ]),
        ('scan_target', "DO NOT GO BY THE DEVICE NAME", [
            "On AWS Nitro instances, network-attached EBS volumes also appear as "
            "/dev/nvme* devices — and those can be snapshotted.",
            "",
            "What cannot is instance store: physically attached, ephemeral, gone when "
            "the instance goes. Check whether the device is backed by a PVC with a "
            "snapshot-capable CSI driver, not what it is called.",
        ]),
        ('error_warning', "SO KEEP THE KEEPER PVC ELSEWHERE", [
            "If the database is on local NVMe, the keeper PVC must not be.",
            "",
            "Put it on network-attached, snapshot-capable storage — otherwise you have "
            "recreated the original problem: a dump Kasten cannot snapshot, which dies "
            "with its node.",
        ]),
    ],
    style='negative')

d.split_dark(
    "Even where the volumes can be snapshotted",
    "It is still\n", "the wrong choice.",
    [
        ("01", "NO CROSS-VOLUME CONSISTENCY",
         "Aerospike stores namespace data on raw block devices, one PVC per pod, and "
         "each node holds only its own subset of the 4096 partitions. A single PVC "
         "snapshot is crash-consistent within that volume — which is not the same as a "
         "consistent cluster."),
        ("02", "CRASH-CONSISTENT STILL LOSES WRITES",
         "Acknowledged writes sit in an 8 MiB streaming write buffer before they reach "
         "the device. Crash-consistent does not mean no acknowledged write was lost, "
         "which is the guarantee people assume they are getting."),
    ],
    left_body="The test cluster used here is EBS-backed, so its volumes are "
              "snapshot-capable — and the blueprint still does a logical dump. The "
              "storage capability is not the deciding factor; the partition layout is.")

d.flow(
    "Pattern 4 — one permanent keeper PVC per shard",
    "A keeper StatefulSet holds one PVC per replica; the shards run concurrently",
    [
        ('node_tree', "SHARD THE PARTITIONS",
         "Each StatefulSet replica owns\na fixed slice of the 4096\npartitions. "
         "replicas: 1 is the\nsimple case"),
        ('upload_cloud', "absctl WRITES THE DUMP",
         "A full dump, or an\nincremental against the\ncurrent chain, per Aerospike\n"
         "namespace"),
        ('loop_left', "SYNC, THEN VERIFY THE BYTES",
         "Every shard is synced, then\nabsctl's reported bytes are\nchecked against "
         "what the\ndevice actually holds"),
        ('shield_check', "KASTEN SNAPSHOTS THE KEEPER PVCs",
         "The permanent PVCs exist at\ndiscovery time, so they are\ncaptured correctly"),
    ],
    note=("Asserting the flush, not assuming it.",
          "A CSI snapshot captures the block device, and data the writer has "
          "acknowledged but the kernel has not flushed sits in the page cache. The "
          "result is not a clean failure — the file appears with the right name and "
          "zero bytes. This blueprint therefore aborts if a shard reports written bytes "
          "while its device still holds none."))

d.table_slide(
    "Incremental chains — measured",
    "An incremental export costs about 69 times less than a full one",
    ["Run", "Kind", "Exported", "Chain member"],
    [
        ("1", "full", "23,526,001 B (22.4 MiB)", "full-102609Z"),
        ("2+", "incremental", "roughly 69x less", "later members of the same chain"),
    ],
    col_widths=[1.3, 2.2, 4.0, 4.8],
    row_heights=[0.5, 0.6, 0.6],
    cards=[
        ('bar_chart', "WHY A FULL DUMP SHIPS EVERYTHING", [
            "absctl output is not byte-stable, so a fresh full dump deduplicates "
            "poorly against the previous one even when the data barely changed. Kopia "
            "cannot recognise the overlap. That is what makes the chain worth the "
            "complexity — and why the number had to be measured rather than assumed."]),
        ('checkbox_multiple', "THE CONTROL IS A BASELINE", [
            "Writing 50 MiB of /dev/urandom to the keeper PVC grew the repository by "
            "52,443,164 bytes — one to one. That confirms the deduplication measurement "
            "is real and not an artefact of the test: incompressible, unique data "
            "shows no saving at all."]),
    ])

d.table_slide(
    "Partition shards — measured",
    "2.37x at four shards, not 4x — and the reason matters more than the number",
    ["Shards", "Parallel per shard", "Records", "Wall clock", "Speed-up"],
    [
        ("1", "1", "2,400,010", "97.9 s", "baseline"),
        ("4", "1", "2,400,010", "41.3 s", "2.37x"),
    ],
    col_widths=[1.4, 2.6, 2.4, 2.5, 3.4],
    row_heights=[0.5, 0.6, 0.6],
    cards=[
        ('speed_up', "WHY NOT 4x", [
            "Four shards approach the new ceiling too. Aerospike keeps its index in RAM "
            "but record data on the device, so a scan issues roughly one device read per "
            "record; a gp3 volume serves a fixed number of operations per second and "
            "queues the rest. Sharding helps only once the database's storage is no "
            "longer the bottleneck."]),
        ('scan_target', "SHARDS READ FROM EVERY NODE", [
            "Partition assignment is pseudo-random rather than contiguous, so every "
            "shard reads from every node. That is a feature — it spreads the load "
            "instead of concentrating a shard's reads on one node. Note gp3 caps at 500 "
            "IOPS per GiB, so 16,000 IOPS needs a volume of at least 32 GiB."]),
    ])

d.table_slide(
    "Versions and blueprint actions",
    "Both settings are environment variables on the keeper, and both were measured",
    ["Action", "Phase", "What it does"],
    [
        ("backupPrehook", "absctlBackup",
         "Reads replicas from the StatefulSet and derives each shard's partition range. "
         "Discovers the Aerospike pod, the cluster name and the namespace list. Detects "
         "a replica-count change through the .partition marker and, if found, wipes "
         "every shard volume and forces a full. Launches all shards concurrently, "
         "writes the marker, syncs every shard, verifies the reported bytes landed, and "
         "prunes superseded chain members after a full."),
        ("restorePosthook", "absctlRestore",
         "Waits for every restored keeper pod, then validates that the shard ranges "
         "tile 0-4095 exactly once — aborting if the restore point was taken under a "
         "different replica count — resolves the chain and rejects zero-length .asb "
         "files. All of that happens before any truncate. Then per namespace: "
         "truncate-namespace, then replay each chain member oldest first, all shards "
         "in parallel within a member."),
        ("delete", "absctlDelete",
         "Logs the artifact values. There is nothing external to delete."),
    ],
    col_widths=[2.2, 1.9, 8.2],
    row_heights=[0.5, 1.9, 1.9, 0.55],
    body_size=10)

d.ending("MEASURE THE CLAIM BEFORE YOU WRITE IT DOWN",
         "69x on export, 2.37x on wall clock — both numbers came from running it.\n"
         "github.com/kastenhq/blueprint-ai — aerospike-abs-example")

d.save()
