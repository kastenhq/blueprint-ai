# PSMDB pbm Blueprint (Pattern 5 — MinIO Keeper)

Kasten blueprint for **sharded** Percona Server for MongoDB (PSMDB) clusters that use
**Percona Backup for MongoDB (pbm) to a local MinIO instance**. Kasten snapshots the MinIO
PVC (which contains the full pbm archive), while pbm handles consistent cross-shard backup
and restore.

## Why this blueprint instead of [psmdb-percona-operator-example](../psmdb-percona-operator-example/)

The [psmdb-percona-operator-example](../psmdb-percona-operator-example/) blueprint (Pattern 1 — quiesce a replica)
targets **unsharded** replica sets. Extending it to sharded clusters is prohibitively complex:
you would need to quiesce every shard's secondary simultaneously, track multiple PVC names, and
exclude the non-quiesced PVCs from each shard at restore time.

This blueprint delegates that entire complexity to the PSMDB operator. pbm performs a
cluster-wide consistent snapshot that covers all shards and the config server replica set
atomically. The blueprint is a thin orchestrator — it creates a `PerconaServerMongoDBBackup` CR,
waits for it to complete, and emits the backup name as an artifact. Restore is equally simple:
create a `PerconaServerMongoDBRestore` CR and wait.

## Versions

Exact versions used for the last end-to-end test (backup, restore, `delete` action), on
2026-11-27:

| Component | Version |
|---|---|
| Kubernetes | `1.35` (kind, `csi-hostpath-sc` from the CSI hostpath driver) |
| Kasten | `9.0.11` |
| PSMDB Operator / Helm chart | `1.22.0` / `psmdb-db 1.22.2` |
| Percona Server for MongoDB | `8.0.19-7` |
| Percona Backup for MongoDB | `2.12.0` |
| S3 keeper | RustFS `1.0.7` (`rustfs/rustfs:1.0.7`) |
| S3 client (bucket creation, checks) | AWS CLI `2.42.4` (`amazon/aws-cli:2.42.4`) |

> Detect your own Kasten version with `helm ls -n kasten-io`.
>
> The blueprint was first developed on EKS `1.32` with Kasten `8.5.4` and a MinIO keeper
> (`RELEASE.2025-04-22T22-12-26Z`).

### Why the keeper runs RustFS and not MinIO

The MinIO images can no longer be pulled. On 2026-11-27, `quay.io/minio/minio` answered
`no such manifest`, and `minio/minio` and `minio/mc` on Docker Hub answered
`denied: requested access to the resource is denied`. A keeper Pod with the old image stays in
`ImagePullBackOff`.

