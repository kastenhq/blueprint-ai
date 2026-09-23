#!/usr/bin/env python3
"""Concept deck for rhoso-galera-example. Content only — the slide machinery lives
in the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py rhoso-galera-example
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('rhoso-galera', 'rhoso-galera-example')

d.title("RHOSO GALERA WITH KASTEN",
        "A logical dump onto a keeper PVC — the documented runbook, run in-cluster")

d.agenda([
    "Status: not yet validated",
    "Why a logical dump and not a volume snapshot",
    "The pattern and where credentials stay",
    "Blueprint actions",
    "Restore",
])

d.hero(
    "THIS BLUEPRINT HAS", "NOT BEEN VALIDATED",
    body="It was built from the Red Hat KCS runbook for backing up RHOSO Galera "
         "clusters and from the\nmariadb-operator source, with no access to a live "
         "RHOSO environment.",
    pills=["No live environment", "Steps 2, 3 and 5 not done", "A starting point"],
    icon='error_warning', polarity='neg')

d.cards(
    "What that means in practice",
    "Treat it as a draft to walk through and confirm, not as something to deploy",
    [
        ('search_eye', "EVERY COMMAND NEEDS CONFIRMING", [
            "Each command marked \"validate\" in the README must be confirmed against "
            "a real cluster before any production use.",
            "",
            "The layout facts the blueprint relies on were read from the "
            "openstack-k8s-operators mariadb-operator source, not observed running.",
        ]),
        ('close_circle', "THE WORKFLOW IS INCOMPLETE", [
            "Steps 2, 3 and 5 of the standard blueprint workflow — deploy the workload, "
            "validate the flow without a blueprint, and test end to end through Kasten "
            "— have not been performed.",
            "",
            "Step 3 is the one that usually catches wrong commands before any YAML is "
            "written.",
        ]),
        ('checkbox_multiple', "WHAT IS SOLID", [
            "The pattern choice and the reasoning behind it, which follow from Red "
            "Hat's documented method rather than from guesswork.",
            "",
            "The design mirrors the runbook exactly: the keeper is simply \"the "
            "workstation, but in-cluster with a persistent PVC\".",
        ]),
    ],
    style='negative')

d.hero(
    "A RAW GALERA DATADIR", "IS NOT A SUPPORTED RESTORE PATH",
    body="A Galera datadir is synchronously replicated and carries cluster state — "
         "gcache, grastate.dat, GTID.\nRed Hat's supported backup is a logical dump, "
         "not a volume snapshot of /var/lib/mysql.",
    pills=["mysqldump --single-transaction", "From a pod not serving traffic"],
    icon='database_storage', polarity='neg')

d.table_slide(
    "Patterns considered and ruled out",
    "Almost everything is excluded by the operator owning the StatefulSet, or by Galera itself",
    ["Pattern", "Ruled out because"],
    [
        ("1 — Fence and quiesce a replica",
         "A fenced-node datadir snapshot is not the supported Galera restore path, and "
         "the StatefulSet is operator-owned, so fencing or scaling it directly is "
         "reverted."),
        ("2 — Quiesce, crash-consistent",
         "Same reason: restoring a raw Galera datadir is unsupported. Red Hat documents "
         "a logical dump and reload."),
        ("3 — Permanent workload PVC",
         "Would need a backup volume added to the operator-owned Galera StatefulSet. "
         "The openstack-operator will not preserve arbitrary extra volumes, and the "
         "Galera PVC is the datadir being protected."),
        ("7-9 — Temporary PVC",
         "Break BlueprintBinding, because an action hook has namespace-only context, "
         "and break SRP and OCP. Strictly worse here."),
        ("11 — Vendor operator data mover",
         "The operator has a galerabackup template but no stable external-immutability "
         "backup API. The documented method is a manual mysqldump."),
    ],
    col_widths=[3.5, 8.8],
    row_heights=[0.5, 0.85, 0.7, 0.95, 0.72, 0.85],
    body_size=10.5)

d.flow(
    "Pattern 4 — dump on a permanent keeper PVC",
    "One keeper Deployment per Galera cluster, bound by label to preserve SRP and OCP",
    [
        ('scan_target', "FIND AN IDLE POD",
         "The keeper picks a galera pod\nnot serving traffic, using the\nactive-service "
         "selector and\nwsrep_incoming_addresses"),
        ('database_storage', "DUMP INSIDE THAT POD",
         "mysqldump\n--single-transaction over\nevery non-system InnoDB\nschema"),
        ('file_text', "ONE FILE ON THE KEEPER PVC",
         "/backup/<galera>-databases.sql,\noverwritten each run, then\nsync"),
        ('shield_check', "KASTEN SNAPSHOTS THE KEEPER PVC",
         "The permanent PVC already\nexists at discovery time, so\nit is captured "
         "correctly"),
    ],
    note=("Where the credentials stay — and why that matters.",
          "The dump and the reload both run inside the Galera pod, using that pod's own "
          "DB_ROOT_PASSWORD and TLS context. No credential is ever copied to the keeper "
          "or into Kanister logs, and no remote root grant or TLS client certificate is "
          "required anywhere."))

d.table_slide(
    "Versions and blueprint actions",
    "Detect the Kasten version from your own cluster rather than trusting the table",
    ["Action", "What it does"],
    [
        ("backupPrehook",
         "KubeExec into the keeper: find a Galera pod not serving traffic, mysqldump "
         "every non-system InnoDB schema into /backup/<galera>-databases.sql, "
         "overwriting it, then sync."),
        ("backupPosthook",
         "KubeExec into the keeper: verify /backup/<galera>-databases.sql exists and is "
         "not empty."),
        ("restorePosthook",
         "KubeExec into the keeper, in three phases: idempotently ensure the cluster is "
         "scaled to one with traffic disabled; drop the existing OpenStack schemas and "
         "reload the dump into <galera>-galera-0; re-enable traffic, scale back to "
         "three, then remind the operator to rotate credentials."),
    ],
    col_widths=[2.8, 9.5],
    row_heights=[0.5, 1.1, 0.65, 1.6],
    body_size=10.5,
    cards=[
        ('file_list', "EXPECTED VERSIONS — VALIDATE THESE", [
            "OpenShift 4.16 or 4.18, RHOSO 18.0 FR1, the mariadb-operator bundled in "
            "RHOSO 18, MariaDB 10.x or 11.x per the RHOSO openstack-mariadb image, and "
            "registry.redhat.io/openshift4/ose-cli as the keeper image."]),
        ('lock_password', "ROTATE CREDENTIALS AFTER A RELOAD", [
            "The reload replaces the OpenStack schemas, which include the service "
            "account rows. The restorePosthook ends by reminding the operator to rotate "
            "credentials — a reloaded dump can otherwise leave services holding "
            "passwords the database no longer agrees with."]),
    ])

d.ending("THE RUNBOOK, BUT IN-CLUSTER AND ON A PVC",
         "Validate it against a live RHOSO environment before trusting it.\n"
         "github.com/kastenhq/blueprint-ai — rhoso-galera-example")

d.save()
