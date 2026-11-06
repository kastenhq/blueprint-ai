# Kasten Blueprint — Logical MongoDB Backup on a Staging PVC (Late Capture)

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

A logical backup (`mongodump` / `mongorestore`) of **MongoDB deployed with the Bitnami Helm chart**
in `replicaset` architecture. It is the late-capture version of the Kasten documentation example
[Logical MongoDB Backup](https://docs.kasten.io/latest/kanister/mongodb/install_logical).

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
| Images | `kanister-tools` + MongoDB image in a `MultiContainerRun` | MongoDB image only (`PrepareData`) |
| Password | Rendered into the pod command | Read from the Secret through `podOverride` env |
| Deleting the backup | Custom `delete` action | Kasten deletes the snapshot when the restore point expires |
| Binding | Annotation | BlueprintBinding (annotation still possible) |

## Pattern

**Pattern 6 — Logical dump on a staging PVC (late capture).** The requester chose this pattern:
the request was to convert the documentation example to late capture.

Why it fits: a `mongodump` archive is a **full** dump, so it does not need the previous backup on
the volume. A PVC that exists only during the backup is enough, and there is no keeper Deployment
to run or PVC to keep between backups (patterns 3–5). The blueprint is bound to the StatefulSet, so
it works with a BlueprintBinding and another workload type can share the namespace — which the
action-hook patterns 8–9 do not allow.

**Why late capture is needed.** Kasten discovers the PVCs to snapshot **before** `backupPrehook`
runs. A PVC created by `backupPrehook` is not discovered. The `backupPrehook` therefore *declares*
the PVC in an output artifact whose name starts with `lateCapture`. After the hook, Kasten reads
that artifact and adds the PVC to the snapshot set. See
[Late Capture](../kasten-kanister.md#late-capture--hook-created-resources-in-the-restore-point).

**What the backup protects:** the content of every database in the replica set, as of the end of
the dump (`--oplog` makes it a consistent point in time), plus every Kubernetes object in the
namespace (StatefulSets, Secret, Services, ConfigMaps).

**What it does not protect:** the MongoDB data PVCs. The policy excludes them, as in the
documentation example, where the `backup` action replaced volume snapshots. After a restore into an
empty namespace, MongoDB starts on **new, empty volumes** and the dump rebuilds the data.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.35.8` (EKS) |
| Kasten | `9.0.10`, `features.lateCapture=true` |
| Helm chart | `bitnami/mongodb 20.0.17` |
| Database | `MongoDB 8.0.11` (`bitnamilegacy/mongodb:8.0.11-debian-12-r1`), replica set `rs0`: 2 data members + 1 arbiter |

> Detect your own Kasten version with `helm ls -n kasten-io` (or, on OpenShift OLM,
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`).

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The blueprint: create and fill the staging PVC, delete it, replay it on restore |
| `blueprintbinding.yaml` | Binds the blueprint to every Bitnami MongoDB data StatefulSet (not the arbiter) |
| `policy.yaml` | On-demand policy that excludes the MongoDB data PVCs and keeps the staging PVC |

---

## Prerequisites

- Kasten `9.0.10`+ with `features.lateCapture=true` (see the top of this page).
- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten. The staging PVC must use `Filesystem` volume mode
  (the default).
- A **Location** profile for the policy (not an `Infra` profile): the `lateCapture` artifact is
  passed from `backupPrehook` to `restorePosthook`, which requires one.

> **Storage class**: `gp3` is the AWS EBS CSI storage class used in our test environment. It
> appears in the Helm command below **and in `blueprint.yaml`** (the staging PVC spec).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on AKS, `standard-rwo` on GKE, your custom class on bare-metal, and so on).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

The staging PVC requests `2Gi`. A `mongodump --gzip` archive is usually much smaller than the
data, but set the size in `blueprint.yaml` above the size of your largest dump.

### Bitnami image availability

