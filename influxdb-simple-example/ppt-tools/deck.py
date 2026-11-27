#!/usr/bin/env python3
"""Concept deck for influxdb-simple-example. Content only — the slide machinery lives in
the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py influxdb-simple-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('influxdb-simple', 'influxdb-simple-example')

d.title("INFLUXDB WITH KASTEN",
        "Stop, snapshot, start: a clean copy of the volume for InfluxDB 1.x and 3 Core")

d.agenda([
    "Why InfluxDB has to be stopped",
    "The pattern, and the patterns ruled out",
    "Architecture and blueprint actions",
    "The watchdog, InfluxDB 3, restore and export cost",
])

d.hero(
    "NO COMMAND TO PAUSE WRITES,", "SO STOP THE PROCESS",
    body="InfluxDB 1.x OSS and 3 Core have no read lock and no flush command.\n"
         "The only state on disk that nothing is changing is the state of a stopped "
         "InfluxDB.",
    pills=["Single replica", "Plain Deployment, no operator", "One RWO PVC"],
    icon='database_table', polarity='neg')

d.cards(
    "Three facts decide the pattern",
    "Each one was checked on the test cluster",
    [
        ('close_circle', "NO REPLICA, NO QUIESCE", [
            "OSS InfluxDB 1.x has no replication and no command that pauses writes. "
            "Patterns 1 and 2 with a database primitive are not possible.",
        ]),
        ('time_clock', "A STOP COSTS 20-35 SECONDS", [
            "Measured on Ceph RBD: about 20 s on 1.11, 24-35 s on 3 Core (WAL replay). Writers "
            "that retry or poll on a schedule catch up.",
        ]),
        ('history_clock', "RECENT WRITES LIVE IN THE WAL", [
            "After a clean stop, the newest points were still only in wal/. A whole-PVC "
            "snapshot includes it, and InfluxDB replays it at start.",
        ]),
    ])

d.table_slide(
    "Pattern 2 — and the patterns ruled out",
    "Ranked order from AGENTS.md; the first one that is technically possible wins",
    ["Pattern", "Why not"],
    [
        ("1. Fence a replica", "InfluxDB 1.x OSS has no replication."),
        ("2. Quiesce (stop the process)", "Chosen. Clean copy, no restore script."),
        ("3. influxd backup to a PVC on the pod",
         "No downtime, but changes the workload and needs a restore script. Next blueprint."),
        ("4. Keeper running influxd backup",
         "Backup port 8088 has no authentication; opening it weakens security."),
        ("6. Staging PVC, late capture", "Needs Kasten 9.0.11; the cluster runs 9.0.8."),
    ],
    col_widths=[4.2, 8.1])

d.flow(
    "Architecture",
    "The blueprint moves no data: it only stops and starts the Deployment",
    [
        ('server_hardware', "BACKUPPREHOOK",
         "Save replicas in an annotation,\ncreate the watchdog Job,\nscale to 0, wait for the pod"),
        ('database_storage', "STOPPED INFLUXDB",
         "Pod deleted only after its\nvolume is unmounted:\nall writes are on the device"),
        ('shield_check', "KASTEN SNAPSHOT + EXPORT",
         "CSI snapshot of the PVC,\nexport of the restore point\nto object storage"),
        ('loop_left_refresh', "BACKUPPOSTHOOK",
         "Scale back to the saved\ncount, wait until ready,\ndelete the watchdog"),
    ],
    note=("Why a label opts the Deployment in.",
          "This blueprint stops what it is bound to. The BlueprintBinding only matches "
          "Deployments labelled blueprint-ai/influxdb-simple=true, so it can never stop a "
          "Deployment by accident."))

d.table_slide(
    "Blueprint actions",
    "Scripts live in the ConfigMap influxdb-simple-scripts, not in the image",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Save the replica count in blueprint-ai/influxdb-replicas, start the watchdog "
         "Job, scale to 0, wait until the pods are deleted. Fail if one is left."),
        ("backupPosthook",
         "Scale back to the saved count, wait until ready, remove the annotation, "
         "delete the watchdog."),
        ("restorePosthook",
         "Same script: scale up if the restored Deployment carries the annotation, "
         "then wait until InfluxDB is ready."),
    ],
    col_widths=[2.6, 9.7])

d.hero(
    "A CANCELLED BACKUP", "MUST NOT LEAVE INFLUXDB DOWN",
    body="Tested on Kasten 9.0.8: after a cancel, backupPosthook did not run, or was "
         "stopped before it finished.\nA watchdog Job starts InfluxDB again after 10 "
         "minutes if the posthook has not.",
    pills=["Created before the stop", "Deleted by the posthook", "Delay per Deployment"],
    icon='shield_keyhole', polarity='neg')

d.table_slide(
    "InfluxDB 3 Core: why sync alone is not enough",
    "Tested under constant writes, with frequent Parquet persistence, on Ceph RBD",
    ["Variant", "Result of the restored copy"],
    [
        ("sync in the pod, no stop",
         "7 of 12 started and were consistent. 5 of 12 did not start: an empty snapshot "
         "file written between the sync and the snapshot."),
        ("Stop, snapshot, start",
         "6 of 6 started, sequence contiguous, every acknowledged write present. "
         "Downtime 24-35 s."),
        ("Same blueprint as 1.x",
         "The scripts only scale a Deployment: one installation serves 1.x and 3 Core."),
    ],
    col_widths=[3.4, 8.9])

d.two_dark(
    "WHAT A RESTORE DOES", "Nothing to run by hand before or after",
    [
        ("01", "KASTEN", "Volume and objects", "Scales the Deployment down, restores the PVC "
         "from the snapshot or the export, and brings the Deployment, Service and Secret "
         "back."),
        ("02", "RESTOREPOSTHOOK", "Make sure it runs", "Scales up if the Deployment was "
         "captured at 0, then waits for readiness. Tested in place and after deleting the "
         "namespace."),
    ])

d.cards(
    "What to know before you rely on it",
    "Limits of a stop-and-snapshot backup",
    [
        ('error_warning', "WRITES DURING THE STOP", [
            "Clients get connection errors for 20-35 s. A writer that neither retries "
            "nor polls loses those points.",
        ]),
        ('database_table', "WHOLE INSTANCE ONLY", [
            "The restore brings back every database together. For a single-database "
            "restore, use influxd backup (pattern 3).",
        ]),
        ('history_clock', "EXPORT COST", [
            "Old files are exported once. A 1% append exported about 5% at each of two backups on 1.11, and under 1% on 3 Core, which did not rewrite its Parquet files.",
        ]),
    ], style='negative')

d.ending("A CLEAN COPY WITH NO RESTORE SCRIPT",
         "Stop InfluxDB, let Kasten snapshot the volume, start it again.\n"
         "github.com/kastenhq/blueprint-ai — influxdb-simple-example")

d.save()
