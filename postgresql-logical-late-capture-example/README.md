# Kasten Blueprint — Logical PostgreSQL Backup on a Staging PVC (Late Capture)

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

A logical backup (`pg_dumpall` / `psql`) of **PostgreSQL deployed with the Bitnami Helm chart**. It
is the late-capture version of the Kasten documentation example
[Logical PostgreSQL Backup](https://docs.kasten.io/latest/kanister/postgresql/install_logical). The
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
| Images | `kanister-tools` + `postgres:17-bookworm` in a `MultiContainerRun` | The server's own image, `bitnamilegacy/postgresql` (`PrepareData`) |
| Password | Rendered into the pod command | Read from the Secret through `podOverride` env (`PGPASSWORD`) |
| Secret and host name | `<instance label>-postgresql` | The StatefulSet name (the chart's full name) |
| Dump | `pg_dumpall --clean` | `pg_dumpall --clean --if-exists`, compressed with `gzip` |
| Errors during the replay | Ignored (`psql` exits 0) | Two expected errors accepted, any other fails the restore |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding (annotation still possible) |

**Secret and host name.** The documentation example builds the name from the
`app.kubernetes.io/instance` label (the release name) plus `-postgresql`. The chart drops the
suffix when the release name already contains `postgresql`, so a release named `postgresql` gets
a StatefulSet, Secret and Service named `postgresql`, and the documentation blueprint looks for
`postgresql-postgresql`. The StatefulSet name is always right.

**Errors during the replay.** A `--clean` dump starts by dropping and recreating every role,
including `postgres`, which the replay session itself uses. PostgreSQL refuses both statements
(*"current user cannot be dropped"* and *"role "postgres" already exists"*), and `psql` continues.
These two errors are harmless. `psql` exits `0` even when other statements fail, so the
documentation example would report success on a failed replay. This blueprint reads `psql`'s error
output and fails on any other error.

## Pattern

**Pattern 6 — Logical dump on a staging PVC (late capture).** The requester chose this pattern:
the request was to port the documentation example to late capture.

Why it fits: a `pg_dumpall` output is a **full** dump, so it does not need the previous backup on the
volume. A PVC that exists only during the backup is enough, and there is no keeper Deployment to
run or PVC to keep between backups (patterns 3–5). The blueprint is bound to the StatefulSet, so it
works with a BlueprintBinding.

**What the backup protects:** every database, every role with its password, and the grants, as of
the start of the dump. Each database is dumped in one transaction, so each is consistent on its
own. Every Kubernetes object in the namespace is also in the restore point.

**What it does not protect:**
- The PostgreSQL data PVC. The policy excludes it, as in the documentation example. After a restore
  into an empty namespace, PostgreSQL starts on a **new, empty volume** and the dump rebuilds the
  data.
- Consistency **between** databases. `pg_dumpall` dumps the databases one after the other; a
  transaction that writes to two databases during the dump can appear in one and not in the other.
- Point-in-time recovery. The dump is the state at the time of the backup; there is no WAL archive.

**Incrementality.** The staging PVC is a new volume at every backup, so the local snapshot is a full
copy each time. The export deduplicates by content: rows added at the end of a table export
little, updates spread over the whole table export the whole dump again. This was measured for
MongoDB ([Step 6](../mongodb-logical-late-capture-example/README.md#step-6--export-deduplication));
it was not measured for PostgreSQL.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.35.8` (EKS) |
| Kasten | `9.0.10`, `features.lateCapture=true` |
| Helm chart | `bitnami/postgresql 16.7.27` |
| Database | `PostgreSQL 17.6` (`bitnamilegacy/postgresql:17.6.0-debian-12-r4`), `standalone` architecture |

> Detect your own Kasten version with `helm ls -n kasten-io` (or, on OpenShift OLM,
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`).

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, replay it on restore |
| `blueprintbinding.yaml` | Binds the blueprint to every Bitnami PostgreSQL primary StatefulSet |
| `policy.yaml` | On-demand policy with export that excludes the PostgreSQL data PVC and keeps the staging PVC |

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

Bitnami moved its free images to the `bitnamilegacy` Docker Hub repository, and stopped publishing
there at PostgreSQL `17.6.0`. Recent chart versions (`18.x`) default to the image tag `latest`,
which the `bitnamilegacy` repository resolves to an older `17.5` image. The commands below pin chart
`16.7.27` (the last one built for `17.6.0`) **and** the image tag, and set
`global.security.allowInsecureImages=true` (which disables the chart's image-origin check), as the
documentation example does. The blueprint uses the same image for `pg_dumpall` and `psql`, so the
tools always match the server version. If you change the PostgreSQL version, change the image in
`blueprint.yaml` too: `pg_dumpall` refuses to dump a server newer than itself.

---

## Step 2 — Deploy the workload and create test data

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
kubectl create namespace postgresql-logical

helm install postgres bitnami/postgresql --namespace postgresql-logical \
  --version 16.7.27 \
  --set primary.persistence.storageClass=gp3 \
  --set global.security.allowInsecureImages=true \
  --set volumePermissions.image.repository=bitnamilegacy/os-shell \
  --set image.repository=bitnamilegacy/postgresql \
  --set image.tag=17.6.0-debian-12-r4 \
  --wait
```

> **Storage class**: see the callout above — replace `gp3` with a CSI snapshot-capable class.

Create a small dataset (< 5 KB):

```bash
NS=postgresql-logical
PW=$(kubectl get secret -n $NS postgres-postgresql -o jsonpath='{.data.postgres-password}' | base64 -d)
kubectl exec -n $NS postgres-postgresql-0 -- env PGPASSWORD="$PW" psql -U postgres -c "CREATE DATABASE test;"
kubectl exec -n $NS postgres-postgresql-0 -- env PGPASSWORD="$PW" psql -U postgres -d test -c "
CREATE TABLE company (id INT PRIMARY KEY, name TEXT NOT NULL, age INT NOT NULL, address CHAR(50), salary REAL, join_date DATE);
INSERT INTO company VALUES
 (1,'Paul',32,'California',20000.00,'2001-07-13'),
 (2,'Allen',25,'Texas',NULL,'2007-12-13'),
 (3,'Teddy',23,'Norway',20000.00,NULL),
 (4,'Mark',25,'Rich-Mond',65000.00,'2007-12-13');
SELECT * FROM company;"
```

Expected: 4 rows.

### Removing the workload

```bash
helm uninstall postgres -n postgresql-logical
kubectl delete pvc -n postgresql-logical --all        # Helm leaves the data PVC
kubectl delete namespace postgresql-logical
kubectl delete policy postgresql-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=postgresql-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

The same sequence as the blueprint, run with `kubectl` and a CSI `VolumeSnapshot` to stand in for
Kasten:

1. Create a PVC (`gp3`, `2Gi`).
2. Run a pod with `bitnamilegacy/postgresql:17.6.0-debian-12-r4` (user `1001`, `fsGroup: 1001`) that
   mounts it and runs `pg_dumpall -h postgres-postgresql -U postgres --clean --if-exists | gzip`,
   then `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop the `test` database.
5. Create a PVC from the snapshot, run `zcat dump.sql.gz | psql -h postgres-postgresql -U postgres -d postgres`.
6. The 4 rows are back. `psql` reported exactly two errors, both about the `postgres` role (see
   [above](#what-changes-compared-with-the-kasten-documentation-example)), and exited `0`.

**The pod must run with `fsGroup: 1001`.** A new EBS volume is owned by `root`, and the Bitnami
image runs as user `1001`, so `pg_dumpall` cannot write the dump otherwise.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps/statefulsets` labelled `app.kubernetes.io/name=postgresql`
**and** `app.kubernetes.io/component=primary`. With `architecture=replication` the chart also
creates a read-replica StatefulSet; it is not bound, and the dump is taken from the primary. A
StatefulSet with an explicit `kanister.kasten.io/blueprint` annotation keeps its own blueprint. To
bind one StatefulSet by hand instead:

```bash
kubectl annotate statefulset postgres-postgresql -n postgresql-logical \
  kanister.kasten.io/blueprint=postgresql-logical-late-capture-blueprint
```

The blueprint assumes the Bitnami chart's conventions: the Secret and the Service have the
StatefulSet's name, and the Secret has the key `postgres-password`. With `auth.existingSecret`
the Secret has another name; adapt the blueprint.

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<sts>-dump`. `PrepareData` mounts it, runs `pg_dumpall --clean --if-exists` through `gzip`, runs `sync`, and checks the archive with `gzip -t`. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<sts>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact), waits up to 5 minutes for `pg_isready`, replays the dump with `psql`, and fails on any error other than the two expected ones. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true` and runs with `set -euo pipefail`. With the
default (`false`), a failed `pg_dumpall` would still report success, and Kasten would snapshot an
empty dump. `pipefail` matters here: without it, a failed `pg_dumpall` piped into a successful
`gzip` returns 0.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

`policy.yaml` excludes the PVCs labelled `app.kubernetes.io/name=postgresql` — the data PVC. Two
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
     generateName: run-postgresql-logical-
     namespace: kasten-io
   spec:
     subject:
       kind: Policy
       name: postgresql-logical-backup
       namespace: kasten-io
   EOF
   ```

   During the backup the PVC `postgres-postgresql-dump` appears, becomes `Bound`, and is deleted.
   The restore point holds `postgres-postgresql-dump` and no `data-postgres-postgresql-0`.

2. **Delete the data:**

   ```bash
   kubectl exec -n postgresql-logical postgres-postgresql-0 -- \
     env PGPASSWORD="$PW" psql -U postgres -c "DROP DATABASE test"
   ```

3. **Restore** (in place):

   ```bash
   kubectl get restorepoint -n postgresql-logical
   kubectl create -f - --validate=false <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RestoreAction
   metadata:
     generateName: restore-
     namespace: postgresql-logical
   spec:
     subject:
       apiVersion: apps.kio.kasten.io/v1alpha1
       kind: RestorePoint
       name: <RESTORE_POINT_NAME>
       namespace: postgresql-logical
     targetNamespace: postgresql-logical
   EOF
   ```

   Kasten restores the staging PVC, then `restorePosthook` replays the dump and deletes the PVC.

4. **Verify** — expect the 4 rows:

   ```bash
   kubectl exec -n postgresql-logical postgres-postgresql-0 -- \
     env PGPASSWORD="$PW" psql -U postgres -d test -c "SELECT * FROM company"
   ```

### Restore after the namespace was deleted

The data PVC is not in the restore point, so PostgreSQL starts on a **new, empty volume**,
initializes with the restored Secret (same `postgres` password), and `restorePosthook` loads the
dump into it.

> **The policy must export.** Kasten keeps the local `VolumeSnapshot` of the staging PVC in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`. Deleting the
> namespace deletes the EBS snapshot. Restore from the **exported** restore point.

```bash
kubectl delete namespace postgresql-logical

# The RestorePoint objects went with the namespace; the RestorePointContent remains.
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=postgresql-logical,k10.kasten.io/exportType=portableAppData
kubectl create namespace postgresql-logical
kubectl create -f - <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata:
  name: postgresql-logical-restored
  namespace: postgresql-logical
spec:
  restorePointContentRef:
    name: <RESTORE_POINT_CONTENT_NAME>
EOF
```

Then run the same RestoreAction as above with `name: postgresql-logical-restored`.

Tested on Kasten 9.0.10: the in-place restore and the restore after deleting the namespace (from
the exported restore point) both brought back the 4 rows, and a new backup after the second restore
completed (the restored staging PVC had been deleted).

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n postgresql-logical -o jsonpath='{.items[-1:].status.error}'
```

**A backup failed after a cancelled or crashed one.** `backupPosthook` did not run, so the
staging PVC stayed behind. The next backup refuses to snapshot an old dump and fails with
*"Late-captured PVC was snapshotted before the pre-backup hook; its data in this restore point
would be stale"*. Its own `backupPosthook` still runs and deletes the PVC, so **the backup after
that succeeds** without any action. Run the policy again, or wait for the next schedule. If the
leftover PVC is `Pending` (never mounted), discovery fails with *"PVC not bound"*; delete it by
hand:

```bash
kubectl delete pvc postgres-postgresql-dump -n postgresql-logical
```

**`Late-captured PVC is excluded by the policy's resource filters`.** The policy filter matches the
staging PVC. Do not give the staging PVC the labels the filter uses.

**The restore fails with "Unexpected errors while replaying the dump".** The restore action's error
shows the `psql` errors. A common cause is a database that has active connections from an
application during the replay: `DROP DATABASE` fails. Scale the application down before the
restore.

**The backup succeeds but the restore finds no `dump.sql.gz`.** Check the feature flag (top of this
page).

---

## Initial prompt

> Port the Kasten documentation logical backup examples (MySQL, PostgreSQL, Microsoft SQL Server)
> to late capture, as was done for MongoDB. Don't measure deduplication. Test them one by one on
> EKS Paris.
