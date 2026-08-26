# Aerospike — full logical dump on a keeper PVC with `absctl`

Kasten blueprint for Aerospike Enterprise clusters managed by the Aerospike Kubernetes Operator
(AKO). A keeper StatefulSet holds a permanent PVC; `backupPrehook` uses **`absctl`** to write a
logical dump of every Aerospike namespace onto it — a full, or an incremental against the current
chain — and Kasten snapshots that PVC.

> ⚠️ **This directory is an example, not a general-purpose solution.** It encodes assumptions about
> the AKO version, the CR layout, the storage class, the absence of Aerospike security, the
> namespace topology and the acceptable consistency level of the deployment it was developed
> against. Copy the directory, rename it, adapt it, then re-run Steps 2–6 to validate it. See the
> ownership disclaimer in the [repository README](../README.md).

> 📉 **Incremental chains are what make this viable.** A full dump every run exports the whole
> dataset every time — Kopia cannot deduplicate absctl output because it is not byte-stable. With
> incremental chains the export drops by ~69x. Set the chain length with one env var on the keeper:
> see [Incremental chains](#incremental-chains) and the measured figures in
> [Export cost — measured](#export-cost--measured).

---

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.32` (EKS) |
| Kasten | `9.0.4` |
| Operator | `aerospike-kubernetes-operator 4.5.0` (Helm) |
| Database | Aerospike Server Enterprise `8.1.2.4` |
| Backup CLI | `aerospike/absctl:v1.1.0` |
| Tools image | `aerospike/aerospike-tools:13.0.2` (`asinfo`, `aql`, `asbench`) |
| Kasten tools image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.4` |
| cert-manager | `v1.19.2` (AKO prerequisite) |

Detect the Kasten version on your own cluster rather than trusting this table:

```bash
helm ls -n kasten-io
```

The `kasten-tools` tag **must** equal that version — see
[images/kasten-tools/README.md](../images/kasten-tools/README.md).

---

## Why this pattern

### Why not snapshot the Aerospike volumes

Aerospike stores namespace data on **raw block devices, one PVC per pod**, and each node holds only
its own subset of the 4096 partitions. A single PVC snapshot *is* crash-consistent within that
volume, but that is not sufficient:

- **No cross-volume/cross-node consistency.** Aerospike's own documentation states snapshots are
  *"consistent within a volume, but may not be consistent between volumes"*. A 3-node namespace
  needs 6 volumes (`nsdata` + `workdir`) cut at the same instant; plain CSI `VolumeSnapshot` does
  not provide that.
- **Crash-consistent ≠ no acknowledged writes lost.** Writes sit in an 8 MiB streaming write buffer
  until it fills or `flush-max-ms` expires. Only `commit-to-device` closes that window, at a
  throughput cost.
- **System metadata (SMD)** — roster, truncate records, secondary index definitions, UDFs — lives on
  the `workdir` volume and Aerospike documents it as **not** covered by snapshot-based backup.

So the Aerospike device PVCs are **excluded** from the policy and `absctl` produces a logical dump
onto a keeper PVC instead.

### Why `absctl` and not the Aerospike Backup Service

ABS is a **lifecycle manager**: it owns cron schedules (`interval-cron`, `incr-interval-cron`) and
its own retention. Inside a Kasten blueprint that is a category error — Kasten owns backup frequency
and retention. Running both means two schedulers and two retention regimes over the same bytes.

`absctl` is the **primitive**: one invocation, one backup, no opinions about time. What that buys:

| | ABS service | `absctl` (this blueprint) |
|---|---|---|
| Scheduling | its own cron | Kasten only |
| Retention | its own | Kasten only |
| Chain state | internal to the service | the directory names on the PVC — inspectable with `ls` |
| Chain length control | two cron expressions | one env var on the keeper |
| Deleted records resurrected | yes, bounded by its full interval | same risk, bounded by `ABSCTL_INCREMENTALS_BEFORE_FULL` (`0` removes it) |
| Namespace list | hand-maintained, can silently miss one | discovered from the live cluster |
| Moving parts | Deployment + ConfigMap + Service + PVC | StatefulSet + PVC |

What genuinely improves:

- **No second scheduler and no second retention policy.** Kasten decides when a backup happens and
  how long restore points live. The keeper's env var only decides how long a chain may get before a
  full is forced, which is a storage-layout concern rather than a lifecycle one.
- **The chain state is a directory listing.** You can see exactly what a restore would replay with
  `ls /backup/<namespace>`. There is no service to interrogate and nothing to get out of sync.
- **No namespace list to get wrong.** The blueprint runs `asinfo -v namespaces` against the live
  cluster, so adding a namespace to the database protects it automatically. There is no config field
  that can silently omit one.

What does **not** improve: the deleted-record risk is inherent to logical incrementals, not to ABS.
It applies here too, bounded by `ABSCTL_INCREMENTALS_BEFORE_FULL` and eliminated only by setting it
to `0`. In practice it is an accepted trade-off for this workload class — see
[Incremental chains](#incremental-chains).

---

## Incremental chains

`absctl` has no chain state of its own — `--modified-after <YYYY-MM-DD_HH:MM:SS>` is supplied by the
caller. This blueprint keeps that state **entirely in the directory names on the keeper PVC**, so
there is no marker file, no ConfigMap and nothing that can drift from reality:

```
/backup/<namespace>/full-20260826T104218Z/
/backup/<namespace>/incr-20260826T104714Z/
/backup/<namespace>/incr-20260826T105003Z/
```

The names sort chronologically, so the current chain is *the newest `full-` plus every `incr-` after
it*. Counting the incrementals decides whether the next run is another incremental or a forced full,
and the newest directory's stamp becomes the next `--modified-after`. Reading the volume is the whole
of the state machine.

### The one knob

```yaml
# abs-keeper.yaml, on the keeper container
env:
  - name: ABSCTL_INCREMENTALS_BEFORE_FULL
    value: "3"        # full, incr, incr, incr, then full again (and prune)
                      # 0 = never take an incremental; every run is a full
```

The blueprint reads it from the **running** pod (`kubectl exec -- printenv`), so changing the value
and rolling the StatefulSet is enough — no blueprint edit. When a full is taken, every superseded
`full-`/`incr-` directory for that namespace is pruned. Pruning the live volume never affects
existing restore points, which are immutable snapshots.

> ℹ️ **Incrementals can resurrect a deleted record — usually an acceptable trade.** absctl
> incrementals carry changed and new records, never deletions, so a record deleted *after* the last
> full comes back if that chain is restored. The next full heals it, which makes
> `ABSCTL_INCREMENTALS_BEFORE_FULL` the maximum exposure window. Durable deletes do **not** help — an
> incremental taken after a durable delete produced no output at all.
>
> Per Aerospike's own solution architects, operators of this workload class generally **do not**
> consider this a blocker: Aerospike is typically a serving or session layer where records are
> re-derivable and the recovery objective is "get the cluster back fast", not "reproduce the exact
> deletion history". That is why this blueprint ships with `3` rather than `0`.
>
> Set `0` when a deletion genuinely must never come back — a compliance erasure path (GDPR and
> similar), or a namespace holding the authoritative copy of something. It costs a full export every
> run; see [Export cost — measured](#export-cost--measured).

A detail worth knowing if you modify this: absctl writes **nothing at all — not even the
directory** — when an incremental finds no changed records. The blueprint pre-creates the directory
so the chain counter still advances and the forced full still triggers on schedule.

---

## Export cost — measured

Measured with [Step 6 of AGENTS.md](../AGENTS.md#step-6--measure-export-deduplication-efficiency) on
100,012 records (~200-byte random values), 1% of records churned before every run,
`ABSCTL_INCREMENTALS_BEFORE_FULL=3`. Five consecutive policy runs:

| Run | Kind | Kopia repo growth | Chain after the run |
|---|---|---|---|
| 1 | **full** | **23,526,001 B** (22.4 MiB) | `full-102609Z` |
| 2 | incr | **341,899 B** (334 KiB) | + `incr-102956Z` |
| 3 | incr | **341,899 B** | + `incr-103336Z` |
| 4 | incr | **368,928 B** | + `incr-103719Z` |
| 5 | **full** | **23,525,933 B** | `full-104218Z` — **all earlier members pruned** |

**An incremental export costs ~69x less than a full.** That is the difference between this blueprint
being usable at scale and not.

### Why a full costs the whole dump

A full dump cannot be deduplicated against the previous full, because **absctl output is not
byte-stable**. Two consecutive dumps of *identical, unchanged* data differ — at `--parallel 4` and at
`--parallel 1`:

```
--parallel 4:  run A md5 = 0ae0a39ff38543be583766521956d288
               run B md5 = 9ab9f4235c1d952758af5f42e3f394b9   => NOT deterministic
--parallel 1:  run A md5 = c6d5cd5a3c74ec1dd8330a555670878f
               run B md5 = f7bab52ede5d081c4d21cdf789451654   => NOT deterministic
```

Record scan order varies between runs, so ~83% of the dump's bytes differ even after a 1% change and
content-defined chunking has nothing to match. A control confirms the pipeline itself is faithful:
50 MiB of `/dev/urandom` written to the keeper PVC grew the repository by 52,443,164 bytes, 1:1.

Incrementals sidestep this entirely — each one is a **new, small, never-rewritten directory**, so the
layout is append-only and Kasten's export only ships the new member.

### Backup duration

absctl reads **only the changed records** — it does not scan the whole namespace:

| | Records read | Bytes written | absctl duration |
|---|---|---|---|
| full | 100,012 | 28,702,695 | ~143 ms |
| incremental (1% churn) | **1,000** | 288,129 | **~13 ms** |

So the tool itself is ~10x faster. The Kasten **BackupAction** duration, however, ranged 71–152 s
across the five runs with no correlation to full vs incremental: at this dataset size it is dominated
by orchestration — snapshot creation, pod scheduling, export setup — and absctl's contribution is
milliseconds. Expect the incremental gain to become visible in the action duration only once the
dataset is large enough for the scan to dominate.

### What Kasten actually put on the wire

The table above uses object-store repository growth because it is durable and re-checkable. Kasten's
own accounting is smaller and even more favourable, because the export is *block*-incremental on top
of the append-only layout — taken from the `portableAppData` ExportAction status:

| Export | `readBytes` | `transferredBytes` |
|---|---|---|
| full | 28,700,000 | **28,700,000** |
| incremental | **168** | **4,400** |

`processedBytes: 29000168` alongside `readBytes: 168` shows it walking the whole volume but reading
only the changed blocks — a ~6,500x reduction on the wire for an incremental.

> **Reading these figures yourself.** Use the **data** export action, labelled
> `k10.kasten.io/exportType: portableAppData`. The *metadata* export action
> (`k10.kasten.io/isMetadataExport: "true"`) has no `progressDetails` at all and is easy to grab by
> mistake when simply taking the newest action:
> ```bash
> kubectl get exportaction -n aerospike-test \
>   -l k10.kasten.io/exportType=portableAppData \
>   --sort-by=.metadata.creationTimestamp -o yaml | sed -n '/progressDetails/,+8p'
> ```
> **Capture it promptly** — `progressDetails` is not durably retained. On this cluster every one of
> 26 older completed data-export actions had lost it, so do not plan to read it hours after the run.
> That is why the results table uses repository growth.

## Prerequisites

- A Kubernetes cluster with Kasten in `kasten-io`.
- A CSI storage class supporting snapshots, with a `VolumeSnapshotClass` registered with Kasten.
- **cert-manager** — AKO's admission webhooks need it.
- **An Aerospike Enterprise feature-key file.** AKO supports Enterprise Edition only.

### The feature-key file is mandatory under AKO

The `aerospike-server-enterprise` image bundles an evaluation key at
`/etc/aerospike/features.conf` (single node, all features, version-bound rather than time-bound).
**It is unusable under AKO**, which mounts an emptyDir named `confdir` at `/etc/aerospike` in the
`aerospike-server` container, hiding the bundled file. Omitting `feature-key-file` fails with:

```
unable to open file /etc/aerospike/features.conf: No such file or directory
```

Note the AKO repository's `config/samples/secrets/` contains TLS certs and passwords but **no
`features.conf`**, even though the sample CRs reference one. Obtain a key by either:

- **Free 8-node, 60-day trial** — sign up on [aerospike.com](https://aerospike.com/). This is what
  this blueprint was tested with.
- **Extract the bundled single-node evaluation key:**
  ```bash
  docker run --rm --entrypoint cat \
    aerospike/aerospike-server-enterprise:8.1.2.4 /etc/aerospike/features.conf > features.conf
  grep -E "account-ID|cluster-nodes-limit|valid-until" features.conf
  ```

> 🔒 Feature-key files are licences. Never commit one. This repository's
> [.gitignore](../.gitignore) excludes `features.conf`, `*features.conf` and `*-features.conf`.

---

## Step 2 — Deploy the workload and create test data

### 2.1 Install AKO

Operator in its own namespace, workload in another, so operator lifecycle stays independent of the
application data Kasten protects.

```bash
kubectl create ns aerospike-operator
kubectl create ns aerospike-test

helm repo add aerospike https://aerospike.github.io/aerospike-kubernetes-enterprise
helm repo update aerospike

helm install aerospike-kubernetes-operator aerospike/aerospike-kubernetes-operator \
  --namespace aerospike-operator \
  --version 4.5.0 \
  --set watchNamespaces="aerospike-test" \
  --wait --timeout 5m
```

### 2.2 RBAC for the cluster pods

```bash
kubectl apply -f aerospike-rbac.yaml
```

> ⚠️ **The AKO documentation's namespace-scoped snippet is not sufficient.** It creates a
> `RoleBinding`, but the `aerospike-init` container runs `akoinit`, which **lists `nodes` at cluster
> scope** — something a `RoleBinding` can never grant. Without a `ClusterRoleBinding` the init
> container crash-loops with:
>
> ```
> Error: nodes is forbidden: User "system:serviceaccount:aerospike-test:aerospike-operator-controller-manager"
> cannot list resource "nodes" in API group "" at the cluster scope
> ```

### 2.3 Feature-key Secret

```bash
kubectl create secret generic aerospike-secret -n aerospike-test \
  --from-file=features.conf=<path-to-your-features.conf>
```

### 2.4 Deploy the Aerospike cluster

```bash
kubectl apply -f aerospike-cluster.yaml
kubectl get aerospikecluster -n aerospike-test -w    # wait for PHASE=Completed
```

[aerospike-cluster.yaml](aerospike-cluster.yaml) creates a 3-node cluster, namespace `test` at
`replication-factor 2`, a 1Gi filesystem `workdir` and a 5Gi **raw block** `nsdata` device per pod.

> **Storage class**: `ebs-sc` is the AWS EBS CSI storage class used in our test environment.
> Replace it with a storage class that supports CSI snapshots on your cluster
> (for example, `managed-csi` on AKS, `standard-rwo` on GKE, your custom class on bare-metal, and so on).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (for example, `gp2` on AWS) — they do not support CSI snapshots.

> ⚠️ **`blockVolumePolicy.initMethod` must zeroize.** A freshly provisioned EBS block volume contains
> non-zero data and Aerospike refuses a device that is neither already an Aerospike device nor
> erased:
>
> ```
> CRITICAL (drv_ssd): /test/dev/xvdf: not an Aerospike device but not erased - check config or erase device
> ```
>
> AKO supports only `dd` or `blkdiscard`; EBS does not reliably support discard/TRIM, so use `dd`.

> ℹ️ This cluster runs **without** Aerospike security, so `absctl` connects without credentials. With
> security enabled, add `--user`/`--password` to the `absctl` invocations in the blueprint.

### 2.5 Deploy the keeper

```bash
kubectl apply -f abs-keeper.yaml
kubectl rollout status statefulset/aerospike-backup-keeper -n aerospike-test
```

[abs-keeper.yaml](abs-keeper.yaml) creates a headless Service and a 1-replica **StatefulSet** running
`aerospike/absctl:v1.1.0`, with a `volumeClaimTemplate` for the dump volume. Notes:

- **StatefulSet, not Deployment** — the pod name is deterministic (`aerospike-backup-keeper-0`), so
  the blueprint never has to discover it, and the same shape scales to the partitioned variant.
- `securityContext.fsGroup: 65532` — absctl runs as `uid=65532(abtuser) gid=65532(abtgroup)`.
- The image ENTRYPOINT is `absctl` itself, so `command` overrides it with a sleep. The image is
  Alpine — **`/bin/sh` only, no bash**, and no `jq`, `curl` or `kando`.
- The PVC holds exactly **one** dump (`--remove-files` overwrites in place), so size for the dump,
  not for a retention chain.
- The volumeClaimTemplate is labelled `app=aerospike-backup-keeper`, deliberately **not**
  `app=aerospike-cluster`, which is what the policy excludes.
- `ABSCTL_INCREMENTALS_BEFORE_FULL` controls the chain length — see
  [Incremental chains](#incremental-chains). The PVC must hold a whole chain, not one dump.

### 2.6 Create test data

```bash
./test-data.sh load       # key1..key10 in test.demo  (~1KB)
./test-data.sh verify
./test-data.sh count      # per-node object counts
```

Deliberately tiny for a fast development loop. Step 6 needs a much larger dataset — see there.

### 2.7 Removing everything

```bash
kubectl delete -f policy.yaml -f blueprintbinding.yaml -f blueprint.yaml
kubectl delete -f abs-keeper.yaml
kubectl delete pvc -l app=aerospike-backup-keeper -n aerospike-test
kubectl delete -f aerospike-cluster.yaml
kubectl delete secret aerospike-secret -n aerospike-test
kubectl delete -f aerospike-rbac.yaml

helm uninstall aerospike-kubernetes-operator -n aerospike-operator
kubectl delete crd aerospikeclusters.asdb.aerospike.com \
  aerospikebackups.asdb.aerospike.com \
  aerospikebackupservices.asdb.aerospike.com \
  aerospikerestores.asdb.aerospike.com

kubectl delete ns aerospike-test aerospike-operator
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=aerospike-test
```

---

## Step 3 — Validate the workflow without a blueprint

```bash
SEED=aerocluster.aerospike-test.svc.cluster.local
POD=aerospike-backup-keeper-0

# Which namespaces exist?
kubectl exec -n aerospike-test aerocluster-0-0 -c aerospike-server -- asinfo -v namespaces

# Dump one namespace
kubectl exec -n aerospike-test $POD -c absctl -- \
  absctl backup --host $SEED --port 3000 -n test -d /backup/test --remove-files --parallel 4

# CRITICAL: flush the page cache before snapshotting (see the warning in Step 4)
kubectl exec -n aerospike-test $POD -c absctl -- sync

# Inspect
kubectl exec -n aerospike-test $POD -c absctl -- sh -c 'ls -l /backup/test; du -sk /backup/test'

# Truncate and restore
kubectl exec -n aerospike-test aerocluster-0-0 -c aerospike-server -- \
  asinfo -v "truncate-namespace:namespace=test"
kubectl exec -n aerospike-test $POD -c absctl -- \
  absctl restore --host $SEED --port 3000 -n test -d /backup/test --parallel 4
```

Emulate what Kasten does to the keeper PVC with a CSI snapshot, following
[this example](https://github.com/michaelcourcy/test-csi-snapshot), and wait for
`readyToUse=true` before testing anything downstream.

---

## Step 4 — The blueprint

```bash
kubectl apply -f blueprint.yaml
kubectl apply -f blueprintbinding.yaml
```

### Blueprint actions

| Action | Phase | What it does |
|---|---|---|
| `backupPrehook` | `absctlBackup` | Waits for the keeper pod. Discovers the Aerospike server pod (`app=aerospike-cluster`), the cluster name (`aerospike.com/cr` label → headless Service = seed host) and the namespace list (`asinfo -v namespaces`). Reads `ABSCTL_INCREMENTALS_BEFORE_FULL` from the running keeper, derives the chain state from the PVC directory names, and takes a **full** or an **incremental** (`--modified-after`) into a new timestamped directory. Then **`sync`**, verifies absctl's reported byte count landed on the device, and **prunes superseded chain members after a full**. Emits `keeper`, `namespace`, `cluster`, `namespaces`, `dumpKiB`, `summary`. |
| `restorePosthook` | `absctlRestore` | Waits for the restored keeper pod. Enumerates namespaces **from the PVC** and resolves each one's chain (newest full + later incrementals). **Pre-flight-validates every chain before touching the database.** Waits for the Aerospike cluster and `cluster_integrity=true`. Then per namespace: `truncate-namespace`, then replays the chain **oldest first** so the newest version of a record wins. |
| `delete` | `absctlDelete` | Logs the artifact values. Nothing external to delete. |

There is deliberately **no `backupPosthook`**: the keeper PVC is permanent, so there is nothing to
unquiesce and no temporary PVC to remove.

### 🛑 The `sync` is not optional

```yaml
kubectl exec ... -- sync
```

A CSI/EBS snapshot captures the **block device** and bypasses the OS page cache. `absctl` returns as
soon as its writes are in cache, so **without this sync the snapshot captures `.asb` files as zero
length**. The result is a restore point that looks perfectly healthy, exports almost nothing, and
restores no data at all — silent, total data loss. Observed directly during development:

```
0        0_test_4.asb
0        1_test_1.asb
7159224  2_test_2.asb
0        3_test_3.asb
```

Counting files is not enough to detect this, because the (empty) files exist. The blueprint compares
**absctl's own reported byte count against the on-device sum after the sync**, which detects the
un-synced case precisely without misreading a legitimately empty incremental (0 changed records → 0
files) as a failure.

### Pre-flight validation must precede the truncate

The restore truncates each namespace before repopulating it. If a dump problem were discovered
mid-loop, the already-truncated namespaces would be left **empty** — turning a failed restore into
data loss. This happened during development and destroyed a test namespace. The blueprint therefore
validates every namespace's dump up front and aborts before a single record is deleted.

### Design notes

**Everything is discovered, nothing is configured.** No ConfigMap, no annotation, no hardcoded
hostname:

| Needed | Discovered from |
|---|---|
| keeper pod | `<StatefulSet name>-0` — deterministic |
| Aerospike server pod | label `app=aerospike-cluster` in the same namespace |
| seed host | `aerospike.com/cr` label → AerospikeCluster name → headless Service |
| namespaces (backup) | `asinfo -v namespaces` on the live cluster |
| namespaces (restore) | the directories present on the keeper PVC |

**One image, via `kubectl exec`.** Phases are `KubeTask` in `kasten-io` using
`kasten-tools:9.0.4` (kubectl, jq, kando) and reach `absctl` in the keeper and `asinfo` in the
server pods with `kubectl exec`. Running in `kasten-io` is required: that is where the Kanister
service account with cross-namespace `pods/exec` lives. No custom image is built.

**`absctl restore` requires `--namespace`.** The help text presents it as remapping only
(`source-ns,destination-ns`), which reads as optional. Omitting it fails with
`failed to validate config: namespace is required`. Passing the bare namespace restores it to
itself.

---

## Step 5 — Kasten policy and end-to-end test

```bash
kubectl apply -f policy.yaml
kubectl get policy aerospike-absctl-backup -n kasten-io -o jsonpath='{.status.validation}'
```

[policy.yaml](policy.yaml) has three mandatory properties:

**1. A Location profile, not an Infra profile** — only a Location profile is recorded in the
`RestorePointContent` in a Kanister-compatible form.

```bash
kubectl get profile <name> -n kasten-io -o jsonpath='{.spec.type}'   # must print "Location"
```

**2. The Aerospike PVCs must be excluded.**

```yaml
filters:
  excludeResources:
    - resource: persistentvolumeclaims
      matchLabels:
        app: aerospike-cluster
```

AKO's PVCs (`nsdata-*`, `workdir-*`) all carry `app=aerospike-cluster`; the keeper PVC carries
`app=aerospike-backup-keeper` and stays in the snapshot. Confirm exactly one snapshot per run:

```bash
kubectl get volumesnapshot -n aerospike-test \
  -o custom-columns='NAME:.metadata.name,PVC:.spec.source.persistentVolumeClaimName,READY:.status.readyToUse'
```

**3. An export action** — required for Step 6, and what makes restore points durable beyond the
cluster. No `migrationToken` is needed in the manifest; Kasten generates one on admission.

### Run a backup

Trigger through a Kasten `RunAction` — **never create a Kanister ActionSet by hand**, since Kasten
sets the ActionSet context up differently and template variables silently resolve to empty strings.

```bash
kubectl create -f - --validate=false <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RunAction
metadata:
  generateName: run-aerospike-
  namespace: kasten-io
spec:
  subject:
    kind: Policy
    name: aerospike-absctl-backup
    namespace: kasten-io
EOF
```

### Restore

```bash
kubectl get restorepoint -n aerospike-test

kubectl create -f - --validate=false <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata:
  generateName: restore-
  namespace: aerospike-test
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: <RESTORE_POINT_NAME>
    namespace: aerospike-test
  targetNamespace: aerospike-test
EOF
```

No `profile` is needed: Kasten extracts the location profile from the `RestorePointContent`.
Verified on Kasten `9.0.4` restoring from a **remote** (exported) restore point with no `profile`
field — the action completed and `restorePosthook` ran normally.

> ⚠️ **On Kasten 8.x you must add `spec.profile` explicitly.** There, extraction was unreliable: the
> restore fell back to looking for a default profile literally named `kanister-profile` and, when it
> did not exist, failed *after* the volumes had been restored, with `restorePosthook` never running:
>
> ```
> profiles.config.kio.kasten.io "kanister-profile" not found
> Could not fetch a location profile. Location profile must be specified in action or policy parameters
> ```
>
> Observed on `8.5.13`, fixed in `9.0.4`. The failure mode is deceptive for a truncate-then-restore
> blueprint: the volume restore succeeds and reports 100% progress, so it looks like a late failure
> when in fact the data-side hook never executed. Workaround on 8.x:
>
> ```yaml
>   profile:
>     name: <LOCATION_PROFILE>
>     namespace: kasten-io
> ```

**Manual preparation for disaster recovery.** For an in-place rollback nothing is needed — the
blueprint truncates and restores. For a restore into an empty namespace or new cluster, the
`AerospikeCluster` must be **running and ready** first, because its device PVCs are excluded from the
restore point. The blueprint waits up to 10 minutes for the cluster to answer `asinfo`.

### Verified end-to-end result

Two Aerospike namespaces (`test` with 100k records plus a `demo` set, and `bar`).

**Chain behaviour** — five consecutive policy runs with `ABSCTL_INCREMENTALS_BEFORE_FULL=3`
produced exactly `full, incr, incr, incr, full`, and the fifth run pruned every earlier member. See
[Export cost — measured](#export-cost--measured).

**Multiple Aerospike namespaces, each with its own chain** — this is the case worth testing, because
every namespace maintains an independent chain and is truncated and replayed independently. Both
namespaces were given a versioned key in each of two incrementals, then corrupted differently:

| Namespace | full | incr 1 | incr 2 |
|---|---|---|---|
| `bar` | 1,389 B | 166 B | 166 B |
| `test` | 28,701,821 B | 168 B | 168 B |

| | Expected | Actual after restore |
|---|---|---|
| `test.keyMULTI` | `v2` — newest of `test`'s two incrementals | ✅ `v2` |
| `bar.keyMULTI` | `w2` — newest of `bar`'s two incrementals | ✅ `w2` |
| `keyJUNKT` (added to `test`, never backed up) | removed | ✅ absent |
| `keyJUNKB` (added to `bar`, never backed up) | removed | ✅ absent |

Note `bar`'s incrementals are only 166 bytes while `test`'s full is 28 MB — the chains are resolved
per namespace, so a small namespace is not dragged along by a large one.

**Chain replay ordering (single namespace)** — a key was given a different value in each of two
incrementals, then deleted, with an extra record added that was never backed up:

```bash
# incremental 1
INSERT INTO test.demo (PK, id, name, city) VALUES ('keyCHAIN', 1, 'chain', 'v1')   # run -> incr
# incremental 2
INSERT INTO test.demo (PK, id, name, city) VALUES ('keyCHAIN', 2, 'chain', 'v2')   # run -> incr
# corrupt
DELETE FROM test.demo WHERE PK='keyCHAIN'
./test-data.sh add-one keyBOGUS        # a record that is NOT in any backup
```

Chain on the PVC: `full-104218Z`, `incr-104714Z`, `incr-105003Z`.

| | Expected | Actual after restore |
|---|---|---|
| `keyCHAIN` | `v2` — newest of the two incrementals | ✅ `v2` |
| `keyBOGUS` | removed (never backed up) | ✅ absent |
| `test.demo` | key1–key10 + keyCHAIN + keyINC1/2 | ✅ |
| `bar.demo` | key1–key10 | ✅ |

`keyCHAIN` resolving to `v2` proves the chain is replayed oldest-first so the newest version of a
record wins. `keyBOGUS` being removed proves the truncate phase runs — an additive restore alone
would have left it behind.

## Step 6 — Export cost measurement

The procedure is defined in
[Step 6 of AGENTS.md](../AGENTS.md#step-6--measure-export-deduplication-efficiency);
[measure-dedup.sh](measure-dedup.sh) implements it for this blueprint.

```bash
./measure-dedup.sh size            # current Kopia repo size on the object store
./measure-dedup.sh dump            # current chain size on the keeper PVC
./measure-dedup.sh run <name>      # backup + export, wait for both
./measure-dedup.sh churn <n>       # rewrite n records with fresh random values
```

Four things that make or break the measurement:

- **The dataset must be large enough.** The Step 2 dataset (~1KB) is useless; Kopia metadata
  dominates. Load ~100k records with `asbench` first:
  ```bash
  asbench --hosts <seed> --port 3000 --namespace test --set bench \
          --keys 100000 --object-spec S200 --workload I --threads 16 --random
  ```
- **`--random` is mandatory.** Without it `asbench` writes the *same* value to every record, which
  deduplicates ~200:1 and makes the result meaningless. This cost real debugging time.
- **Sum both export prefixes.** The namespace repository holds the bulk, with a smaller per-policy
  companion:
  ```
  k10/<cluster-uid>/migration/repo/<namespace-uid>/   <- the exported volume data
  k10/<cluster-uid>/migration/<policy-name>/kopia/    <- smaller companion repo
  ```
- **`transferredBytes` cannot be read after the fact.** Kasten discards
  `status.progressDetails` when an action completes, so the repository growth on the object store is
  the retained, verifiable figure — that is what the results table uses.

Results are in [Export cost — measured](#export-cost--measured).

## Troubleshooting

```bash
# Read a failed action's real cause — it is nested inside status.error.cause
kubectl get restoreaction <name> -n aerospike-test -o jsonpath='{.status.error}'
kubectl get runaction <name> -n kasten-io -o jsonpath='{.status.error}'

kubectl logs -n kasten-io -l component=executor --tail=10000 -f
kubectl logs -n aerospike-test aerocluster-0-0 -c aerospike-server --tail=200
kubectl logs -n aerospike-test aerocluster-0-0 -c aerospike-init --tail=60
```

| Symptom | Cause |
|---|---|
| `profiles... "kanister-profile" not found` and `restorePosthook` never ran | Kasten 8.x only; fixed in 9.0.4. On 8.x set `spec.profile` on the `RestoreAction`. |
| `nodes is forbidden ... at the cluster scope` in `aerospike-init` | A `RoleBinding` was used instead of a `ClusterRoleBinding`. Apply [aerospike-rbac.yaml](aerospike-rbac.yaml). |
| `unable to open file /etc/aerospike/features.conf` | Relying on the image's bundled key under AKO, which overlays `/etc/aerospike`. Mount your own key. |
| `not an Aerospike device but not erased` | `blockVolumePolicy.initMethod` is `none`. Use `dd`. |
| `cannot update replication-factor ... alongside any other spec change` | The AKO webhook requires that change on its own; easiest during development is to delete and recreate the CR. |
| `failed to validate config: namespace is required` | `absctl restore` needs `--namespace`, despite the help text. |
| `.asb` files are zero length in a restore point | The backup ran without `sync`. See [the sync warning](#-the-sync-is-not-optional). |
| Restore fails and a namespace is left empty | A pre-flight failure after truncate. The current blueprint validates before truncating; older revisions did not. |
| `Kanister artifact did not contain expected size field` (error level) | Benign. Kasten looks for a `size` key for repository accounting; no blueprint in this repo emits one. |

Test commands before changing YAML:

```bash
kubectl run debug -n aerospike-test --image=aerospike/aerospike-tools:13.0.2 --restart=Never --rm -it -- bash
```

---

## Files

| File | Purpose |
|---|---|
| [aerospike-rbac.yaml](aerospike-rbac.yaml) | ServiceAccount + **ClusterRoleBinding** for the Aerospike pods |
| [aerospike-cluster.yaml](aerospike-cluster.yaml) | 3-node `AerospikeCluster` CR |
| [abs-keeper.yaml](abs-keeper.yaml) | Keeper headless Service + StatefulSet with the dump PVC |
| [blueprint.yaml](blueprint.yaml) | The blueprint |
| [blueprintbinding.yaml](blueprintbinding.yaml) | Binds the blueprint to the keeper StatefulSet |
| [policy.yaml](policy.yaml) | Kasten policy: PVC exclusion + export |
| [test-data.sh](test-data.sh) | Load / verify / mutate the test dataset |
| [measure-dedup.sh](measure-dedup.sh) | Step 6 deduplication measurement |

---

## Initial prompt

> Can you build a blueprint for Aerospike?
>
> How could this approach complement or compare with Aerospike Backup Service and its incremental
> backup capabilities, considering there will be also other applications to backup on the same
> cluster such as Kafka?
>
> [after a review with an Aerospike solution architect] Using the ABS routine and policy is
> unadapted because Kasten is in charge of the backup lifecycle — we cannot ride both horses. The
> image contains an `absctl` utility that can do all the primitive operations. Implement the simple
> approach with absctl: full backup each time, but with an honest Kopia deduplication measurement.
>
> [after that measurement showed a full dump exports the whole dataset every run] Now work with
> incremental mode. Put the number of incrementals before a full in an env variable on the keeper —
> discovering how many incrementals were done should just be reading the filesystem in the PVC.
