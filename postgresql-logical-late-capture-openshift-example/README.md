# Kasten Blueprint — Logical PostgreSQL Backup on a Staging PVC (Late Capture), OpenShift

> ⚠️ **Kasten `9.0.10` or later, with the `lateCapture` feature flag on.**
>
> ```bash
> # Kasten installed by the OpenShift operator (OLM): set the flag in the K10 custom resource
> kubectl patch k10 k10 -n kasten-io --type merge -p '{"spec":{"features":{"lateCapture":true}}}'
> # Kasten installed with Helm
> helm upgrade k10 kasten/k10 -n kasten-io --version 9.0.10 --reuse-values --set features.lateCapture=true
>
> kubectl get cm k10-features -n kasten-io -o jsonpath='{.data.lateCapture}'   # must print: true
> ```
>
> **Without the flag, the backup succeeds and holds no dump.** Kasten only logs the warning
> *"Late-capture declarations present but the lateCapture feature flag is not set; ignoring
> them"*. The staging PVC is created, never snapshotted, then deleted by `backupPosthook`. Check
> the flag before you rely on this blueprint. If Kasten is deployed by a GitOps tool (Argo CD, for
> example), change the version and the flag in its source, not on the cluster: the tool would undo
> the change, and undoing a version change is a Kasten downgrade.