Bitnami moved its free images to the `bitnamilegacy` Docker Hub repository. The commands below use
`bitnamilegacy/mongodb` and set `global.security.allowInsecureImages=true` (which disables the
chart's image-origin check), as the documentation example does. The blueprint uses the same image
for `mongodump` and `mongorestore`, so the tools always match the server version. If you change the
MongoDB version, change the image in `blueprint.yaml` too.

---

## Step 2 — Deploy the workload and create test data

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
kubectl create namespace mongodb-logical

helm install mongo-logical bitnami/mongodb --namespace mongodb-logical \
  --version 20.0.17 \
  --set architecture="replicaset" \
  --set global.defaultStorageClass=gp3 \
  --set global.security.allowInsecureImages=true \
  --set volumePermissions.image.repository=bitnamilegacy/os-shell \
  --set image.repository=bitnamilegacy/mongodb \
  --set image.tag=8.0.11-debian-12-r1 \
  --wait
```

> **Storage class**: see the callout above — replace `gp3` with a CSI snapshot-capable class.

Create a small dataset (< 5 KB):

```bash
NS=mongodb-logical
PW=$(kubectl get secret -n $NS mongo-logical-mongodb -o jsonpath='{.data.mongodb-root-password}' | base64 -d)
kubectl exec -n $NS mongo-logical-mongodb-0 -c mongodb -- mongosh --quiet -u root -p "$PW" --eval '
db = db.getSiblingDB("test");
db.restaurants.insertMany([
 {name:"Le Petit Zinc", borough:"Paris 6", cuisine:"French"},
 {name:"Chez Janou", borough:"Paris 3", cuisine:"Provencal"},
 {name:"Pho 14", borough:"Paris 13", cuisine:"Vietnamese"},
 {name:"Breizh Cafe", borough:"Paris 3", cuisine:"Creperie"}]);
db.restaurants.countDocuments()'
```

Expected: `4`. Run the command on `mongo-logical-mongodb-0` only while it is the primary
(`rs.status()`); otherwise use the primary pod.

### Removing the workload

```bash
helm uninstall mongo-logical -n mongodb-logical
kubectl delete pvc -n mongodb-logical --all        # Helm leaves the data PVCs
kubectl delete namespace mongodb-logical
kubectl delete policy mongodb-logical-backup -n kasten-io
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=mongodb-logical
```

---

## Step 3 — What was validated by hand before writing the blueprint

The same sequence as the blueprint, run with `kubectl` and a CSI `VolumeSnapshot` to stand in for
Kasten:

1. Create the PVC `mongo-logical-mongodb-dump` (`gp3`, `2Gi`).
2. Run a pod with `bitnamilegacy/mongodb:8.0.11-debian-12-r1` that mounts it and runs
   `mongodump --host rs0/<pod-0>,<pod-1> --oplog --gzip --archive=/backup/rs_backup.gz`, then `sync`.
3. Snapshot the PVC, wait until `readyToUse`, delete the PVC.
4. Drop `test.restaurants`.
5. Create a PVC from the snapshot, run `mongorestore --oplogReplay --drop --gzip --archive=…`.
6. The 4 documents are back.

Two findings from this step are now in the blueprint:

- **The pod must run with `fsGroup: 1001`.** A new EBS volume is owned by `root`, and the Bitnami
  image runs as user `1001`, so `mongodump` cannot create the archive otherwise.
- **Connect to the replica set, not to pod 0.** The documentation example connects to
  `<sts>-0`, which is not always the primary. The blueprint builds the seed list from the
  StatefulSet pods (`rs0/<pod-0>.<sts>-headless:27017,<pod-1>…`), and the driver finds the primary.

---

## Step 4 — Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The BlueprintBinding matches `apps/statefulsets` labelled `app.kubernetes.io/name=mongodb` **and**
`app.kubernetes.io/component=mongodb`. The arbiter StatefulSet (`component=arbiter`) holds no data
and is not bound. A StatefulSet with an explicit `kanister.kasten.io/blueprint` annotation keeps
its own blueprint. To bind one StatefulSet by hand instead:

```bash
kubectl annotate statefulset mongo-logical-mongodb -n mongodb-logical \
  kanister.kasten.io/blueprint=mongodb-logical-late-capture-blueprint
```

The blueprint assumes the Bitnami chart's conventions: the Secret is named after the StatefulSet
with the key `mongodb-root-password`, the headless Service is `<sts>-headless`, and the replica set
is `rs0` (`REPLICA_SET` in the scripts). Adapt them if your release differs.

### Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshots | `KubeOps` creates the PVC `<sts>-dump`. `PrepareData` mounts it, runs `mongodump --oplog --gzip --archive` against the replica set, runs `sync`, and checks the archive is not empty. The `lateCapture` output artifact declares the PVC, so Kasten snapshots it. |
| `backupPosthook` | After the PVC snapshots | Deletes the PVC `<sts>-dump`. Kasten fails the backup if it still exists. |
| `restorePosthook` | After Kasten restored the objects and the workloads are ready | `PrepareData` mounts the restored PVC (name from the `lateCapture` artifact) and runs `mongorestore --oplogReplay --drop`, retrying for up to 5 minutes while the replica set elects a primary. Then deletes the restored PVC — otherwise the next backup fails on it. |

Every `PrepareData` phase sets `failOnError: true`. With the default (`false`), a failed
`mongodump` would still report success, and Kasten would snapshot an empty archive.

### The policy

```bash
sed 's/<LOCATION_PROFILE>/<your location profile>/' policy.yaml | kubectl apply -f -
```

`policy.yaml` excludes the PVCs labelled `app.kubernetes.io/name=mongodb` and
`app.kubernetes.io/component=mongodb` — the data PVCs. Two details matter:

- The rule is limited to `resource: persistentvolumeclaims`. The StatefulSet carries the same
  labels; excluding it would remove the workload, and the blueprint would never run.
- The staging PVC does **not** carry these labels. Late capture fails the backup if the policy
  excludes a declared PVC.

The policy also **exports** the restore point to the same location profile. The local snapshot is
deleted with the namespace; the exported copy is what a restore after the loss of the namespace
or the cluster uses.

If you prefer to keep the data PVCs as well (a crash-consistent copy next to the dump), remove the
filter. The restore then replaces the volumes **and** replays the dump.

---

## Step 5 — End-to-end test through Kasten

1. **Run the policy** (UI, or a `RunAction`):

   ```bash
   kubectl create -f - <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RunAction
   metadata:
     generateName: run-mongodb-logical-
   spec:
     subject:
       kind: Policy
       name: mongodb-logical-backup
       namespace: kasten-io
   EOF
   ```

   During the backup the PVC `mongo-logical-mongodb-dump` appears, becomes `Bound`, and is deleted.
   The executor log shows `Late capture: PVC added to spec set and snapshot session`:

   ```bash
   kubectl logs -n kasten-io -l component=executor --tail=10000 | grep -i 'late capture'
   ```

   The restore point holds `mongo-logical-mongodb-dump` (spec and snapshot) and no `datadir-*` PVC.

2. **Delete the data:**

   ```bash
   kubectl exec -n mongodb-logical mongo-logical-mongodb-0 -c mongodb -- \
     mongosh --quiet -u root -p "$PW" --eval 'db.getSiblingDB("test").restaurants.drop()'
   ```

3. **Restore** (in place):

   ```bash
   kubectl get restorepoint -n mongodb-logical
   kubectl create -f - --validate=false <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RestoreAction
   metadata:
     generateName: restore-
     namespace: mongodb-logical
   spec:
     subject:
       apiVersion: apps.kio.kasten.io/v1alpha1
       kind: RestorePoint
       name: <RESTORE_POINT_NAME>
       namespace: mongodb-logical
     targetNamespace: mongodb-logical
   EOF
   ```

   Kasten restores the staging PVC, restarts the StatefulSets, then `restorePosthook` replays the
   dump and deletes the PVC.

4. **Verify** — expect the 4 restaurants:

   ```bash
   kubectl exec -n mongodb-logical mongo-logical-mongodb-0 -c mongodb -- \
     mongosh --quiet -u root -p "$PW" --eval 'db.getSiblingDB("test").restaurants.find({}, {_id:0, name:1}).toArray()'
   ```

### Restore after the namespace was deleted

This is the case that matters for this blueprint: the data PVCs are not in the restore point, so
MongoDB starts on **new, empty volumes**, initialises a new replica set with the restored Secret
(same root password), and `restorePosthook` loads the dump into it.

> **The policy must export.** Kasten keeps the local `VolumeSnapshot` of the staging PVC in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`. Deleting the
> namespace deletes the EBS snapshot, and a restore from the local restore point then fails with
> *"can not find snapshot for snapshotID"*. This is how Kasten handles every PVC, not something
> specific to late capture. Add an `export` action to the policy and restore from the exported
> restore point.

```bash
kubectl delete namespace mongodb-logical

# The RestorePoint objects went with the namespace; the RestorePointContent remains.
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=mongodb-logical
kubectl create namespace mongodb-logical
kubectl create -f - <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata:
  name: mongodb-logical-restored
  namespace: mongodb-logical
spec:
  restorePointContentRef:
    name: <RESTORE_POINT_CONTENT_NAME>
EOF
```

Then run the same RestoreAction as above with `name: mongodb-logical-restored`. Use the
restore point content of an **exported** restore point (the one created by the export action,
named `<namespace>-scheduled-<id><suffix>`).

Tested on Kasten 9.0.10: after deleting the namespace, the restore from the exported restore point
created new data PVCs, MongoDB formed a new replica set, and `restorePosthook` loaded the dump —
the 4 restaurants and all 40,800 records of the Step 6 dataset came back.

> **Restore under the same namespace name.** The Bitnami chart writes the release namespace into
> its `setup.sh` ConfigMap (`<sts>-scripts`), in the command that looks for the current primary
> (`…mongodb-logical.svc.cluster.local`). Restored into a namespace with another name, the
> secondary tries to join the replica set of the **original** namespace and never becomes ready, so
> Kasten never reaches `restorePosthook`. This is a property of the chart, not of the blueprint.
> To restore under another name, add a Kasten transform that rewrites the namespace in that
> ConfigMap.

---

## Step 6 — Export deduplication

The staging PVC is a new volume at every backup, so the local EBS snapshot is a full copy each
time. The question is whether the **export** to the location profile is incremental: Kopia cuts
the data into chunks by content (about 4 MB on average) and uploads only chunks it has not seen.

**Dataset:** 40,000 records of 512 random bytes (base64), generated with
`crypto.randomBytes` in `mongosh` so that records do not compress or deduplicate artificially —
27.2 MiB of BSON. **Change:** 1% of the records (400), either **added** (new `_id`s, written at the
end of the collection) or **updated** in place at random positions. Growth is the size of
`k10/<cluster-uid>/migration/repo/<namespace-uid>/` in the bucket before and after the export.

| Dump format | Dump size | Change | Export growth | Share of the dump |
|---|---|---|---|---|
| `--gzip` | 20.9 MB | 1% updated at random | 20.86 MB | **100%** |
| uncompressed | 28.5 MB | 1% added | 3.56 MB | **12%** |
| uncompressed | 28.5 MB | 1% updated at random | 28.78 MB | **101%** |
| `--gzip` | 21.1 MB | 1% added | 2.55 MB | **12%** |

The companion repository `migration/<policy>/kopia/` grew by about 0.3 MB per export in every case.
Kasten 9.0.10 reported no `progressDetails` on these export actions, so only the bucket growth is
available.

What this means:

- **Where the changes are decides everything, not the format.** 400 updates spread over 27 MB
  touch every 4 MB chunk, so every chunk is new and the whole dump is uploaded again. 400 added
  records sit at the end of the archive, so only the last chunks change.
- **`--gzip` does not prevent deduplication.** gzip produces the same output for the same input
  prefix, so added records deduplicate as well as with the uncompressed archive (12%), and the
  dump is 26% smaller. The blueprint keeps `--gzip`.
- **For a database updated across the whole collection, assume every backup exports the full
  dump.** Backup time and export size then grow with the database. Patterns that snapshot the
  database volumes (quiesce, fence a replica) do not have this cost.

---

## Troubleshooting

```bash
kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl get backupaction -n mongodb-logical -o jsonpath='{.items[-1:].status.error}'
```

**A backup failed after a cancelled or crashed one.** `backupPosthook` did not run, so the
staging PVC stayed behind. The next backup refuses to snapshot an old dump and fails with
*"Late-captured PVC was snapshotted before the pre-backup hook; its data in this restore point
would be stale"*. Its own `backupPosthook` still runs and deletes the PVC, so **the backup after
that succeeds** without any action (tested). Run the policy again, or wait for the next schedule.
The same applies to a restored staging PVC left by an interrupted restore.

If the leftover PVC is `Pending` (never mounted), discovery fails with *"PVC not bound"*; delete it
by hand:

```bash
kubectl delete pvc mongo-logical-mongodb-dump -n mongodb-logical
```

**`Late-captured PVC is excluded by the policy's resource filters`.** The policy filter matches the
staging PVC. Do not give the staging PVC the labels the filter uses.

**The backup succeeds but the restore finds no `rs_backup.gz`.** Check the feature flag
(top of this page).

---

## Initial prompt

> Try to convert this example https://docs.kasten.io/latest/kanister/mongodb/install_logical with
> late capture. Use the EKS Paris cluster.