The keeper now runs [RustFS](https://github.com/rustfs/rustfs), an S3-compatible server under the
Apache 2.0 license. pbm only needs the S3 API, so nothing changes on the PSMDB side. What changes
in [minio-keeper.yaml](minio-keeper.yaml):

| MinIO | RustFS |
|---|---|
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` (same Secret) |
| `args: [server, /data, --console-address, ":9001"]` | `args: [/data]`, console disabled |
| runs as root | runs as uid `10001`: the Pod sets `fsGroup: 10001` so it can write to the PVC |
| `mc` client | `aws s3` client (`amazon/aws-cli`) |

The resource names still contain `minio` (`my-psmdb-psmdb-db-minio`, label `psmdb-minio`): the
blueprint finds the PSMDB cluster name by removing the `-minio` suffix from the Deployment name,
and existing deployments keep working with the same binding.

> **Existing MinIO keepers.** Replacing only the image of a running MinIO keeper, so that RustFS
> reads the MinIO data directory, was **not tested**. Deploy a new keeper, take a new backup with
> it, and keep the old restore points until they expire. They restore the old MinIO Deployment.
> Until the image is replaced, that Deployment cannot start unless the MinIO image is still in a
> cache or a mirror you control. See also
> [cnpg-barman-cloud-example](../cnpg-barman-cloud-example/README.md#why-the-keeper-runs-rustfs-and-not-minio),
> which made the same change.

## Pattern

**Pattern 5 — pbm backup via a local MinIO keeper (vendor operator data mover).**

A dedicated S3 `Deployment` (the *keeper*, running RustFS — see
[why not MinIO](#why-the-keeper-runs-rustfs-and-not-minio)) with a permanent PVC stores the pbm archive.
The `backupPrehook` blueprint action:
1. Creates a `PerconaServerMongoDBBackup` CR to trigger a pbm logical backup across all shards.
2. Polls until the backup `status.state` reaches `ready`.
3. Runs `sync` in the keeper Pod. A CSI snapshot captures the block device, and files still in
   the page cache would be missing from it (or present with zero bytes).
4. Emits the backup CR name, pbm internal name, cluster name, and namespace as restore-point
   artifacts.

Kasten then snapshots the MinIO PVC (and only that PVC — PSMDB data PVCs are excluded via
policy filter). The MinIO snapshot contains a self-consistent pbm archive: a full logical backup
of all shards and the config server.

On restore, the `restorePosthook` action:
1. Waits for the PSMDB cluster to reach `ready` state after Kasten restores the CR and MinIO PVC.
2. Creates a `PerconaServerMongoDBRestore` CR referencing the artifact backup name.
3. Polls until the restore `status.state` reaches `ready`.

**Why this pattern beats direct PVC snapshotting for sharded clusters:**
pbm provides a cluster-wide consistent point-in-time snapshot that spans all replica sets.
Snapshotting the raw MongoDB data PVCs directly across shards would capture each shard at a
slightly different point in time, producing an inconsistent cluster state on restore (missing
cross-shard transactions, inconsistent chunk metadata in the config server).

## Architecture

```
┌───────────────────────────────────────────┐
│  Namespace: psmdb-test                    │
│                                           │
│  ┌──────────┐   ┌─────────┐   ┌─────────┐ │
│  │ cfg rs   │   │  rs0    │   │ mongos  │ │
│  │ (3 pods) │   │ (3 pods)│   │ (1 pod) │ │
│  └──────────┘   └─────────┘   └─────────┘ │
│       │               │                   │
│  PVCs (excluded   pbm backup-agent        │
│  from snapshot)   on every pod            │
│                        │                  │
│                        ▼                  │
│                  ┌──────────┐             │
│                  │  MinIO   │             │
│                  │  Keeper  │             │
│                  │   PVC    │◄────────────┼── Kasten snapshots this
│                  └──────────┘             │
└───────────────────────────────────────────┘
```

The BlueprintBinding targets Deployments labelled `psmdb-minio: "true"`. The MinIO Deployment
name must follow the convention `<cluster-name>-minio` — the blueprint derives the PSMDB cluster
name by stripping the `-minio` suffix.

## Blueprint actions

| Action | Hook | What it does |
|---|---|---|
| `backupPrehook` | Before PVC snapshot | Creates `PerconaServerMongoDBBackup` CR, waits for pbm to complete, runs `sync` in the keeper Pod, emits `backupCRName`, `pbmName`, `clusterName`, `namespace` as restore-point artifacts |
| `restorePosthook` | After PVC restore | Waits for PSMDB cluster ready, creates `PerconaServerMongoDBRestore` CR, polls until `ready`, waits for cluster to return to `ready` |
| `delete` | On restore-point deletion | Runs `pbm delete-backup` to remove archive data from MinIO, then deletes the `PerconaServerMongoDBBackup` CR; silently no-ops if the namespace or pod no longer exists |

> **Why a `delete` action is necessary**: deleting a Kasten restore point only removes the MinIO
> PVC snapshot. The pbm archive data inside MinIO and the `PerconaServerMongoDBBackup` CR are not
> cleaned up automatically. Without the `delete` action, the MinIO bucket grows unboundedly and
> old CRs accumulate. The `delete` action runs `pbm delete-backup` directly in the backup-agent
> container (which has pbm pre-configured) and removes the Kubernetes CR.

## Prerequisites

- PSMDB operator installed (`percona/psmdb-operator` Helm chart, `watchAllNamespaces=true`).
- `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.11` image available (adds `kubectl` to
  `gcr.io/kasten-images/kanister-tools:9.0.11`, see [../images/kasten-tools/](../images/kasten-tools/)).
  Use the tag that matches your Kasten version.
- Blueprint and BlueprintBinding deployed in the `kasten-io` namespace.
- Kasten backup policy configured with the PVC filter described below.

## Deploy the operator

```bash
helm repo add percona https://percona.github.io/percona-helm-charts/
helm repo update

helm upgrade --install psmdb-operator percona/psmdb-operator \
  --namespace psmdb-operator --create-namespace \
  --version 1.22.0 \
  --set watchAllNamespaces=true \
  --wait
```

## Deploy the workload

### 1. Create the namespace

```bash
kubectl create namespace psmdb-test
```

### 2. Deploy the S3 keeper (RustFS)

Review [minio-keeper.yaml](minio-keeper.yaml) and replace `storageClassName: ebs-sc` with a
CSI storage class that supports snapshots on your cluster.

> **Storage class**: `ebs-sc` is the AWS EBS CSI storage class used in our first test environment
> (the last test ran on kind with `csi-hostpath-sc`).
> Replace it with a storage class that supports CSI snapshots on your cluster
> (e.g. `managed-csi` on AKS, `standard-rwo` on GKE, your custom class on bare-metal, etc.).
> The class must have a matching `VolumeSnapshotClass` registered with Kasten.
> Do **not** use legacy in-tree classes (e.g. `gp2` on AWS) — they do not support CSI snapshots.

```bash
kubectl apply -f minio-keeper.yaml
kubectl wait deployment my-psmdb-psmdb-db-minio -n psmdb-test --for=condition=Available --timeout=3m
```

### 3. Create the bucket

```bash
kubectl run s3-init -n psmdb-test --image=amazon/aws-cli:2.42.4 --restart=Never --rm -i \
  --env=AWS_ACCESS_KEY_ID=minioadmin --env=AWS_SECRET_ACCESS_KEY=minioadmin123 \
  --env=AWS_DEFAULT_REGION=us-east-1 -- \
  --endpoint-url http://my-psmdb-psmdb-db-minio:9000 s3 mb s3://pbm-backup
```

### 4. Deploy the sharded PSMDB cluster

Review [psmdb-values.yaml](psmdb-values.yaml) and replace `storageClassName: ebs-sc` with your
CSI storage class (same requirement as above).

```bash
helm upgrade --install my-psmdb percona/psmdb-db \
  --namespace psmdb-test \
  --version 1.22.2 \
  -f psmdb-values.yaml \
  --wait --timeout=15m
```

> **Single-node clusters (kind, minikube).** The chart spreads the pods of each replica set over
> different nodes (pod anti-affinity), so on one node the second pod of each set stays `Pending`
> with `didn't match pod anti-affinity rules`. The test on kind added
> `--set replsets.rs0.affinity.antiAffinityTopologyKey=none --set sharding.configrs.affinity.antiAffinityTopologyKey=none --set sharding.mongos.affinity.antiAffinityTopologyKey=none`
> to the command above. Do not use this on a production cluster: it allows all replicas on the
> same node. The kind node had 8 GiB, shared with Kasten, so the test also lowered the memory
> **requests** (not the limits) with
> `--set replsets.rs0.resources.requests.memory=256Mi --set sharding.configrs.resources.requests.memory=128Mi --set sharding.mongos.resources.requests.memory=128Mi`.
> Without it, `mongos-0` stayed `Pending` with `Insufficient memory`.

Wait for the cluster to become ready:

```bash
kubectl wait psmdb my-psmdb-psmdb-db -n psmdb-test \
  --for=jsonpath='{.status.state}'=ready --timeout=15m
```

The cluster topology once ready:

| Component | Pods | Role |
|---|---|---|
| Config server rs | `my-psmdb-psmdb-db-cfg-0/1/2` | Config replica set |
| Shard rs0 | `my-psmdb-psmdb-db-rs0-0/1/2` | Data shard |
| Mongos | `my-psmdb-psmdb-db-mongos-0` | Query router |
| S3 keeper (RustFS) | `my-psmdb-psmdb-db-minio-*` | pbm archive storage |

### 5. Create test data

Connect via the mongos router:

```bash
DBADMIN_USER=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_USER}" | base64 -d)
DBADMIN_PASS=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_PASSWORD}" | base64 -d)

kubectl exec -n psmdb-test my-psmdb-psmdb-db-mongos-0 -c mongos -- \
  mongosh "mongodb://localhost:27017/admin" -u "$DBADMIN_USER" -p "$DBADMIN_PASS" \
  --quiet --eval '
db.getSiblingDB("testdb").customers.insertMany([
  {name:"Alice",email:"alice@example.com",plan:"gold"},
  {name:"Bob",email:"bob@example.com",plan:"silver"},
  {name:"Carol",email:"carol@example.com",plan:"gold"}
]);
print("count:", db.getSiblingDB("testdb").customers.countDocuments());
'
```

### Verify test data

```bash
kubectl exec -n psmdb-test my-psmdb-psmdb-db-mongos-0 -c mongos -- \
  mongosh "mongodb://localhost:27017/admin" -u "$DBADMIN_USER" -p "$DBADMIN_PASS" \
  --quiet --eval '
var docs = db.getSiblingDB("testdb").customers.find().toArray();
print("count:", docs.length);
docs.forEach(d => print(d.name, d.email, d.plan));
'
```

## Deploy the blueprint

```bash
kubectl apply -f blueprint.yaml
kubectl apply -f blueprintbinding.yaml
```

## Kasten policy configuration

Create a backup policy targeting the `psmdb-test` namespace with a **Location** profile
(required for Kanister phases). 

**Critical: exclude PSMDB data PVCs from the snapshot.** Only the MinIO PVC must be snapshotted.
All MongoDB data PVCs carry the label `app.kubernetes.io/name=percona-server-mongodb`. Configure
the policy's volume filter to exclude them:

```yaml
backupParameters:
  filters:
    excludeResources:
      - matchLabels:
          app.kubernetes.io/name: percona-server-mongodb
```

This filter serves two purposes:

- **Correctness at restore time** — if MongoDB data PVCs are restored alongside the MinIO PVC,
  the cluster would start with stale on-disk data that contradicts the pbm archive. The restore CR
  would then overwrite inconsistent data with the correct archive, but the operator behaviour
  during this transition is unpredictable.
- **Avoiding redundant storage** — all MongoDB data is fully represented in the pbm archive inside
  MinIO. Snapshotting the raw data PVCs adds cost with no recovery benefit: the MinIO PVC alone
  is sufficient for a complete restore.

## Verifying a backup

After a RunAction completes:

```bash
# Confirm the backup CR was created and completed
kubectl get psmdb-backup -n psmdb-test

# Check the pbm archive in the keeper
kubectl run s3-chk -n psmdb-test --image=amazon/aws-cli:2.42.4 --restart=Never --rm -i \
  --env=AWS_ACCESS_KEY_ID=minioadmin --env=AWS_SECRET_ACCESS_KEY=minioadmin123 \
  --env=AWS_DEFAULT_REGION=us-east-1 -- \
  --endpoint-url http://my-psmdb-psmdb-db-minio:9000 s3 ls s3://pbm-backup/

# Check pbm backup list via the backup-agent
kubectl exec -n psmdb-test my-psmdb-psmdb-db-rs0-0 -c backup-agent -- pbm list
```

## Restore

Restore is fully automated via the `restorePosthook`. Kasten restores the MinIO PVC (containing
the pbm archive), the PSMDB CR, Secrets, and other resources. Once the cluster reaches `ready`
state, the `restorePosthook` creates a `PerconaServerMongoDBRestore` CR and waits for it to
complete.

### Simulate data loss

Drop the test collection so the restore has something meaningful to bring back:

```bash
DBADMIN_USER=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_USER}" | base64 -d)
DBADMIN_PASS=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_PASSWORD}" | base64 -d)

kubectl exec -n psmdb-test my-psmdb-psmdb-db-mongos-0 -c mongos -- \
  mongosh "mongodb://localhost:27017/admin" -u "$DBADMIN_USER" -p "$DBADMIN_PASS" \
  --quiet --eval '
db.getSiblingDB("testdb").customers.drop();
print("count after drop:", db.getSiblingDB("testdb").customers.countDocuments());
'
```

### Triggering a RestoreAction

```bash
# List available restore points
kubectl get restorepoint -n psmdb-test

RESTORE_POINT=$(kubectl get restorepoint -n psmdb-test \
  -o jsonpath='{.items[-1].metadata.name}')

kubectl create -f - --validate=false <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata:
  generateName: restore-
  namespace: psmdb-test
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: ${RESTORE_POINT}
    namespace: psmdb-test
  targetNamespace: psmdb-test
  # No profile needed — Kasten extracts the location profile from the RestorePointContent.
EOF
```

### Verify restore

```bash
kubectl wait psmdb my-psmdb-psmdb-db -n psmdb-test \
  --for=jsonpath='{.status.state}'=ready --timeout=10m

DBADMIN_USER=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_USER}" | base64 -d)
DBADMIN_PASS=$(kubectl -n psmdb-test get secret my-psmdb-psmdb-db-secrets \
  -o jsonpath="{.data.MONGODB_DATABASE_ADMIN_PASSWORD}" | base64 -d)

kubectl exec -n psmdb-test my-psmdb-psmdb-db-mongos-0 -c mongos -- \
  mongosh "mongodb://localhost:27017/admin" -u "$DBADMIN_USER" -p "$DBADMIN_PASS" \
  --quiet --eval '
var docs = db.getSiblingDB("testdb").customers.find().toArray();
print("count:", docs.length);
docs.forEach(d => print(d.name, d.email, d.plan));
'
```

## pbm backup lifecycle and storage growth

Each Kasten backup creates one full pbm logical backup in MinIO. pbm does not yet support
incremental logical backups (physical incremental backups require WiredTiger checkpoints and
are available separately, but not via the operator CR mechanism).

**Storage growth**: each backup adds a full copy of all collection data. For test datasets this
is under 100 KiB; for production datasets size it accordingly.

**Garbage collection**: when a Kasten restore point is deleted, the blueprint `delete` action
runs `pbm delete-backup` inside the `backup-agent` container to remove the corresponding archive
from MinIO and deletes the `PerconaServerMongoDBBackup` CR. This keeps the live MinIO bucket
bounded to the number of active Kasten restore points.

## Tearing down

```bash
kubectl delete psmdb my-psmdb-psmdb-db -n psmdb-test 2>/dev/null || true
kubectl wait pods -n psmdb-test -l app.kubernetes.io/instance=my-psmdb-psmdb-db \
  --for=delete --timeout=3m 2>/dev/null || true
kubectl delete namespace psmdb-test
kubectl delete blueprint psmdb-pbm-blueprint -n kasten-io
kubectl delete blueprintbinding psmdb-pbm-blueprint-binding -n kasten-io
kubectl delete policy <YOUR_POLICY_NAME> -n kasten-io 2>/dev/null || true
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=psmdb-test

# (Optional) remove the operator — only if no other PSMDB cluster uses it
# helm uninstall psmdb-operator -n psmdb-operator && kubectl delete namespace psmdb-operator
```

---

## Initial prompt

> Create a blueprint for MongoDB deployed with the Percona operator but in sharded mode and using the
> backup/restore CR.