A logical backup (`pg_dumpall` / `psql`) of **PostgreSQL deployed on OpenShift with the
`postgresql-ephemeral` template** (a `DeploymentConfig`). It is the late-capture version of the
Kasten documentation example
[Logical PostgreSQL Backup on OpenShift Clusters](https://docs.kasten.io/latest/kanister/postgresql/install_logical_os).
The Kubernetes version is
[postgresql-logical-late-capture-example](../postgresql-logical-late-capture-example/), and
[mysql-logical-late-capture-openshift-example](../mysql-logical-late-capture-openshift-example/)
is the MySQL version of this page, with more detail on the OpenShift specifics.

## What changes compared with the Kasten documentation example

The documentation blueprint defines `backup`, `restore` and `delete` actions. It streams the dump
to the location profile with `kando location push`: **the blueprint is the data mover**, which
[AGENTS.md](../AGENTS.md#core-principle) forbids. This blueprint writes the dump to a **staging
PVC** instead, and **Kasten is the data mover**:

| | Documentation example | This blueprint |
|---|---|---|
| Where the dump goes | `kando location push` to object storage | A staging PVC that Kasten snapshots |
| Actions | `backup`, `restore`, `delete` | `backupPrehook`, `backupPosthook`, `restorePosthook` |
| Images | `kanister-tools` + `postgres:13-bookworm` from Docker Hub in a `MultiContainerRun` | The server's own image, read from the `DeploymentConfig` (`PrepareData`) |
| PostgreSQL version | `10-el8` (the template default, end of life since 2022) | `15-el9` (`-p POSTGRESQL_VERSION=15-el9`) |
| Password | Rendered into the pod command | Read from the Secret through `podOverride` env (`PGPASSWORD`) |
| Dump | `pg_dumpall --clean` | `pg_dumpall --clean --if-exists`, compressed with `gzip` |
| Errors during the replay | Ignored (`psql` exits 0) | Two expected errors accepted, any other fails the restore |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding on `DeploymentConfigs` from the PostgreSQL templates (annotation still possible) |

**The admin password Secret.** The template's Secret holds the application user's credentials,
not the `postgres` password. As in the documentation example, create a second Secret
`<dc>-<namespace>` with the key `postgresql_admin_password` (Step 2). The blueprint reads it.

**Errors during the replay.** A `--clean` dump drops and recreates every role, including
`postgres`, which the replay session itself uses. PostgreSQL refuses both statements
(*"current user cannot be dropped"* and *"role "postgres" already exists"*), and `psql` continues.
These two errors are harmless. `psql` exits `0` even when other statements fail, so the blueprint
reads `psql`'s error output and fails on any other error.

**The image comes from the workload.** The blueprint reads the image of the `DeploymentConfig`'s
container, which the image trigger resolves to a digest. `pg_dumpall` and `psql` always match the
server: `pg_dumpall` refuses to dump a server newer than itself.

## Pattern

**Pattern 6 — Logical dump on a staging PVC (late capture).** The requester chose this pattern:
the request was to port the documentation example to late capture.

Why it fits: a `pg_dumpall` output is a **full** dump, so it does not need the previous backup on
the volume. The blueprint is bound to the `DeploymentConfig`, which Kasten 9.0.10 treats as a
workload, so late capture and BlueprintBinding both work.

**The template is ephemeral: the dump is the only copy of the data.** `postgresql-ephemeral` keeps
the database in an `emptyDir`; every pod restart starts PostgreSQL empty, and after a restore
`restorePosthook` rebuilds every database from the dump. With the `postgresql-persistent` template
the data PVC is snapshotted as well; exclude it in the policy if the dump is meant to replace it.

**What the backup protects:** every database, every role with its password, and the grants, as of
the start of the dump, plus every Kubernetes object in the namespace.

**What it does not protect:** consistency **between** databases (they are dumped one after the
other), and point-in-time recovery (no WAL archive).

**Incrementality.** The local snapshot is a full copy each time (the staging PVC is new). The export
deduplicates by content; measured for MongoDB
([Step 6](../mongodb-logical-late-capture-example/README.md#step-6--export-deduplication)), not for
PostgreSQL.

## Versions

| Component | Version |
|---|---|
| OpenShift | `4.21.30` (bare metal, OpenShift Data Foundation) |
| Kasten | `9.0.10` (OpenShift operator), `features.lateCapture=true` |
| Template | `postgresql-ephemeral` from `openshift/origin` (`release-4.11`), `POSTGRESQL_VERSION=15-el9` |
| Database | `PostgreSQL 15.19` (`registry.redhat.io/rhel9/postgresql-15`) |

The manual validation (Step 3) was also run on OpenShift `4.18.21` on Azure (PostgreSQL `15.14`,
Azure Disk CSI). `DeploymentConfig` is deprecated since OpenShift 4.14; see the
[MySQL page](../mysql-logical-late-capture-openshift-example/README.md#versions) for what to change
for a `Deployment`.

> Detect your own Kasten version with
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`
> (operator) or `helm ls -n kasten-io` (Helm).

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, replay it on restore |
| `blueprintbinding.yaml` | Binds the blueprint to `DeploymentConfigs` created by the PostgreSQL templates |
| `policy.yaml` | On-demand policy with export |

---

## Prerequisites

- Kasten `9.0.10`+ with `features.lateCapture=true` (see the top of this page).
- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten.
- A **Location** profile for the policy (not an `Infra` profile).
- Access to `registry.redhat.io` from the cluster (the global pull secret of OpenShift has it).

> **Storage class**: `ocs-storagecluster-ceph-rbd` is the OpenShift Data Foundation RBD class used
> in our test environment. It appears **in `blueprint.yaml`** (the staging PVC spec).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on Azure, `gp3-csi` on ROSA or AWS, your custom class on bare-metal,
> and so on). The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

---

## Step 2 — Deploy the workload and create test data

The template takes its image from the `postgresql` image stream in the namespace given by
`NAMESPACE` (default: `openshift`), part of the OpenShift samples. If the Samples Operator is
`Removed`, create the image stream in the application namespace:

```bash
oc create namespace postgresql-logical

# Only if `oc get is postgresql -n openshift` finds nothing:
oc import-image postgresql:15-el9 --from=registry.redhat.io/rhel9/postgresql-15:latest --confirm -n postgresql-logical

oc new-app https://raw.githubusercontent.com/openshift/origin/release-4.11/examples/db-templates/postgresql-ephemeral-template.json \
  --namespace postgresql-logical \
  -p POSTGRESQL_VERSION=15-el9 \
  -p NAMESPACE=postgresql-logical \
  -e POSTGRESQL_ADMIN_PASSWORD=secretpassword
oc create secret generic postgresql-postgresql-logical --namespace postgresql-logical \
  --from-literal=postgresql_admin_password=secretpassword
oc rollout status dc/postgresql -n postgresql-logical
```

Create a small dataset (< 5 KB):

```bash
NS=postgresql-logical
PSQL() { oc exec -n $NS dc/postgresql -- bash -c \
  "PGPASSWORD=\$POSTGRESQL_ADMIN_PASSWORD psql -h 127.0.0.1 -U postgres $*"; }

PSQL -c "'CREATE DATABASE test'"
PSQL -d test -c "\"
CREATE TABLE company (id INT PRIMARY KEY, name TEXT NOT NULL, age INT NOT NULL, address CHAR(50), salary REAL, join_date DATE);
INSERT INTO company VALUES
 (1,'Paul',32,'California',20000.00,'2001-07-13'),
 (2,'Allen',25,'Texas',NULL,'2007-12-13'),
 (3,'Teddy',23,'Norway',20000.00,NULL),
 (4,'Mark',25,'Rich-Mond',65000.00,'2007-12-13');
SELECT * FROM company;\""
```

Expected: 4 rows.

### Removing the workload

```bash
oc delete namespace postgresql-logical
kubectl delete policy postgresql-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=postgresql-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

On the Azure cluster, with `oc` and a CSI `VolumeSnapshot` standing in for Kasten:

1. Create a PVC (`2Gi`).
2. Run a pod with the PostgreSQL image that mounts it and runs
   `pg_dumpall -h postgresql -U postgres --clean --if-exists | gzip`, then `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop the `test` database.
5. Create a PVC from the snapshot, run `zcat dump.sql.gz | psql -h postgresql -U postgres -d postgres`.
6. The 4 rows are back. `psql` reported exactly the two expected errors about the `postgres` role
   (and a `NOTICE` for the database that no longer existed), and exited `0`.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps.openshift.io/deploymentconfigs` labelled
`template=postgresql-ephemeral-template` or `template=postgresql-persistent-template`. To bind one
`DeploymentConfig` by hand instead:

```bash
oc annotate dc postgresql -n postgresql-logical \
  kanister.kasten.io/blueprint=postgresql-logical-late-capture-openshift-blueprint
```

The blueprint assumes the Service has the `DeploymentConfig`'s name, the admin Secret is
`<dc>-<namespace>` with the key `postgresql_admin_password`, and PostgreSQL is the first container.

**Security context:** the blueprint sets none, and must not pin the pod to `restricted-v2`. Kasten
runs Kanister pods as root, which `restricted-v2` rejects. See
[Security context and SCC](../mysql-logical-late-capture-openshift-example/README.md#security-context-and-scc).

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<dc>-dump`. `PrepareData` (the server's own image) mounts it, runs `pg_dumpall --clean --if-exists` through `gzip`, runs `sync`, and checks the archive with `gzip -t`. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<dc>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact), waits up to 5 minutes for `pg_isready`, replays the dump with `psql`, and fails on any error other than the two expected ones. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true` and runs with `set -euo pipefail`.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

With the ephemeral template there is no data PVC, so the policy has no filter. With the persistent
template, see [The policy](../mysql-logical-late-capture-openshift-example/README.md#the-policy) on
the MySQL page. The policy **exports** the restore point: the local snapshot is deleted with the
namespace.

---

## Step 5 — End-to-end test through Kasten

1. **Run the policy** (UI, or a `RunAction` on `postgresql-logical-backup`). During the backup the
   PVC `postgresql-dump` appears, becomes `Bound`, and is deleted. The restore point holds
   `postgresql-dump`.
2. **Delete the data:** `PSQL -c "'DROP DATABASE test'"`
3. **Restore** in place with a `RestoreAction` in `postgresql-logical` (see the
   [MySQL page](../mysql-logical-late-capture-openshift-example/README.md#step-5--end-to-end-test-through-kasten)
   for the manifest).
4. **Verify:** `PSQL -d test -c "'SELECT * FROM company'"` — expect the 4 rows.

### Restore after the namespace was deleted

Restore from the **exported** restore point, as described on the
[MySQL page](../mysql-logical-late-capture-openshift-example/README.md#restore-after-the-namespace-was-deleted):
create the namespace, a `RestorePoint` from the exported `RestorePointContent`, then the
`RestoreAction`. Kasten restores the `DeploymentConfig`, both Secrets, the Service and the image
stream; PostgreSQL starts empty and `restorePosthook` loads the dump.

Tested on OpenShift 4.21 with Kasten 9.0.10 (operator):
- the in-place restore after dropping `test` brought back the 4 rows;
- the restore after deleting the namespace, from the exported restore point, brought back the 4
  rows into a new, empty PostgreSQL pod;
- a new backup after that restore completed;
- the staging PVC was deleted after every backup and every restore.

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n postgresql-logical -o jsonpath='{.items[-1:].status.error}'
```

**The restore fails with "Unexpected errors while replaying the dump".** The restore action's
error shows the `psql` errors. A common cause is an application connected to a database during the
replay: `DROP DATABASE` fails. Scale the application down before the restore.

**`secret "postgresql-<namespace>" not found`.** The admin Secret of Step 2 is missing.

For a leftover staging PVC, a policy filter that excludes it, an SCC error, or a backup that holds
no dump, see [Troubleshooting](../mysql-logical-late-capture-openshift-example/README.md#troubleshooting)
on the MySQL page (replace `mysql` with `postgresql`).

---

## Initial prompt

> Port the Kasten documentation logical backup examples for OpenShift (MySQL, MongoDB, PostgreSQL)
> to late capture, as was done for the Kubernetes versions. Kasten on the lab cluster is deployed
> by Argo CD.
