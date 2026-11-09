# Kasten Blueprint — Logical MySQL Backup on a Staging PVC (Late Capture)

> ⚠️ **Kasten `9.0.10` or later, with the `lateCapture` feature flag on.**
>
> ```bash
> helm upgrade k10 kasten/k10 -n kasten-io --version 9.0.10 --reuse-values --set features.lateCapture=true
> kubectl get cm k10-features -n kasten-io -o jsonpath='{.data.lateCapture}'   # must print: true
> ```
>
> **Without the flag, the backup succeeds and holds no dump.** Kasten only logs the warning
> *"Late-capture declarations present but the lateCapture feature flag is not set; ignoring
> them"*. The staging PVC is created, never snapshotted, then deleted by `backupPosthook`. Check
> the flag before you rely on this blueprint.

A logical backup (`mysqldump` / `mysql`) of **MySQL deployed with the Bitnami Helm chart**. It is
the late-capture version of the Kasten documentation example
[Logical MySQL Backup](https://docs.kasten.io/latest/kanister/mysql/install). The
[MongoDB version](../mongodb-logical-late-capture-example/) of the same conversion explains the
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
| Images | `kanister-tools` + `mysql:8` in a `MultiContainerRun` | The server's own image, `bitnamilegacy/mysql` (`PrepareData`) |
| Password | Rendered into the pod command | Read from the Secret through `podOverride` env (`MYSQL_PWD`) |
| Secret and host name | `app.kubernetes.io/instance` label (the release name) | The StatefulSet name (the chart's full name) |
| Dump | `--single-transaction --all-databases` | Same, plus `--routines --events`, compressed with `gzip` |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding (annotation still possible) |

**Secret and host name.** The documentation example takes the release name from the
`app.kubernetes.io/instance` label. That only works when the release name is the chart's full
name, as with `mysql-release`. With a release named `db`, the chart names the StatefulSet, the
Secret and the Service `db-mysql`, and the documentation blueprint looks for `db`. The StatefulSet
name is always right.

## Pattern

**Pattern 6 — Logical dump on a staging PVC (late capture).** The requester chose this pattern:
the request was to port the documentation example to late capture.

Why it fits: a `mysqldump` output is a **full** dump, so it does not need the previous backup on the
volume. A PVC that exists only during the backup is enough, and there is no keeper Deployment to
run or PVC to keep between backups (patterns 3–5). The blueprint is bound to the StatefulSet, so it
works with a BlueprintBinding.

**What the backup protects:** every database, user and grant on the server (`--all-databases`
includes the `mysql` system schema), stored procedures, functions and events, as of the start of
the dump. `--single-transaction` gives a consistent view of InnoDB tables without locking them.
Every Kubernetes object in the namespace is also in the restore point.

**What it does not protect:**
- The MySQL data PVC. The policy excludes it, as in the documentation example. After a restore into
  an empty namespace, MySQL starts on a **new, empty volume** and the dump rebuilds the data.
- Tables that do not use InnoDB (MyISAM, for example). `--single-transaction` does not make them
  consistent; a write during the dump can leave them out of step with the rest.

**Incrementality.** The staging PVC is a new volume at every backup, so the local snapshot is a full
copy each time. The export deduplicates by content: records added at the end of a table export
little, updates spread over the whole table export the whole dump again. This was measured for
MongoDB ([Step 6](../mongodb-logical-late-capture-example/README.md#step-6--export-deduplication));
it was not measured for MySQL.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.35.8` (EKS) |
| Kasten | `9.0.10`, `features.lateCapture=true` |
| Helm chart | `bitnami/mysql 14.0.3` |
| Database | `MySQL 9.4.0` (`bitnamilegacy/mysql:9.4.0-debian-12-r1`), `standalone` architecture |

> Detect your own Kasten version with `helm ls -n kasten-io` (or, on OpenShift OLM,
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`).

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, replay it on restore |
| `blueprintbinding.yaml` | Binds the blueprint to every Bitnami MySQL primary StatefulSet |
| `policy.yaml` | On-demand policy with export that excludes the MySQL data PVC and keeps the staging PVC |

---

## Prerequisites

- Kasten `9.0.10`+ with `features.lateCapture=true` (see the top of this page).
- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten.
- A **Location** profile for the policy (not an `Infra` profile): the `lateCapture` artifact is
  passed from `backupPrehook` to `restorePosthook`, which requires one.

> **Storage class**: `gp3` is the AWS EBS CSI storage class used in our test environment. It
> appears in the Helm command below **and in `blueprint.yaml`** (the staging PVC spec).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on AKS, `standard-rwo` on GKE, your custom class on bare-metal, and so on).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

The staging PVC requests `2Gi`. Set the size in `blueprint.yaml` above the size of your largest
compressed dump.

### Bitnami image availability

Bitnami moved its free images to the `bitnamilegacy` Docker Hub repository. The commands below use
`bitnamilegacy/mysql` and set `global.security.allowInsecureImages=true` (which disables the
chart's image-origin check), as the documentation example does. The blueprint uses the same image
for `mysqldump` and `mysql`, so the tools always match the server version. If you change the MySQL
version, change the image in `blueprint.yaml` too.

---

## Step 2 — Deploy the workload and create test data

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
kubectl create namespace mysql-logical

helm install mysql-release bitnami/mysql --namespace mysql-logical \
  --version 14.0.3 \
  --set primary.persistence.storageClass=gp3 \
  --set global.security.allowInsecureImages=true \
  --set volumePermissions.image.repository=bitnamilegacy/os-shell \
  --set image.repository=bitnamilegacy/mysql \
  --wait
```

> **Storage class**: see the callout above — replace `gp3` with a CSI snapshot-capable class.

Create a small dataset (< 5 KB):

```bash
NS=mysql-logical
PW=$(kubectl get secret -n $NS mysql-release -o jsonpath='{.data.mysql-root-password}' | base64 -d)
kubectl exec -n $NS mysql-release-0 -c mysql -- env MYSQL_PWD="$PW" mysql -u root -e "
CREATE DATABASE test; USE test;
CREATE TABLE pets (name VARCHAR(20), owner VARCHAR(20), species VARCHAR(20), sex CHAR(1), birth DATE, death DATE);
INSERT INTO pets VALUES
 ('Puffball','Diane','hamster','f','1999-03-30',NULL),
 ('Fluffy','Harold','cat','f','1993-02-04',NULL),
 ('Claws','Gwen','cat','m','1994-03-17',NULL),
 ('Buffy','Harold','dog','f','1989-05-13',NULL);
SELECT * FROM pets;"
```

Expected: 4 rows.

### Removing the workload

```bash
helm uninstall mysql-release -n mysql-logical
kubectl delete pvc -n mysql-logical --all        # Helm leaves the data PVC
kubectl delete namespace mysql-logical
kubectl delete policy mysql-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=mysql-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

The same sequence as the blueprint, run with `kubectl` and a CSI `VolumeSnapshot` to stand in for
Kasten:

1. Create a PVC (`gp3`, `2Gi`).
2. Run a pod with `bitnamilegacy/mysql:9.4.0-debian-12-r1` (user `1001`, `fsGroup: 1001`) that mounts
   it and runs `mysqldump -h mysql-release --single-transaction --all-databases --routines --events | gzip`,
   then `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop the `test` database.
5. Create a PVC from the snapshot, run `zcat dump.sql.gz | mysql -h mysql-release -u root`.
6. The 4 rows are back.

**The pod must run with `fsGroup: 1001`.** A new EBS volume is owned by `root`, and the Bitnami
image runs as user `1001`, so `mysqldump` cannot write the dump otherwise.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps/statefulsets` labelled `app.kubernetes.io/name=mysql` **and**
`app.kubernetes.io/component=primary`. With `architecture=replication` the chart also creates a
secondary StatefulSet; it is not bound, and the dump is taken from the primary. A StatefulSet with
an explicit `kanister.kasten.io/blueprint` annotation keeps its own blueprint. To bind one
StatefulSet by hand instead:

```bash
kubectl annotate statefulset mysql-release -n mysql-logical \
  kanister.kasten.io/blueprint=mysql-logical-late-capture-blueprint
```

The blueprint assumes the Bitnami chart's conventions: the Secret and the Service have the
StatefulSet's name, and the Secret has the key `mysql-root-password`. Adapt them if your release
differs.

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<sts>-dump`. `PrepareData` mounts it, runs `mysqldump --single-transaction --all-databases --routines --events` through `gzip`, runs `sync`, and checks the archive with `gzip -t`. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<sts>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact), waits up to 5 minutes for MySQL to answer, and replays the dump with `mysql`. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true` and runs with `set -euo pipefail`. With the
default (`false`), a failed `mysqldump` would still report success, and Kasten would snapshot an
empty dump. `pipefail` matters here: without it, a failed `mysqldump` piped into a successful
`gzip` returns 0.

The dump is written to `dump.sql.gz.tmp` and renamed only when `mysqldump` succeeded, so the PVC
never holds a file named `dump.sql.gz` that is incomplete.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

`policy.yaml` excludes the PVCs labelled `app.kubernetes.io/name=mysql` — the data PVC. Two
details matter:

- The rule is limited to `resource: persistentvolumeclaims`. The StatefulSet carries the same
  label; excluding it would remove the workload, and the blueprint would never run.
- The staging PVC does **not** carry this label. Late capture fails the backup if the policy
  excludes a declared PVC.

The policy also **exports** the restore point to the same location profile. The local snapshot is
deleted with the namespace; the exported copy is what a restore after the loss of the namespace or
the cluster uses.

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

   During the backup the PVC `mysql-release-dump` appears, becomes `Bound`, and is deleted. The
   restore point holds `mysql-release-dump` and no `data-mysql-release-0`.

2. **Delete the data:**

   ```bash
   kubectl exec -n mysql-logical mysql-release-0 -c mysql -- \
     env MYSQL_PWD="$PW" mysql -u root -e "DROP DATABASE test"
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

   Kasten restores the staging PVC, then `restorePosthook` replays the dump and deletes the PVC.

4. **Verify** — expect the 4 rows:

   ```bash
   kubectl exec -n mysql-logical mysql-release-0 -c mysql -- \
     env MYSQL_PWD="$PW" mysql -u root -e "SELECT * FROM test.pets"
   ```

### Restore after the namespace was deleted

The data PVC is not in the restore point, so MySQL starts on a **new, empty volume**, initializes
with the restored Secret (same root password), and `restorePosthook` loads the dump into it.

> **The policy must export.** Kasten keeps the local `VolumeSnapshot` of the staging PVC in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`. Deleting the
> namespace deletes the EBS snapshot. Restore from the **exported** restore point.

```bash
kubectl delete namespace mysql-logical

# The RestorePoint objects went with the namespace; the RestorePointContent remains.
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=mysql-logical,k10.kasten.io/exportType=portableAppData
kubectl create namespace mysql-logical
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

Tested on Kasten 9.0.10: the in-place restore and the restore after deleting the namespace (from
the exported restore point) both brought back the 4 rows, and a new backup after the second restore
completed (the restored staging PVC had been deleted).

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n mysql-logical -o jsonpath='{.items[-1:].status.error}'
```

**A backup failed after a cancelled or crashed one.** `backupPosthook` did not run, so the
staging PVC stayed behind. The next backup refuses to snapshot an old dump and fails with
*"Late-captured PVC was snapshotted before the pre-backup hook; its data in this restore point
would be stale"*. Its own `backupPosthook` still runs and deletes the PVC, so **the backup after
that succeeds** without any action. Run the policy again, or wait for the next schedule. If the
leftover PVC is `Pending` (never mounted), discovery fails with *"PVC not bound"*; delete it by
hand:

```bash
kubectl delete pvc mysql-release-dump -n mysql-logical
```

**`Late-captured PVC is excluded by the policy's resource filters`.** The policy filter matches the
staging PVC. Do not give the staging PVC the labels the filter uses.

**The backup succeeds but the restore finds no `dump.sql.gz`.** Check the feature flag (top of this
page).

---

## Initial prompt

> Port the Kasten documentation logical backup examples (MySQL, PostgreSQL, Microsoft SQL Server)
> to late capture, as was done for MongoDB. Don't measure deduplication. Test them one by one on
> EKS Paris.
