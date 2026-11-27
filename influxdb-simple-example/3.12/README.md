# InfluxDB 3 Core 3.12 — Kasten blueprint (stop, snapshot, start)

Back up a **single-node InfluxDB 3 Core** `Deployment` that uses the `file` object store on a PVC.
The blueprint stops InfluxDB just before Kasten snapshots the PVC, and starts it again as soon as
the snapshot is taken. Kasten is the data mover: the blueprint moves no data.

The blueprint, the scripts and the binding are **the same files** as in [1.11/](../1.11/): they
only scale a Deployment, so they do not depend on the InfluxDB version. What is specific to
InfluxDB 3 is the test deployment, the test scripts and the results below.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.34.1` (Talos `1.12.6`, bare metal) |
| Kasten | `9.0.8` (`helm ls -n kasten-io`) |
| InfluxDB | `3.12.3` Core (`influxdb:3.12.3-core`), object store `file` |
| Storage | Rook `1.19.5`, Ceph `19.2.0`, RBD (`ceph-block`), VolumeSnapshotClass `rook-ceph-block-snapshot` |
| Tool image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.8` ([Dockerfile](../../images/kasten-tools/Dockerfile)) |

No operator: InfluxDB is a plain `Deployment`.

## Pattern

**Pattern 2 — Quiesce** ([kasten-kanister.md](../../kasten-kanister.md#2-quiesce)), done by
stopping the process. A lighter variant without downtime (`sync` only) was tested first and
**rejected**: see [Why not just `sync`](#why-not-just-sync).

How InfluxDB 3 Core stores data, as far as it matters for a snapshot:

| | |
|---|---|
| Files | WAL files (`wal/`), Parquet files (`dbs/`), snapshot files (`snapshots/*.info.json`), catalog (`catalog/`), all under `--data-dir` |
| When a write is acknowledged | After the WAL file is written, every `--wal-flush-interval` (1 s by default) |
| Backup command | None (the CLI has no `backup` or `restore`) |
| Command to pause writes | None |
| Empty or damaged WAL file at start | **Skipped** with a warning, unless `--wal-replay-fail-on-error` |
| Empty snapshot file at start | **The server does not start** |

| | Result |
|---|---|
| Consistency | Clean: no process has the files open when the snapshot is taken |
| Downtime | 24–35 s per backup in our tests (InfluxDB 3 replays its WAL at start) |
| Restore | Kasten restores the PVC; InfluxDB starts on it. No restore script |
| Granularity | All databases together |

Patterns ruled out:

| Pattern | Why not |
|---|---|
| 1 — Fence a replica | Core is single-node |
| 2b — `sync` in the pod, no stop | Tested: 5 restored copies out of 12 did not start. See below |
| 3 / 4 / 6 — dump to a PVC | No backup tool. A per-table export through `influxdb3 query` loses tokens, triggers, plugins and retention settings, and is a full copy every time |
| Object store on S3 | Not covered: Kasten would not move the data. Protect the bucket (versioning, replication) instead |

> **Downtime.** Clients that write during the backup get connection errors for 24–35 seconds.
> In our tests a writer that retried each failed request lost nothing: every point it wrote
> before the stop was in the restored copy. A writer that does not retry loses what it sends
> during the stop.

### What the backup protects

| Protected | Not protected |
|---|---|
| Everything under `--data-dir`: WAL, Parquet, snapshot and catalog files, tokens, databases, retention settings | Writes sent while InfluxDB is stopped, if the client does not retry |
| The Deployment, Service and Secret(s) of the namespace | An object store outside the PVC (S3, GCS, Azure) |

---

## Step 2 — Deploy InfluxDB 3 and create test data

```bash
kubectl create namespace influxdb3-simple-test
kubectl apply -n influxdb3-simple-test -f influxdb3.yaml
kubectl rollout status deploy/influxdb3 -n influxdb3-simple-test

# Create the admin token once, keep it in a Secret, restart so the pod gets it as INFLUXDB3_AUTH_TOKEN.
TOKEN=$(kubectl exec -n influxdb3-simple-test deploy/influxdb3 -- \
  influxdb3 create token --admin --host http://127.0.0.1:8181 | grep -oE 'apiv3_[A-Za-z0-9_-]+' | head -1)
kubectl create secret generic influxdb3-admin-token -n influxdb3-simple-test --from-literal=token="$TOKEN"
unset TOKEN
kubectl rollout restart deploy/influxdb3 -n influxdb3-simple-test
kubectl rollout status deploy/influxdb3 -n influxdb3-simple-test

./seed.sh       # 10 heart_rate points, 10 steps points, fixed timestamps
./verify.sh     # expected: heart_rate 10 points sum 655, steps 10 points sum 5500
```

[influxdb3.yaml](influxdb3.yaml): one `Deployment` (`influxdb3 serve --object-store=file`), one RWO
PVC, one Service. `--disable-authz=health,ping` lets the readiness probe call `/health` without a
token; every other endpoint needs one.

> **Storage class**: `ceph-block` is the Rook Ceph RBD class used in our test environment.
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `ebs-sc` on EKS, `managed-csi` on AKS, `standard-rwo` on GKE).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

---

## Step 3 — Manual validation (no blueprint)

### The test

[writer.yaml](writer.yaml) runs a writer that sends 10 points per request, with consecutive
`seq` values, to the database `stress`, and records the highest `seq` InfluxDB acknowledged. A
restored copy is **consistent** if it starts, holds `seq` 1..N with no gap, and N is at least the
last value acknowledged before the snapshot.

Each round ([snapshot-round.sh](snapshot-round.sh) `sync|stop <n>`): (stop or `sync`) → CSI snapshot → (start) → clone the snapshot into a new PVC → start a
second InfluxDB on the clone → query `count(*)`, `min(seq)`, `max(seq)` and `testdb` → check the
log for skipped or corrupt files.

To make a snapshot more likely to catch a file being written, the server ran with
`--wal-files-per-snapshot=5`, so it wrote Parquet and snapshot files about every 5 seconds instead
of every few minutes:

```bash
kubectl patch deploy influxdb3 -n influxdb3-simple-test --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--wal-files-per-snapshot=5"}]'
kubectl apply -n influxdb3-simple-test -f writer.yaml
```

### Why not just `sync`

The `sync` variant runs `sync` in the InfluxDB pod and takes the snapshot while InfluxDB keeps
running. Results, with the writer active:

| Result of the restored copy | Rounds |
|---|---|
| Starts, `seq` contiguous, `testdb` intact | 7 |
| **Does not start**: `Serve command failed: failed to initialize write buffer: error from persister: serde_json error: EOF while parsing a value at line 1 column 0` | **5** |

Every copy that started also logged
`Skipping corrupt WAL file error=WAL file too small: expected at least 12 bytes, but got 0 bytes`.

**What happens.** The CSI snapshot is taken a few seconds after the `sync` (about 4 s in our
tests; longer under Kasten). InfluxDB keeps writing files in between: a WAL file every second, and
from time to time a snapshot file. The filesystem can record a new file's name in the snapshot
before its content reaches the device, so the file is there with **0 bytes**. An empty WAL file is
skipped (its points were written after the `sync`, so nothing from before is lost). An empty
snapshot file stops the server from starting. In one inspected copy, no empty file was present and
the newest file was older than the `sync`; it started, with all 6,860 points.

With the default settings, snapshot files are written much less often, so a backup would hit this
less often, but it would not be safe: one backup in some number would give a copy that does not
start, and nothing would tell you until the restore.

### Stop, snapshot, start

| Round | Downtime | Restored `seq` | Acknowledged before the stop | Errors in the log |
|---|---|---|---|---|
| 1–3 | 27 s, 33 s, 24 s | contiguous | all present | none |
| 4 | 28 s | 1..22020, contiguous | 22020 | none |
| 5 | 35 s | 1..22020, contiguous | 22020 | none |
| 6 | 24 s | 1..22500, contiguous | 22500 | none |

(Rounds 1–3 ran before a fix to the result parsing of the test script; they showed no errors in
the log, and the server started, but their counts were not read.)

The writer got connection errors during each stop, retried, and lost nothing.

---

## Step 4 — The blueprint

The same as [1.11](../1.11/README.md#step-4--the-blueprint): install the ConfigMap, the blueprint
and the binding, then label the Deployment.

```bash
kubectl create configmap influxdb-simple-scripts -n kasten-io --from-file=scripts/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f blueprint.yaml -f blueprintbinding.yaml
kubectl label deploy <influxdb3-deployment> -n <namespace> blueprint-ai/influxdb-simple=true
```

If you already installed it from `1.11/`, there is nothing more to install: the files are
identical (`diff -r 1.11/scripts 3.12/scripts` prints nothing), and one blueprint serves both
versions.

### Blueprint actions

| Action | Phase | What it does |
|---|---|---|
| `backupPrehook` | `stopInfluxDB` ([stop.sh](scripts/stop.sh)) | Saves the replica count in the annotation `blueprint-ai/influxdb-replicas`, creates the watchdog Job, scales the Deployment to 0 and waits until its pods are deleted. Fails if a pod is still there after 300 s |
| `backupPosthook` | `startInfluxDB` ([start.sh](scripts/start.sh)) | Scales the Deployment back to the saved count, waits until it is ready, removes the annotation, deletes the watchdog Job |
| `restorePosthook` | `startInfluxDB` ([start.sh](scripts/start.sh)) | Same script. If the restored Deployment has the annotation, scales it to the saved count; then waits until InfluxDB is ready |

The watchdog starts InfluxDB again if a cancelled backup does not run `backupPosthook`. It was
tested on InfluxDB 1.11 with the same scripts
([details](../1.11/README.md#the-watchdog-a-cancelled-backup-must-not-leave-influxdb-down)), not
again on 3.12. Its default delay is 600 s: InfluxDB 3 takes longer to start than 1.x, but the
delay is measured from the stop to the end of the snapshot, not to the end of the start.

---

## Step 5 — End-to-end test through Kasten

[policy.yaml](policy.yaml): on demand, snapshot then export to the Location profile
`kasten-backups` (replace with yours). Same commands as
[1.11](../1.11/README.md#step-5--end-to-end-test-through-kasten), with the namespace
`influxdb3-simple-test`, the policy `influxdb3-simple-test-backup`, and `./verify.sh` from this
folder.

To delete the test data before the in-place restore:

```bash
kubectl exec -n influxdb3-simple-test deploy/influxdb3 -- influxdb3 delete database testdb --hard-delete now -y
```

Tested on Kasten `9.0.8`, with the writer active during the backup:

| Test | Result |
|---|---|
| Backup + export | `Complete`. Prehook stopped InfluxDB, posthook started it; annotation and watchdog removed |
| Restore in place after deleting `testdb` | `Complete`. 20 points back; `stress` contiguous 1..22550; no error in the log |
| Restore after `kubectl delete namespace`, from the exported restore point | `Complete`. Same result: 20 points, `stress` 1..22550, no error |

The admin token is stored in the catalog on the PVC, and the Secret that holds it for clients is
in the namespace. Both come back with the restore, and match.

---

## Step 6 — Export deduplication

Kasten exports the snapshot of the PVC to object storage with Kopia, which stores each piece of
content once. The question is how much a new backup adds when only a little data has changed.

### Dataset and settings

[bulk-load.sh](bulk-load.sh) writes points with random float values into a database `bench`
(8 series). The writer from Step 3 was stopped, and `--wal-files-per-snapshot` was back to its
default (600).

```bash
./bulk-load.sh influxdb3-simple-test 2000000 <now - 49 h, in ms> 691   # 2,000,000 points over 2 days
./bulk-load.sh influxdb3-simple-test 20000   <now - 50 min, in ms> 691  # +1%, newer timestamps
```

InfluxDB 3 Core writes the WAL into Parquet after 600 WAL files, or when un-persisted data is
older than `--force-snapshot-max-age` (default **1 h**). With no other writes, the bulk load
would have stayed in the WAL for an hour, so for this measurement only the server ran with
`--force-snapshot-max-age=10m`.

On disk after persistence: **43 MB** of Parquet (450 files) and **77 MB** of WAL. InfluxDB keeps
the last 300 WAL files after it has persisted them (`--snapshotted-wal-files-to-keep`, default
300), so the WAL of the bulk load stays on the volume until newer writes replace it.

### Measurement

Repository growth: sum of `migration/repo/<namespace-uid>/` and
`migration/influxdb3-simple-test-backup/kopia/` in the bucket. `transferredBytes` was not
reported in the export action status on Kasten `9.0.8`.

| Backup | Change since the previous backup | Repository size | Growth |
|---|---|---|---|
| Before | (new namespace, no export yet) | 0.04 MiB | — |
| 1 | +2,000,000 points, in Parquet; 77 MB of WAL kept | 77.33 MiB | 77.28 MiB |
| 2 | +20,000 points (1%), still in the WAL | 77.85 MiB | **0.52 MiB (0.7%)** |
| 3 | none from clients; the 1% persisted into 4 new Parquet files | 78.29 MiB | **0.44 MiB (0.6%)** |
| 4 | none | 78.35 MiB | 0.06 MiB (0.1%) |

### What the numbers mean

- **InfluxDB 3 Core did not rewrite old files in this test.** New data becomes new WAL files, then new
  Parquet files; existing Parquet files did not change. Kopia stores the old files once, so a
  backup costs roughly what was written since the last one.
- **A 1% append cost about 1% in total**, over two backups (0.52 MiB as WAL, 0.44 MiB as
  Parquet). For comparison, the same test on [1.11](../1.11/README.md#step-6--export-deduplication)
  cost about 5% at each of the two backups, because InfluxDB 1.x rewrites TSM files when it
  compacts.
- **The first export is larger than the data**: it also holds the retained WAL files (77 MB here,
  for 43 MB of Parquet).
- The **local** snapshot is a Ceph RBD snapshot, which only stores changed blocks.

---

## Clean up

```bash
kubectl delete ns influxdb3-simple-test
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=influxdb3-simple-test --wait=false
kubectl delete -f policy.yaml
# Only if no other InfluxDB uses it:
kubectl delete -f blueprintbinding.yaml -f blueprint.yaml
kubectl delete configmap influxdb-simple-scripts -n kasten-io
```

---

## Initial prompt

> I would like to use this repository to create a blueprint that will work with Kasten, Kanister
> to protect influxdb, I have this running in my cluster today with important data in so would
> like to be careful when protecting this data but would like to explore the options we have
> available to protect this in a consistent state.
> (Follow-up: skip 2.x, move to InfluxDB 3; try `sync` only first, and go back to the stop if it
> does not bring the data back consistently.)
