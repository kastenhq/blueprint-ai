# InfluxDB 1.11 — Kasten blueprint (stop, snapshot, start)

Back up a **single-replica InfluxDB 1.x OSS** `Deployment` with a clean on-disk state. The
blueprint stops InfluxDB just before Kasten snapshots its PVC, and starts it again as soon as the
snapshot is taken. Kasten is the data mover: the blueprint moves no data.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.34.1` (Talos `1.12.6`, bare metal) |
| Kasten | `9.0.8` (`helm ls -n kasten-io`) |
| InfluxDB | `1.11.8` OSS (`influxdb:1.11.8`) |
| Storage | Rook `1.19.5`, Ceph `19.2.0`, RBD (`ceph-block`), VolumeSnapshotClass `rook-ceph-block-snapshot` |
| Tool image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.8` ([Dockerfile](../../images/kasten-tools/Dockerfile)) |

No operator: InfluxDB is a plain `Deployment`.

## Pattern

**Pattern 2 — Quiesce** ([kasten-kanister.md](../../kasten-kanister.md#2-quiesce)), done by
stopping the process.

InfluxDB 1.x has no command that pauses writes and flushes them to disk (nothing like
`FLUSH TABLES WITH READ LOCK` or `fsyncLock`). The only way to get a state on disk that no
process is changing is to stop InfluxDB. The blueprint scales the Deployment to 0, waits until
the pod is deleted, lets Kasten snapshot the PVC, then scales it back.

| | |
|---|---|
| Consistency | Clean: no process has the files open when the snapshot is taken |
| Downtime | About 20 s per backup in our tests (stop 3 s, snapshot 6 s, start 11 s) |
| Restore | Kasten restores the PVC; InfluxDB starts on it. No restore script |
| Incrementality | See [Step 6](#step-6--export-deduplication) |
| Granularity | All databases of the instance together; no single-database restore |

Patterns ruled out:

| Pattern | Why not |
|---|---|
| 1 — Fence a replica | InfluxDB 1.x OSS has no replication |
| 3 — `influxd backup -portable` to a PVC mounted by InfluxDB | Works without downtime, but changes the workload and needs a restore script. Planned as a separate blueprint |
| 4 — Keeper running `influxd backup` | The backup port `8088` listens on `127.0.0.1` only and has no authentication; opening it to the namespace weakens security |
| 6 — Staging PVC with late capture | Needs Kasten `9.0.11` or later |

> **Downtime.** Clients that write to InfluxDB during the backup get connection errors for about
> 20 seconds. Make sure your writers retry or catch up. A collector that pulls from an external
> API on a schedule (for example Garmin, weather, or energy meters) usually catches up on its
> next run. If you cannot accept any write downtime, use a pattern that backs up online
> (pattern 3).

### What the backup protects

| Protected | Not protected |
|---|---|
| Everything under `/var/lib/influxdb`: `data/` (TSM files), `wal/`, `meta/` (databases, users, retention policies, continuous queries) | Writes sent while InfluxDB is stopped, if the client does not retry |
| The Deployment, Service, Secret and PVC of the namespace (Kasten backs up the namespace) | Anything outside the namespace, for example a Grafana in another namespace |

> **The WAL matters.** In Step 3, after a clean shutdown, the 20 test points were still only in
> the WAL (`wal/testdb/autogen/2/_00001.wal`) and were replayed from it at start. InfluxDB keeps
> recent writes in the WAL and moves them into TSM files later. A backup that copied only `data/`
> would lose them. Kasten snapshots the whole PVC, so `wal/` is included.

---

## Step 2 — Deploy InfluxDB and create test data

```bash
kubectl create namespace influxdb-simple-test
kubectl create secret generic influxdb-credentials -n influxdb-simple-test \
  --from-literal=INFLUXDB_ADMIN_USER=admin \
  --from-literal=INFLUXDB_ADMIN_PASSWORD="$(openssl rand -hex 12)"
kubectl apply -n influxdb-simple-test -f influxdb.yaml
kubectl rollout status deploy/influxdb -n influxdb-simple-test
```

[influxdb.yaml](influxdb.yaml) has the same shape as a typical InfluxDB 1.x Helm deployment: one
`Deployment` (`influxdb:1.11.8`, auth enabled, database `testdb`), one RWO PVC, one Service.

> **Storage class**: `ceph-block` is the Rook Ceph RBD class used in our test environment.
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `ebs-sc` on EKS, `managed-csi` on AKS, `standard-rwo` on GKE).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

Write 20 known points and check them:

```bash
./seed.sh       # 10 heart_rate points, 10 steps points, fixed timestamps
./verify.sh     # expected: heart_rate count 10 sum 655, steps count 10 sum 5500
```

---

## Step 3 — Manual validation (no blueprint)

The same sequence the blueprint runs, by hand:

```bash
NS=influxdb-simple-test
kubectl scale deploy/influxdb -n $NS --replicas=0
kubectl wait --for=delete pod -l app=influxdb -n $NS --timeout=120s
kubectl apply -n $NS -f - <<'EOF'
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata: {name: influxdb-manual}
spec:
  volumeSnapshotClassName: rook-ceph-block-snapshot
  source: {persistentVolumeClaimName: influxdb}
EOF
kubectl wait volumesnapshot/influxdb-manual -n $NS --for=jsonpath='{.status.readyToUse}'=true
kubectl scale deploy/influxdb -n $NS --replicas=1
```

Then `DROP DATABASE testdb`, scale to 0, delete the PVC, re-create it with
`dataSource: {kind: VolumeSnapshot, name: influxdb-manual}`, scale to 1 and run `./verify.sh`.

**Result:** the 20 points came back (count 10 / sum 655 and count 10 / sum 5500). Downtime was
20 s: stop 3 s, snapshot ready 6 s, start 11 s.

**Why waiting for the pod to be deleted is enough to flush:** Kubernetes removes the pod object
only after the kubelet has unmounted its volumes, and unmounting writes all cached data to the
block device. Once the pod is gone, every write InfluxDB acknowledged is on the device. No
separate `sync` is needed (and none is possible, as no pod mounts the volume any more).

---

## Step 4 — The blueprint

### Install

```bash
kubectl create configmap influxdb-simple-scripts -n kasten-io --from-file=scripts/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f blueprint.yaml -f blueprintbinding.yaml
```

Then opt in each InfluxDB Deployment with a label:

```bash
kubectl label deploy <influxdb-deployment> -n <namespace> blueprint-ai/influxdb-simple=true
```

[blueprintbinding.yaml](blueprintbinding.yaml) binds the blueprint to every Deployment with that
label, in any namespace. The label is an explicit opt-in on purpose: this blueprint **stops** the
workload it is bound to, so it must never match a Deployment by accident.

> **Helm-managed InfluxDB.** If your chart does not set this label, a later `helm upgrade` may
> remove it. After an upgrade, check it is still there:
> `kubectl get deploy -n <namespace> -l blueprint-ai/influxdb-simple=true`. A Deployment
> without the label is still backed up by the policy, **without** the stop (a crash-consistent
> snapshot).

The scripts are in [scripts/](scripts/) and reach the pods through the ConfigMap
`influxdb-simple-scripts`. After changing a script, re-run the `kubectl create configmap` command
above. Each run logs a fingerprint first (`influxdb-simple scripts <hash>`), so you can tell which
version of `scripts/` ran.

### Blueprint actions

| Action | Phase | What it does |
|---|---|---|
| `backupPrehook` | `stopInfluxDB` ([stop.sh](scripts/stop.sh)) | Saves the replica count in the annotation `blueprint-ai/influxdb-replicas`, creates the watchdog Job, scales the Deployment to 0 and waits until its pods are deleted. Fails if a pod is still there after 300 s |
| `backupPosthook` | `startInfluxDB` ([start.sh](scripts/start.sh)) | Scales the Deployment back to the saved count, waits until it is ready, removes the annotation, deletes the watchdog Job |
| `restorePosthook` | `startInfluxDB` ([start.sh](scripts/start.sh)) | Same script. If the restored Deployment has the annotation, scales it to the saved count; then waits until InfluxDB is ready |

There is no `backup` or `restore` action: Kasten snapshots and restores the PVC.

### The watchdog: a cancelled backup must not leave InfluxDB down

Tested on Kasten `9.0.8`: when a `BackupAction` is **cancelled** while InfluxDB is stopped,
Kasten does not always run `backupPosthook` to the end. Three tests gave two outcomes:

| Test | What Kasten did after the cancel | InfluxDB without the watchdog |
|---|---|---|
| 1 | Did not run `backupPosthook` | **Stayed down** |
| 2 | Started `backupPosthook`; it was stopped during the readiness wait | Up, stale annotation left |
| 3 | Did not run `backupPosthook` | **Stayed down** (watchdog started it after 60 s) |

So before it stops InfluxDB, `stop.sh` creates a **watchdog Job** in `kasten-io`
(`influxdb-watchdog-<hash of namespace/name>`). The Job sleeps, then:

- if the Deployment is still at 0 replicas with the annotation, it scales it back and removes the annotation;
- if it is already running but the annotation is still there, it removes the annotation;
- otherwise it does nothing.

A normal `backupPosthook` deletes the Job before it fires. The Job uses the same service account
and image as the `KubeTask` pod that creates it, and mounts the same ConfigMap.

The delay is **600 s** by default. To change it for one Deployment:

```bash
kubectl annotate deploy <influxdb-deployment> -n <namespace> blueprint-ai/influxdb-watchdog-seconds=900
```

Set it longer than your longest backup from stop to snapshot ready. If the watchdog fires while
a slow snapshot is still being taken, InfluxDB starts during the snapshot: the backup is then
crash-consistent instead of clean (InfluxDB replays its WAL at start), not lost.

A failed backup (rather than a cancelled one) was not tested. The watchdog covers it the same
way.

### Restore

No manual step is needed before a restore. Kasten scales the Deployment down, restores the PVC,
and brings the Deployment back; `restorePosthook` then makes sure InfluxDB is running. In our
tests the restored Deployment never had the annotation (Kasten had captured it with 1 replica),
so `restorePosthook` only waited for readiness. The scale-up branch is there in case the
Deployment is captured while the prehook has it at 0.

---

## Step 5 — End-to-end test through Kasten

### Policy

[policy.yaml](policy.yaml): on demand, snapshot then export to the Location profile
`kasten-backups` (replace with yours).

> **Ship the policy with an export.** The local snapshot is a `VolumeSnapshot` in the
> application namespace, with the `Delete` policy of the `VolumeSnapshotClass`: deleting the
> namespace deletes it. Only the exported restore point survives that.

```bash
kubectl apply -f policy.yaml
kubectl label deploy influxdb -n influxdb-simple-test blueprint-ai/influxdb-simple=true
kubectl create -f - <<'EOF'
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RunAction
metadata: {generateName: run-influxdb-simple-, namespace: kasten-io}
spec: {subject: {kind: Policy, name: influxdb-simple-test-backup, namespace: kasten-io}}
EOF
```

Watch the hooks in the Kanister log (`Pod_Out` field):

```bash
kubectl logs -n kasten-io deploy/kanister-svc --tail=2000 | grep -E '"Phase":"(stop|start)InfluxDB"' \
  | grep -oE '"Pod_Out":"[^"]*"'
```

### Restore in place

```bash
kubectl exec -n influxdb-simple-test deploy/influxdb -- sh -c \
  'influx -username "$INFLUXDB_ADMIN_USER" -password "$INFLUXDB_ADMIN_PASSWORD" -execute "DROP DATABASE testdb"'
kubectl get restorepoint -n influxdb-simple-test
kubectl create -f - <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata: {generateName: restore-, namespace: influxdb-simple-test}
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: <RESTORE_POINT_NAME>
    namespace: influxdb-simple-test
  targetNamespace: influxdb-simple-test
EOF
./verify.sh
```

### Restore after the namespace was deleted

```bash
kubectl delete namespace influxdb-simple-test
# The RestorePoint objects went with the namespace; the exported RestorePointContent remains.
kubectl get restorepointcontent \
  -l k10.kasten.io/appNamespace=influxdb-simple-test,k10.kasten.io/exportType=portableAppData
kubectl create namespace influxdb-simple-test
kubectl create -f - <<EOF
apiVersion: apps.kio.kasten.io/v1alpha1
kind: RestorePoint
metadata: {name: influxdb-restored, namespace: influxdb-simple-test}
spec:
  restorePointContentRef: {name: <RESTORE_POINT_CONTENT_NAME>}
EOF
```

Then run the same `RestoreAction` with `name: influxdb-restored`.

### Result

Tested on Kasten `9.0.8`:

| Test | Result |
|---|---|
| Backup + export | `Complete`. Prehook stopped InfluxDB, posthook started it; annotation and watchdog removed |
| Restore in place after `DROP DATABASE testdb` | `Complete`, 20 points back, `restorePosthook` ran |
| Restore after `kubectl delete namespace`, from the exported restore point | `Complete`, 20 points back, label and Deployment restored |
| New backup after the namespace restore | `Complete` |
| Cancel while InfluxDB is stopped | Watchdog started InfluxDB (see [the watchdog](#the-watchdog-a-cancelled-backup-must-not-leave-influxdb-down)) |

---

## Step 6 — Export deduplication

Kasten exports the snapshot of the InfluxDB PVC to object storage with Kopia, which stores
each piece of content once. The question is how much a new backup adds when only a little data
has changed.

### Dataset

[bulk-load.sh](bulk-load.sh) writes 3,000,000 points with random float values into a database
`bench` (8 series, one point every 10 s per series, 43 days, 7 shards of the default 7-day shard
duration). Random values matter: identical values compress and deduplicate far better than real
data.

```bash
./bulk-load.sh influxdb-simple-test 3000000 1764547200      # 2025-12-01, 3,000,000 points
# wait until /var/lib/influxdb/wal/bench is small (InfluxDB compacts a shard ~10 min after its last write)
./bulk-load.sh influxdb-simple-test 30000 1768297200        # +1%, newer timestamps
```

On disk after compaction: **44 MB** of TSM files for `bench`.

The change is an **append** of 1% new points after the existing ones, which is how time-series
data grows. InfluxDB writes them first to the WAL, then about 10 minutes later compacts them into
a new TSM file in the newest shard. The TSM files of the older shards did not change (same files,
same timestamps). We backed up at both moments.

### Measurement

Repository growth: sum of `migration/repo/<namespace-uid>/` and
`migration/influxdb-simple-test-backup/kopia/` in the bucket. `transferredBytes` was not reported
in the export action status on Kasten `9.0.8`.

| Backup | Change since the previous backup | Repository size | Growth |
|---|---|---|---|
| Before Step 6 | (20 test points only) | 0.40 MiB | — |
| 1 | +3,000,000 points, compacted | 44.59 MiB | 44.19 MiB |
| 2 | +30,000 points (1%), still in the WAL | 46.87 MiB | **2.28 MiB (5.1%)** |
| 3 | none from clients; the 1% compacted into a new 0.45 MB TSM file | 49.44 MiB | **2.57 MiB (5.8%)** |
| 4 | none | 49.99 MiB | 0.56 MiB (1.3%) |

### What the numbers mean

- **Old data is exported once.** Shards that receive no new writes keep the same TSM files,
  and Kopia stores them only once. This is what makes the pattern cheap as the history grows:
  a year of data costs the same per backup as a month.
- **A 1% append cost about 5% per backup, twice:** once as WAL, once more after compaction
  rewrote it into TSM files. Over the two backups, about 4.9 MiB were exported for about 0.5 MB
  of compressed new data. The rest was not isolated. It is not InfluxDB's own `_internal`
  monitoring data alone: a backup with no change at all (backup 4) grew by 0.56 MiB only.
- **Expect the export to grow with the write rate, not with the database size.** For a
  collector such as a Garmin poller that writes a few thousand points a day, the growth per
  backup should be small. Measure it on your own data with the same procedure.
- The **local** snapshot is a Ceph RBD snapshot, which only stores changed blocks.

---

## Apply it to an existing InfluxDB

1. Check that the PVC's storage class has a `VolumeSnapshotClass` with the annotation
   `k10.kasten.io/is-snapshot-class: "true"`.
2. Install the ConfigMap, the blueprint and the binding ([Install](#install)).
3. **Before** labelling the production Deployment, run a first backup **without** the label:
   it gives you a crash-consistent restore point to fall back on, and it shows how long a
   snapshot takes on your storage (set `blueprint-ai/influxdb-watchdog-seconds` above that).
4. Label the Deployment, run a backup, and watch your writers' logs during the ~20 s of downtime.
5. Restore into a **new namespace** (Kasten `targetNamespace`) and check the data there, instead
   of restoring over the production namespace.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| InfluxDB at 0 replicas after a backup | Backup cancelled or failed, posthook did not run; the watchdog has not fired yet | Wait for the watchdog (`kubectl get job -n kasten-io -l blueprint-ai/influxdb-watchdog=true`), or scale up by hand with the value in the `blueprint-ai/influxdb-replicas` annotation |
| `ERROR: N pod(s) still present` in the prehook | A pod did not terminate within 300 s (stuck volume unmount) | Investigate the pod; the watchdog starts InfluxDB again |
| Backup completes but the prehook did not run | The Deployment does not have the `blueprint-ai/influxdb-simple=true` label, or has a `kanister.kasten.io/blueprint` annotation | Check the label |
| The Deployment was at 0 before the backup | Stopped by someone else | The prehook logs `stopped outside this blueprint` and leaves it stopped |

---

## Clean up

```bash
kubectl delete ns influxdb-simple-test
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=influxdb-simple-test --wait=false
kubectl delete job -n kasten-io -l blueprint-ai/influxdb-watchdog=true
kubectl delete -f policy.yaml -f blueprintbinding.yaml -f blueprint.yaml
kubectl delete configmap influxdb-simple-scripts -n kasten-io
```

---

## Initial prompt

> I would like to use this repository to create a blueprint that will work with Kasten, Kanister
> to protect influxdb, I have this running in my cluster today with important data in so would
> like to be careful when protecting this data but would like to explore the options we have
> available to protect this in a consistent state.
