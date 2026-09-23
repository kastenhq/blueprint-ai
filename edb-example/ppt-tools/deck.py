#!/usr/bin/env python3
"""Concept deck for edb-example. Content only — the slide machinery lives in the
shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py edb-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('edb', 'edb-example')

d.title("EDB POSTGRES FOR KUBERNETES\nWITH KASTEN",
        "A cold backup, driven by EDB's own Kasten add-on")

d.agenda([
    "EDB and CloudNativePG: what actually differs",
    "The Kasten add-on, and what it publishes",
    "Cold backup: why it beats pausing WAL replay",
    "Blueprint actions",
    "What this does not give you",
])

d.hero(
    "THE BLUEPRINT DOES NOT", "INVENT ITS OWN QUIESCE",
    body="EDB ships an official Kasten add-on. Enable it and the operator elects one "
         "instance, marks it,\nand publishes the exact commands a backup tool must run. "
         "The blueprint reads the marks and runs them.",
    pills=["Operator elects", "Operator publishes", "Blueprint obeys"],
    icon='handshake_agreement')

d.table_slide(
    "EDB and CloudNativePG",
    "The same operator — CloudNativePG was written by EDB, then donated to the CNCF",
    ["", "CloudNativePG", "EDB Postgres for Kubernetes"],
    [
        ("Owner", "CNCF project, Apache 2.0", "EDB, commercial subscription for production"),
        ("API group", "postgresql.cnpg.io", "postgresql.k8s.enterprisedb.io"),
        ("Label prefix", "cnpg.io/", "k8s.enterprisedb.io/"),
        ("kubectl plugin", "kubectl cnpg", "kubectl cnp"),
        ("Engine", "Community PostgreSQL",
         "Also EDB Postgres Advanced Server and EDB Postgres Extended"),
        ("Support window", "Community, about 6 months per minor",
         "Long-term support releases, 18 months"),
        ("Kasten integration", "None — you write the quiescing yourself",
         "Built in: the kasten add-on"),
    ],
    col_widths=[2.2, 4.4, 5.7],
    row_heights=[0.5, 0.62, 0.5, 0.5, 0.5, 0.72, 0.62, 0.62],
    body_size=10.5)

d.cards(
    "Two consequences when you protect these clusters",
    "The first is trivial to fix; the second changes what the blueprint should do",
    [
        ('loop_left', "A CNPG BLUEPRINT BINDS TO NOTHING", [
            "Every API group and every label prefix differs, so the cnpg-example "
            "blueprint matches no resource at all on an EDB cluster.",
            "",
            "That part is a simple rename — and it is not the interesting difference.",
        ]),
        ('checkbox_multiple', "THE RIGHT MECHANISM DIFFERS TOO", [
            "On CloudNativePG, cnpg-example pauses WAL replay on a replica. The replica "
            "keeps running, so Kasten snapshots a live data directory and PostgreSQL "
            "must crash-recover from it at restore time.",
            "",
            "On EDB the add-on stops PostgreSQL on the elected instance outright, so "
            "what Kasten captures is a cleanly shut-down data directory. EDB calls this "
            "a cold backup, and it is the only mode EDB supports for external snapshot "
            "tools.",
        ]),
    ])

d.hero(
    "THE BLUEPRINT PROVES IT", "IN ITS OWN LOG",
    body="Database cluster state: shut down in recovery\n\n"
         "That line from pg_controldata is the evidence that the fence really took "
         "effect — not merely that a command returned zero.",
    pills=["Verified, not assumed"],
    icon='checkbox_multiple')

d.table_slide(
    "What the add-on marks, and what it means",
    "The blueprint reads these labels and annotations rather than hard-coding any names",
    ["Where", "Mark", "Meaning"],
    [
        ("Elected pod", "label kasten-enterprisedb.io/hasHooks: \"true\"",
         "Run the hooks in this pod"),
        ("Elected pod",
         "annotations pre-backup-command, post-backup-command, pre-backup-on-error, "
         "and the matching *-container keys",
         "The commands to run, and in which container"),
        ("Elected instance PVCs", "label kasten-enterprisedb.io/elected: \"true\"",
         "Snapshot these — PG_DATA and PG_WAL"),
        ("Every other pod and PVC", "label kasten-enterprisedb.io/excluded: \"true\"",
         "Do not snapshot these"),
    ],
    col_widths=[2.5, 5.5, 4.3],
    row_heights=[0.5, 0.62, 1.0, 0.72, 0.62],
    body_size=10.5,
    cards=[
        ('error_warning', "IT REFUSES TO RUN IF THE ADD-ON IS OFF", [
            "With the add-on disabled there is no elected instance and nothing gets "
            "fenced — so a backup would silently capture a live data directory while "
            "reporting success. The prehook fails loudly instead, and tells you the "
            "annotation to add."]),
        ('search_eye', "CHECK THE SPELLING FIRST", [
            "If backupInstance is empty the add-on did not run. The usual cause is the "
            "annotation being misspelled: it has to match exactly, and a near-miss "
            "produces no error from the operator — just an unprotected cluster."]),
    ])

d.table_slide(
    "Versions and blueprint actions",
    "Three actions, all reading the operator's own marks",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "Finds the elected instance, runs the operator's pre-backup command to fence "
         "it, checks the fence really took effect — the Cluster lists the instance and "
         "postmaster.pid is gone — logs the pg_controldata cluster state, then runs "
         "sync in that pod. Fails the backup if the add-on is not enabled."),
        ("backupPosthook",
         "Runs the operator's post-backup command to unfence the instance, waits up to "
         "a minute for the operator to drop it from the fence list, and removes it by "
         "hand if it is still there."),
        ("restorePosthook",
         "Removes the fencing annotation from the restored Cluster if it carries one, "
         "then waits for the cluster to report a ready instance."),
    ],
    col_widths=[2.7, 9.6],
    row_heights=[0.5, 1.5, 1.05, 0.8],
    body_size=10.5,
    cards=[
        ('file_list', "TESTED VERSIONS", [
            "Kubernetes 1.31.6 on OpenShift 4.18.6, Kasten 9.0.5, EDB operator CSV "
            "cloud-native-postgresql.v1.25.9 (certified), operator image actually run "
            "quay.io/enterprisedb/cloud-native-postgresql:1.25.4, PostgreSQL 16.10, "
            "tool image kasten-tools:9.0.5, storage class managed-csi on Azure Disk CSI."]),
        ('close_circle', "NO POINT-IN-TIME RECOVERY", [
            "You restore to a restore point, and nothing finer. The add-on captures no "
            "WAL archive, so your recovery point objective is simply your policy "
            "frequency. If you need PITR, you need an archive — see cnpg-barman-example "
            "for that shape of design. The primary is unaffected throughout the backup."]),
    ])

d.hero(
    "YOU MAY ALREADY RUN EDB", "WITHOUT KNOWING IT",
    body="IBM Cloud Pak for Data installs this operator itself, repackaged, for its "
         "internal metadata databases.\nThose clusters use a different add-on wired to "
         "IBM's own labels — do not point this blueprint at them.",
    pills=["Covered by cp4d-example instead"],
    icon='error_warning', polarity='neg')

d.ending("LET THE OPERATOR DECIDE, AND VERIFY THAT IT DID",
         "EDB elects and publishes. The blueprint runs the commands and checks the "
         "result.\ngithub.com/kastenhq/blueprint-ai — edb-example")

d.save()
