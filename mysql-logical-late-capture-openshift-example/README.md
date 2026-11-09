# Kasten Blueprint — Logical MySQL Backup on a Staging PVC (Late Capture), OpenShift

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

A logical backup (`mysqldump` / `mysql`) of **MySQL deployed on OpenShift with the
`mysql-ephemeral` template** (a `DeploymentConfig`). It is the late-capture version of the Kasten
documentation example
[Logical MySQL Backup for OpenShift](https://docs.kasten.io/latest/kanister/mysql/install_logical_os).
The Kubernetes version is [mysql-logical-late-capture-example](../mysql-logical-late-capture-example/),
and [mongodb-logical-late-capture-example](../mongodb-logical-late-capture-example/) explains the
mechanism in more detail.

## What changes compared with the Kasten documentation example

The documentation blueprint defines `backup`, `restore` and `delete` actions. It streams the dump
to the location profile with `kando location push`: **the blueprint is the data mover**, which
[AGENTS.md](../AGENTS.md#core-principle) forbids (no immutability, no export deduplication, an
artifact nobody can inspect, a custom `delete` action to clean it up).

This blueprint writes the dump to a **staging PVC** instead, and **Kasten is the data mover**:

| | Documentation example | This blueprint |
|---|---|---|
| Where the dump goes | `kando location push` to object storage | A staging PVC that Kasten snapshots |
| Actions | `backup`, `restore`, `delete` | `backupPrehook`, `backupPosthook`, `restorePosthook` |
| Images | `kanister-tools` + `mysql:8` from Docker Hub in a `MultiContainerRun` | The server's own image, read from the `DeploymentConfig` (`PrepareData`) |
| Password | Rendered into the pod command | Read from the Secret through `podOverride` env (`MYSQL_PWD`) |
| Dump | `--single-transaction --all-databases` | Same, plus `--routines --events`, compressed with `gzip` |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding on `DeploymentConfigs` from the MySQL templates (annotation still possible) |

**The image comes from the workload.** The blueprint reads the image of the `DeploymentConfig`'s
container (`{{ (index .Object.spec.template.spec.containers 0).image }}`). An OpenShift image
trigger writes the resolved image there (`registry.redhat.io/rhel8/mysql-80@sha256:…`), so
`mysqldump` and `mysql` always match the server, wherever the image stream points.

## Pattern

**Pattern 6 — Logical dump on a staging PVC (late capture).** The requester chose this pattern:
the request was to port the documentation example to late capture.

Why it fits: a `mysqldump` output is a **full** dump, so it does not need the previous backup on the
volume. A PVC that exists only during the backup is enough. The blueprint is bound to the
`DeploymentConfig`: Kasten 9.0.10 treats a `DeploymentConfig` as a workload, so late capture and
BlueprintBinding both work.

**The template is ephemeral: the dump is the only copy of the data.** `mysql-ephemeral` keeps the
database in an `emptyDir`. There is no data PVC to snapshot, and every pod restart starts MySQL
empty. After a restore (in place, or into an empty namespace), `restorePosthook` rebuilds every
database from the dump. With the `mysql-persistent` template, the data PVC is snapshotted as well;
exclude it in the policy if the dump is meant to replace it (see [The policy](#the-policy)).

**What the backup protects:** every database, user and grant on the server (`--all-databases`
includes the `mysql` system schema), stored procedures, functions and events, as of the start of
the dump. Every Kubernetes object in the namespace (`DeploymentConfig`, Secret, Service,
ImageStream) is also in the restore point.

**What it does not protect:** tables that do not use InnoDB (MyISAM, for example):
`--single-transaction` does not make them consistent with the rest.

**Incrementality.** The staging PVC is a new volume at every backup, so the local snapshot is a full
copy each time. The export deduplicates by content; this was measured for MongoDB
([Step 6](../mongodb-logical-late-capture-example/README.md#step-6--export-deduplication)), not for
MySQL.

## Versions

| Component | Version |
|---|---|
| OpenShift | `4.21.30` (bare metal, OpenShift Data Foundation) |
| Kasten | `9.0.10` (OpenShift operator), `features.lateCapture=true` |
| Template | `mysql-ephemeral` from `openshift/origin` (`master`), `MYSQL_VERSION=8.0-el8` |
| Database | `MySQL 8.0.45` (`registry.redhat.io/rhel8/mysql-80`) |

The manual validation (Step 3) was also run on OpenShift `4.18.21` on Azure (MySQL `8.0.43`, Azure
Disk CSI).

> Detect your own Kasten version with
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`
> (operator) or `helm ls -n kasten-io` (Helm).

`DeploymentConfig` is deprecated since OpenShift 4.14 and is an optional cluster capability; it was
still enabled on 4.21. For a new deployment, prefer a `Deployment`; the blueprint then needs
`.Deployment` instead of `.DeploymentConfig`, and the binding `apps/deployments`.

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, replay it on restore |
| `blueprintbinding.yaml` | Binds the blueprint to `DeploymentConfigs` created by the MySQL templates |
| `policy.yaml` | On-demand policy with export |

---

## Prerequisites

- Kasten `9.0.10`+ with `features.lateCapture=true` (see the top of this page).
- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten.
- A **Location** profile for the policy (not an `Infra` profile): the `lateCapture` artifact is
  passed from `backupPrehook` to `restorePosthook`, which requires one.
- Access to `registry.redhat.io` from the cluster (the global pull secret of OpenShift has it).

> **Storage class**: `ocs-storagecluster-ceph-rbd` is the OpenShift Data Foundation RBD class used
> in our test environment. It appears **in `blueprint.yaml`** (the staging PVC spec).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on Azure Red Hat OpenShift or an Azure cluster, `gp3-csi` on ROSA or
> AWS, your custom class on bare-metal, and so on).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

The staging PVC requests `2Gi`. Set the size in `blueprint.yaml` above the size of your largest
compressed dump.

---

## Step 2 — Deploy the workload and create test data

The template takes its image from the `mysql` image stream in the namespace given by `NAMESPACE`
(default: `openshift`). That image stream is part of the OpenShift samples. On a cluster where the
Samples Operator is `Removed`, the image stream does not exist and the `DeploymentConfig` never
starts. Create the image stream in the application namespace instead, from the image the samples
use:

```bash
oc create namespace mysql-logical

# Only if `oc get is mysql -n openshift` finds nothing:
oc import-image mysql:8.0-el8 --from=registry.redhat.io/rhel8/mysql-80:latest --confirm -n mysql-logical

oc new-app https://raw.githubusercontent.com/openshift/origin/master/examples/db-templates/mysql-ephemeral-template.json \
  --namespace mysql-logical \
  -p MYSQL_ROOT_PASSWORD=secretpassword \
  -p NAMESPACE=mysql-logical            # omit to use the image stream in the openshift namespace
oc rollout status dc/mysql -n mysql-logical
```

Create a small dataset (< 5 KB). In this image, `root@localhost` has no password (socket) and
`root@%` has `MYSQL_ROOT_PASSWORD`. The blueprint connects through the Service, as `root@%`:

```bash
NS=mysql-logical
oc exec -n $NS dc/mysql -- bash -c 'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -h 127.0.0.1 -u root -e "
CREATE DATABASE test; USE test;
CREATE TABLE pets (name VARCHAR(20), owner VARCHAR(20), species VARCHAR(20), sex CHAR(1), birth DATE, death DATE);
INSERT INTO pets VALUES
 (\"Puffball\",\"Diane\",\"hamster\",\"f\",\"1999-03-30\",NULL),
 (\"Fluffy\",\"Harold\",\"cat\",\"f\",\"1993-02-04\",NULL),
 (\"Claws\",\"Gwen\",\"cat\",\"m\",\"1994-03-17\",NULL),
 (\"Buffy\",\"Harold\",\"dog\",\"f\",\"1989-05-13\",NULL);
SELECT * FROM pets;"'
```

Expected: 4 rows.

### Removing the workload

```bash
oc delete namespace mysql-logical
kubectl delete policy mysql-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=mysql-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

The same sequence as the blueprint, run with `oc` and a CSI `VolumeSnapshot` to stand in for
Kasten (on the Azure cluster):

1. Create a PVC (`2Gi`).
2. Run a pod with the MySQL image that mounts it and runs
   `mysqldump -h mysql -u root --single-transaction --all-databases --routines --events | gzip`, then
   `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop the `test` database.
5. Create a PVC from the snapshot, run `zcat dump.sql.gz | mysql -h mysql -u root`.
6. The 4 rows are back.

**The pod's user must be able to write to the new volume.** The first attempt failed with
*"Permission denied"*: the pod, created by a cluster administrator, was admitted under the `anyuid`
SCC and ran as the image's user (`mysql`, uid 27) with no `fsGroup`, on a volume owned by `root`.
This does not happen when Kasten creates the pod; see the next section.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps.openshift.io/deploymentconfigs` labelled
`template=mysql-ephemeral-template` or `template=mysql-persistent-template` (the templates put this
label on every object). A `DeploymentConfig` with an explicit `kanister.kasten.io/blueprint`
annotation keeps its own blueprint. To bind one `DeploymentConfig` by hand instead:

```bash
oc annotate dc mysql -n mysql-logical \
  kanister.kasten.io/blueprint=mysql-logical-late-capture-openshift-blueprint
```

The blueprint assumes the template's conventions: the Secret and the Service have the
`DeploymentConfig`'s name, the Secret has the key `database-root-password`, and the MySQL container
is the first container.

### Security context and SCC

The blueprint sets **no** `securityContext`. Kasten adds its own pod override to every Kanister
pod and runs it **as root** (`runAsUser: 0`); Kasten's service account is allowed to create such
pods, so the dump pod can write to the new volume.

> **Do not pin the pod to `restricted-v2`** (annotation `openshift.io/required-scc`). It looks like
> the safe choice, and it works when you create the pod yourself, but Kasten's `runAsUser: 0` then
> violates the SCC and the backup fails with
> *"pods "prepare-data-job-" is forbidden: … runAsUser: Invalid value: 0: must be in the ranges"*.
> Tested.

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<dc>-dump`. `PrepareData` (the server's own image) mounts it, runs `mysqldump --single-transaction --all-databases --routines --events` through `gzip`, runs `sync`, and checks the archive with `gzip -t`. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<dc>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact), waits up to 5 minutes for MySQL to answer, and replays the dump with `mysql`. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true` and runs with `set -euo pipefail`. With the
default (`false`), a failed `mysqldump` would still report success, and Kasten would snapshot an
empty dump. `pipefail` matters here: without it, a failed `mysqldump` piped into a successful
`gzip` returns 0.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

With the ephemeral template there is no data PVC, so the policy has no filter. With the
`mysql-persistent` template, the data PVC (named after the `DeploymentConfig`) is snapshotted as
well. To keep only the dump, add an `excludeResources` filter on `persistentvolumeclaims` that
matches the data PVC and **not** the staging PVC: late capture fails the backup if the policy
excludes a declared PVC.

The policy also **exports** the restore point. The local snapshot is deleted with the namespace;
the exported copy is what a restore after the loss of the namespace or the cluster uses.

---

## Step 5 — End-to-end test through Kasten

1. **Run the policy** (UI, or a `RunAction`):

   ```bash
   kubectl create -f - <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RunAction
   metadata:
     generateName: run-mysql-logical-
     namespace: kasten-io
   spec:
     subject:
       kind: Policy
       name: mysql-logical-backup
       namespace: kasten-io
   EOF
   ```

   During the backup the PVC `mysql-dump` appears, becomes `Bound`, and is deleted. The restore
   point holds `mysql-dump`.

2. **Delete the data:**

   ```bash
   oc exec -n mysql-logical dc/mysql -- bash -c \
     'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -h 127.0.0.1 -u root -e "DROP DATABASE test"'
   ```

3. **Restore** (in place):

   ```bash
   kubectl get restorepoint -n mysql-logical
   kubectl create -f - --validate=false <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RestoreAction
   metadata:
     generateName: restore-
     namespace: mysql-logical
   spec:
     subject:
       apiVersion: apps.kio.kasten.io/v1alpha1
       kind: RestorePoint
       name: <RESTORE_POINT_NAME>
       namespace: mysql-logical
     targetNamespace: mysql-logical
   EOF
   ```

4. **Verify** — expect the 4 rows:

   ```bash
   oc exec -n mysql-logical dc/mysql -- bash -c \
     'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -h 127.0.0.1 -u root -e "SELECT * FROM test.pets"'
   ```

### Restore after the namespace was deleted

Kasten restores the `DeploymentConfig`, the Secret, the Service and the image stream. MySQL starts
empty (the template is ephemeral), and `restorePosthook` loads the dump.

> **The policy must export.** Kasten keeps the local `VolumeSnapshot` of the staging PVC in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`. Deleting the
> namespace deletes the snapshot. Restore from the **exported** restore point.

```bash
oc delete namespace mysql-logical

# The RestorePoint objects went with the namespace; the RestorePointContent remains.
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=mysql-logical,k10.kasten.io/exportType=portableAppData
oc create namespace mysql-logical
kubectl create -f - <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata:
  name: mysql-logical-restored
  namespace: mysql-logical
spec:
  restorePointContentRef:
    name: <RESTORE_POINT_CONTENT_NAME>
EOF
```

Then run the same RestoreAction as above with `name: mysql-logical-restored`.

Tested on OpenShift 4.21 with Kasten 9.0.10 (operator):
- the restore after deleting the namespace, from the exported restore point, brought back the 4
  rows into a new, empty MySQL pod;
- a new backup after that restore completed;
- the in-place restore after dropping `test` brought back the 4 rows;
- the staging PVC was deleted after every backup and every restore.

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n mysql-logical -o jsonpath='{.items[-1:].status.error}'
```

**`runAsUser: Invalid value: 0: must be in the ranges`.** Something pins the dump pod to a
restricted SCC (an `openshift.io/required-scc` annotation, or a cluster policy). See
[Security context and SCC](#security-context-and-scc).

**The `DeploymentConfig` never starts a pod after `oc new-app`.** The image stream it waits for is
missing. See Step 2.

**A backup failed after a cancelled or crashed one.** `backupPosthook` did not run, so the
staging PVC stayed behind. The next backup refuses to snapshot an old dump and fails with
*"Late-captured PVC was snapshotted before the pre-backup hook; its data in this restore point
would be stale"*. Its own `backupPosthook` still runs and deletes the PVC, so **the backup after
that succeeds** without any action. If the leftover PVC is `Pending` (never mounted), discovery
fails with *"PVC not bound"*; delete it by hand:

```bash
oc delete pvc mysql-dump -n mysql-logical
```

**`Late-captured PVC is excluded by the policy's resource filters`.** The policy filter matches the
staging PVC. Do not give the staging PVC the labels the filter uses.

**The backup succeeds but the restore finds no `dump.sql.gz`.** Check the feature flag (top of this
page).

---

## Initial prompt

> Port the Kasten documentation logical backup examples for OpenShift (MySQL, MongoDB, PostgreSQL)
> to late capture, as was done for the Kubernetes versions. Kasten on the lab cluster is deployed
> by Argo CD.
