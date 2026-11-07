# Kasten Blueprint — Logical SQL Server Backup on a Staging PVC (Late Capture)

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

A logical backup (`BACKUP DATABASE` / `RESTORE DATABASE`) of **Microsoft SQL Server running as a
Deployment**. It is the late-capture version of the Kasten documentation example
[Logical Microsoft SQL Server Backup](https://docs.kasten.io/latest/kanister/mssql/install). The
[MongoDB version](../mongodb-logical-late-capture-example/) of the same conversion explains the
mechanism in more detail.

> ⚠️ **Read [The backup passes through the node's disk](#the-backup-passes-through-the-nodes-disk)
> before you use this blueprint on a large database.** For SQL Server, a permanent backup PVC
> mounted by SQL Server (pattern 3) is the better design. This blueprint exists to complete the port
> of the documentation examples.

## What changes compared with the Kasten documentation example

The documentation blueprint defines `backup`, `restore` and `delete` actions. It copies the `.bak`
files out of the SQL Server pod and streams them to the location profile with `kando location push`:
**the blueprint is the data mover**, which [AGENTS.md](../AGENTS.md#core-principle) forbids (no
immutability, no export deduplication, an artifact nobody can inspect, a custom `delete` action to
clean it up).

This blueprint writes the `.bak` files to a **staging PVC** instead, and **Kasten is the data
mover**:

| | Documentation example | This blueprint |
|---|---|---|
| Where the backup goes | `kando location push` to object storage | A staging PVC that Kasten snapshots |
| Actions | `backup`, `restore`, `delete` | `backupPrehook`, `backupPosthook`, `restorePosthook` |
| Images | `kanister-tools` + `kanisterio/mssql-tools` (kubectl 1.18) in a `MultiContainerRun` | The shared [`kasten-tools`](../images/kasten-tools/Dockerfile) image (kubectl + tar) |
| Where `sqlcmd` runs | In the tools image, connecting through the Service | Inside the SQL Server container (`kubectl exec`), which ships `sqlcmd` |
| Password | Rendered into the pod command | Never leaves the SQL Server pod: `sqlcmd` reads its `SA_PASSWORD` variable |
| Permissions | The Kanister pod's own account | A ServiceAccount with `pods/exec` in this namespace only (`rbac.yaml`) |
| Free-space check | None | The backup refuses to start when `/tmp` is smaller than the data files |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding (annotation still possible) |

Two changes to the documentation's workload manifest:

- The data PVC has the label `app: mssql`, so the policy can exclude it.
- The Deployment uses `strategy: Recreate`. With the default rolling update, a restarted pod can be
  scheduled on another node while the old pod still holds the `ReadWriteOnce` volume, and the new pod
  never starts.

## Pattern

**Pattern 6 — Database backup on a staging PVC (late capture).** The requester chose this pattern:
the request was to port the documentation example to late capture.

Why it fits: each `BACKUP DATABASE` here is a **full** backup, so it does not need the previous
backup on the volume. A PVC that exists only during the backup is enough. The blueprint is bound to
the Deployment, so it works with a BlueprintBinding.

### The backup passes through the node's disk

SQL Server writes a backup file **on its own filesystem**: `BACKUP DATABASE … TO DISK` and
`RESTORE DATABASE … FROM DISK` read and write paths inside the SQL Server container. The staging
PVC cannot be mounted there: a running pod cannot get a new volume, and changing the Deployment to
add one restarts SQL Server.

So the blueprint writes each `.bak` file to `/tmp` in the SQL Server container, then streams the
files to the staging PVC with `tar` over `kubectl exec`, then deletes them. The restore does the
same in the other direction. `/tmp` is in the container's writable layer, which uses the **node's
own disk**, not a PVC. The consequences:

| Risk | Why |
|---|---|
| The node's disk fills up | The `.bak` files are about the size of the used data. When the node's disk runs low, the kubelet evicts pods — possibly the SQL Server pod itself. |
| The database needs twice its size in free space for a while | Once in `/tmp`, once on the staging PVC (and the same during a restore). |
| A crash leaves files behind | If the backup is interrupted between the `BACKUP` and the clean-up, the `.bak` files stay in `/tmp` until the next backup or the next pod restart. |
| The backup takes longer | The data is written twice and sent through the Kubernetes API server. |

**What the blueprint does about it:** before the first `BACKUP`, it compares the allocated size of
the user databases' data files (`sys.master_files`) with the free space in `/tmp`, and fails with
*"Not enough space in /tmp of the SQL Server container"* if it does not fit. The check is
conservative: allocated size includes empty space in the files.

**The better design for SQL Server:** add a permanent backup PVC to the Deployment, mounted at
`/var/opt/mssql/backup` (pattern 3, [kasten-kanister.md](../kasten-kanister.md)). SQL Server then
writes its backup directly to a PVC that Kasten snapshots: no copy, no node disk, no `pods/exec`
permission. The price is one change to the Deployment and a PVC kept between backups.

### What the backup protects

Every user database (`database_id > 4`) that is `ONLINE`, as a full backup with `CHECKSUM`, and
every Kubernetes object in the namespace (Deployment, Secret, Service, ServiceAccount, Role).

### What it does not protect

- **The system databases** (`master`, `msdb`, `model`), as in the documentation example. Logins,
  SQL Agent jobs and server settings are not in the backup. After a restore into an empty
  namespace, SQL Server starts with new system databases: recreate logins and jobs by hand, or
  script them.
- **The SQL Server data PVC.** The policy excludes it. After a restore into an empty namespace,
  SQL Server starts on an empty volume and the `.bak` files rebuild the user databases. **The PVC
  itself is not recreated** — see [Restore after the namespace was deleted](#restore-after-the-namespace-was-deleted).
- **Point-in-time recovery.** There is no transaction-log backup chain.

**Incrementality.** The staging PVC is a new volume at every backup, so the local snapshot is a full
copy each time. The export deduplicates by content. This was measured for MongoDB
([Step 6](../mongodb-logical-late-capture-example/README.md#step-6--export-deduplication)); it was
not measured for SQL Server.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.35.8` (EKS) |
| Kasten | `9.0.10`, `features.lateCapture=true` |
| Database | `SQL Server 2019 CU27` (`mcr.microsoft.com/mssql/server:2019-CU27-ubuntu-20.04`), Developer edition |
| Tools image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.10` ([Dockerfile](../images/kasten-tools/Dockerfile)) |

No operator and no Helm chart: plain manifests, as in the documentation example.

> Detect your own Kasten version with `helm ls -n kasten-io` (or, on OpenShift OLM,
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`).

**`sqlcmd` path.** The blueprint calls `/opt/mssql-tools/bin/sqlcmd`, which the SQL Server 2019
image ships. Recent SQL Server 2022 images ship `/opt/mssql-tools18/bin/sqlcmd` instead, which also
needs `-C` (trust the server certificate). Check with `ls /opt/mssql-tools*/bin` in your container
and adapt the `sqlcmd` commands in `blueprint.yaml`.

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, restore from it |
| `blueprintbinding.yaml` | Binds the blueprint to Deployments labelled `app: mssql` |
| `rbac.yaml` | ServiceAccount `mssql-logical-backup` with `pods/exec` in the application namespace |
| `policy.yaml` | On-demand policy with export that excludes the SQL Server data PVC and keeps the staging PVC |

---

## Prerequisites

- Kasten `9.0.10`+ with `features.lateCapture=true` (see the top of this page).
- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten.
- A **Location** profile for the policy (not an `Infra` profile): the `lateCapture` artifact is
  passed from `backupPrehook` to `restorePosthook`, which requires one.
- `rbac.yaml` applied in the application namespace (Step 2).
- The SQL Server container is named `mssql` and has the variable `SA_PASSWORD`.

> **Storage class**: `gp3` is the AWS EBS CSI storage class used in our test environment. It
> appears in the manifests below **and in `blueprint.yaml`** (the staging PVC spec).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on AKS, `standard-rwo` on GKE, your custom class on bare-metal, and so on).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

The staging PVC requests `2Gi`. Set the size in `blueprint.yaml` above the total size of your
`.bak` files.

---

## Step 2 — Deploy the workload and create test data

```bash
kubectl create namespace mssql-logical
kubectl create secret generic mssql --from-literal=SA_PASSWORD='MyC0m9l&xP@ssw0rd' -n mssql-logical
kubectl apply -n mssql-logical -f rbac.yaml

cat <<'EOF' | kubectl apply -n mssql-logical -f -
kind: PersistentVolumeClaim
apiVersion: v1
metadata:
  name: mssql-data
  labels:
    app: mssql
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: gp3
  resources:
    requests:
      storage: 8Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mssql-deployment
  labels:
    app: mssql
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: mssql
  template:
    metadata:
      labels:
        app: mssql
    spec:
      terminationGracePeriodSeconds: 30
      hostname: mssqlinst
      securityContext:
        fsGroup: 10001
      containers:
        - name: mssql
          image: mcr.microsoft.com/mssql/server:2019-CU27-ubuntu-20.04
          ports:
            - containerPort: 1433
          env:
            - name: MSSQL_PID
              value: "Developer"
            - name: ACCEPT_EULA
              value: "Y"
            - name: SA_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mssql
                  key: SA_PASSWORD
          volumeMounts:
            - name: mssqldb
              mountPath: /var/opt/mssql
      volumes:
        - name: mssqldb
          persistentVolumeClaim:
            claimName: mssql-data
---
apiVersion: v1
kind: Service
metadata:
  name: mssql-deployment
spec:
  selector:
    app: mssql
  ports:
    - protocol: TCP
      port: 1433
      targetPort: 1433
  type: ClusterIP
EOF
kubectl rollout status deploy/mssql-deployment -n mssql-logical
```

> **Storage class**: see the callout above — replace `gp3` with a CSI snapshot-capable class.

Create a small dataset (< 5 KB). `sqlcmd` runs inside the SQL Server container and reads the
password from its environment:

```bash
NS=mssql-logical
SQL() { kubectl exec -n $NS deploy/mssql-deployment -c mssql -- bash -c \
  "/opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P \"\$SA_PASSWORD\" -b -Q \"$1\""; }

SQL "CREATE DATABASE test"
SQL "USE test; CREATE TABLE inventory (id INT, name NVARCHAR(50), quantity INT);
     INSERT INTO inventory VALUES (1,'banana',150),(2,'orange',154),(3,'apple',100),(4,'kiwi',75);
     SELECT * FROM inventory"
```

Expected: 4 rows.

### Removing the workload

```bash
kubectl delete namespace mssql-logical
kubectl delete policy mssql-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=mssql-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

The same sequence as the blueprint, run with `kubectl` and a CSI `VolumeSnapshot` to stand in for
Kasten:

1. Create a PVC (`gp3`, `2Gi`).
2. Run a pod with the `kasten-tools` image, the ServiceAccount of `rbac.yaml`, user `1001` and
   `fsGroup: 1001`, that mounts it. It runs `BACKUP DATABASE [test] TO DISK = N'/tmp/mssql-backup/test.bak'`
   inside the SQL Server container, streams `/tmp/mssql-backup` to the PVC with `tar` over
   `kubectl exec`, deletes it from the container, then runs `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop the `test` database.
5. Create a PVC from the snapshot, stream the `.bak` files back into the container, run
   `RESTORE DATABASE [test] FROM DISK = … WITH REPLACE, CHECKSUM`.
6. The 4 rows are back.

Two findings from this step are now in the blueprint:

- **Archive the folder, not `.`.** `tar … -C /tmp/mssql-backup .` fails on the PVC side: extracting
  `.` tries to change the owner and mode of the PVC's root directory, which belongs to `root`. The
  blueprint archives the folder by name (`-C /tmp mssql-backup`).
- **The documentation's tools image ships kubectl 1.18** (2020). The SQL Server image already has
  `sqlcmd`, so the blueprint runs `sqlcmd` there and only needs `kubectl` and `tar` in its own pod:
  the shared `kasten-tools` image.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps/deployments` labelled `app: mssql`. A Deployment with an
explicit `kanister.kasten.io/blueprint` annotation keeps its own blueprint. To bind one Deployment
by hand instead:

```bash
kubectl annotate deployment mssql-deployment -n mssql-logical \
  kanister.kasten.io/blueprint=mssql-logical-late-capture-blueprint
```

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<deployment>-dump`. `PrepareData` (ServiceAccount `mssql-logical-backup`) mounts it, checks the free space in the SQL Server container's `/tmp`, runs `BACKUP DATABASE … WITH FORMAT, INIT, CHECKSUM` for every online user database there, streams the `.bak` files to the PVC with `tar` over `kubectl exec`, deletes them from `/tmp`, and runs `sync`. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<deployment>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact), waits up to 5 minutes for SQL Server to answer, streams the `.bak` files into the container's `/tmp`, runs `RESTORE DATABASE … WITH REPLACE, CHECKSUM` for each, and deletes them. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true` and runs with `set -euo pipefail`, and every
`sqlcmd` call uses `-b` (exit with an error code when a statement fails). With the default
`failOnError: false`, a failed `BACKUP` would still report success, and Kasten would snapshot an
empty PVC.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

`policy.yaml` excludes the PVCs labelled `app: mssql` — the data PVC. Two details matter:

- The rule is limited to `resource: persistentvolumeclaims`. The Deployment carries the same label;
  excluding it would remove the workload, and the blueprint would never run.
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
     generateName: run-mssql-logical-
     namespace: kasten-io
   spec:
     subject:
       kind: Policy
       name: mssql-logical-backup
       namespace: kasten-io
   EOF
   ```

   During the backup the PVC `mssql-deployment-dump` appears, becomes `Bound`, and is deleted. The
   restore point holds `mssql-deployment-dump` and no `mssql-data`.

2. **Delete the data:**

   ```bash
   SQL "DROP DATABASE test"
   ```

3. **Restore** (in place):

   ```bash
   kubectl get restorepoint -n mssql-logical
   kubectl create -f - --validate=false <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RestoreAction
   metadata:
     generateName: restore-
     namespace: mssql-logical
   spec:
     subject:
       apiVersion: apps.kio.kasten.io/v1alpha1
       kind: RestorePoint
       name: <RESTORE_POINT_NAME>
       namespace: mssql-logical
     targetNamespace: mssql-logical
   EOF
   ```

   Kasten restores the staging PVC, then `restorePosthook` restores the databases and deletes the
   PVC.

4. **Verify** — expect the 4 rows:

   ```bash
   SQL "SELECT * FROM test.dbo.inventory"
   ```

### Restore after the namespace was deleted

> ⚠️ **Manual step: create the empty data PVC.** A StatefulSet creates its missing PVCs from its
> `volumeClaimTemplates`; a Deployment only refers to a PVC by name. The policy excluded
> `mssql-data`, so the restore point does not contain it, and nothing recreates it: the SQL Server
> pod stays `Pending` with *"persistentvolumeclaim "mssql-data" not found"*, and the restore waits
> for it. Create the PVC (empty, from the Step 2 manifest) in the new namespace, before or during
> the restore.

> **The policy must export.** Kasten keeps the local `VolumeSnapshot` of the staging PVC in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`. Deleting the
> namespace deletes the EBS snapshot. Restore from the **exported** restore point.

```bash
kubectl delete namespace mssql-logical

kubectl create namespace mssql-logical
# The empty data PVC (see above). SQL Server initializes it on first start.
cat <<'EOF' | kubectl apply -n mssql-logical -f -
kind: PersistentVolumeClaim
apiVersion: v1
metadata:
  name: mssql-data
  labels:
    app: mssql
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: gp3
  resources:
    requests:
      storage: 8Gi
EOF

# The RestorePoint objects went with the namespace; the RestorePointContent remains.
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=mssql-logical,k10.kasten.io/exportType=portableAppData
kubectl create -f - <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata:
  name: mssql-logical-restored
  namespace: mssql-logical
spec:
  restorePointContentRef:
    name: <RESTORE_POINT_CONTENT_NAME>
EOF
```

Then run the same RestoreAction as above with `name: mssql-logical-restored`. Kasten restores the
Secret, the ServiceAccount and Role of `rbac.yaml`, the Deployment and the staging PVC; SQL Server
starts on the empty volume with the restored `SA_PASSWORD`, and `restorePosthook` restores the user
databases.

Tested on Kasten 9.0.10: the in-place restore (after checking that `test` was gone) and the restore
after deleting the namespace (from the exported restore point, with the data PVC created by hand)
both brought back the 4 rows, and a new backup after the second restore completed.

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n mssql-logical -o jsonpath='{.items[-1:].status.error}'
```

**"Not enough space in /tmp of the SQL Server container for the backup".** The data files are
larger than the free space on the node's disk seen by the container. See
[The backup passes through the node's disk](#the-backup-passes-through-the-nodes-disk): give the
node more disk, or move to a permanent backup PVC (pattern 3).

**`pods "…" is forbidden: … cannot create resource "pods/exec"`.** `rbac.yaml` is missing in the
application namespace, or the ServiceAccount has another name than the one in `blueprint.yaml`.

**The restore waits and the SQL Server pod is `Pending`.** The data PVC does not exist; see the
manual step in [Restore after the namespace was deleted](#restore-after-the-namespace-was-deleted).

**A backup failed after a cancelled or crashed one.** `backupPosthook` did not run, so the
staging PVC stayed behind. The next backup refuses to snapshot an old dump and fails with
*"Late-captured PVC was snapshotted before the pre-backup hook; its data in this restore point
would be stale"*. Its own `backupPosthook` still runs and deletes the PVC, so **the backup after
that succeeds** without any action. If the leftover PVC is `Pending` (never mounted), discovery
fails with *"PVC not bound"*; delete it by hand:

```bash
kubectl delete pvc mssql-deployment-dump -n mssql-logical
```

**`Late-captured PVC is excluded by the policy's resource filters`.** The policy filter matches the
staging PVC. Do not give the staging PVC the labels the filter uses.

**The backup succeeds but the restore finds no `.bak` file.** Check the feature flag (top of this
page).

---

## Initial prompt

> Port the Kasten documentation logical backup examples (MySQL, PostgreSQL, Microsoft SQL Server)
> to late capture, as was done for MongoDB. Don't measure deduplication. Test them one by one on
> EKS Paris.
