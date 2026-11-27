# Elasticsearch (ECK) + MinIO Keeper — Kasten Blueprint

Pattern 5 (local MinIO keeper) for an ECK-managed Elasticsearch cluster.
Elasticsearch's native S3 snapshot repository writes to a local S3 server (the
keeper, which now runs RustFS — see
[why not MinIO](#why-the-keeper-runs-rustfs-and-not-minio)); Kasten snapshots
the keeper PVC, capturing the full ES snapshot archive in each restore point.

This blueprint is the **Pattern 5 alternative** to the existing
[`elasticsearch-eck-example/`](../elasticsearch-eck-example/) blueprint (Pattern 3 — permanent
workload PVC with shared filesystem repository). Use this one when:

- You don't want to mount an RWX repository PVC across all ES nodes.
- You want **per-restore-point ES snapshot lifecycle** (snapshots survive
  across backups, are deleted when the corresponding Kasten restore point
  is retired).

## Versions

Versions used for the last end-to-end test (backup with export, in-place restore,
restore after namespace deletion, `delete` action), on 2026-11-27:

| Component | Version |
|---|---|
| Kubernetes | `1.35` (kind, `csi-hostpath-sc` from the CSI hostpath driver) |
| Kasten | `9.0.11` |
| ECK operator | `2.16.1` |
| Elasticsearch | `8.17.0` |
| S3 keeper | RustFS `1.0.7` (`rustfs/rustfs:1.0.7`) |
| Keeper sidecar | `gcr.io/kasten-images/kanister-tools:9.0.11` |
| `delete` action image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.11` ([Dockerfile](../images/kasten-tools/Dockerfile)) |
| S3 client (bucket creation) | AWS CLI `2.42.4` (`amazon/aws-cli:2.42.4`) |

The blueprint was first developed on EKS `1.32` with Kasten `8.5.7` and a MinIO
keeper (`RELEASE.2025-04-22T22-12-26Z`).

Both image tags in the table must equal your Kasten version. Detect it with
`helm ls -n kasten-io`, then change the tag in [minio-keeper.yaml](minio-keeper.yaml)
and [blueprint.yaml](blueprint.yaml).

### Why the keeper runs RustFS and not MinIO

The MinIO images can no longer be pulled. On 2026-11-27, `quay.io/minio/minio` answered
`no such manifest`, and `minio/minio` and `minio/mc` on Docker Hub answered
`denied: requested access to the resource is denied`. A keeper Pod with the old image stays in
`ImagePullBackOff`.

The keeper now runs [RustFS](https://github.com/rustfs/rustfs), an S3-compatible server under the
Apache 2.0 license. Elasticsearch only needs the S3 API: the same repository settings
(`protocol: http`, `path_style_access: true`) work, and `POST /_snapshot/kasten-repo/_verify`
succeeds on every node. What changes in [minio-keeper.yaml](minio-keeper.yaml):

| MinIO | RustFS |
|---|---|
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` (same Secret keys `rootUser` / `rootPassword`) |
| `args: [server, /data, --console-address, ":9001"]` | `args: [/data]`, console disabled |
| probe `/minio/health/ready` | probe `/health/ready` |
| runs as root | runs as uid `10001`: the Pod sets `fsGroup: 10001` so it can write to the PVC |
| bucket created with `mc mb` inside the MinIO container | bucket created with `aws s3 mb` from a separate Pod (the RustFS image has no `mc`) |

The resource names still contain `minio` (`es-cluster-minio`, label `minio-s3-keeper`): the
blueprint builds the keeper Service name as `<workload>-minio`, and existing deployments keep
working with the same binding.

> **Existing MinIO keepers.** Replacing only the image of a running MinIO keeper, so that RustFS
> reads the MinIO data directory, was **not tested**. Deploy a new keeper, take a new backup with
> it, and keep the old restore points until they expire. They restore the old MinIO Deployment,
> which cannot start unless the MinIO image is still in a cache or a mirror you control.

## Pattern

**Pattern 5 — Database dump or snapshot via a local MinIO keeper.**

```
┌──────────────────────────────────────────────────────────────────┐
│  Namespace: elasticsearch-eck-minio                              │
│                                                                  │
│  Elasticsearch (3 nodes)         MinIO keeper Deployment         │
│  ┌──────────────────────┐        ┌──────────────────────┐        │
│  │ es-cluster-es-...    │ ──S3── │ es-cluster-minio     │        │
│  │ (data PVCs)          │ snap   │  ├─ minio container  │        │
│  └──────────────────────┘        │  └─ curl sidecar     │        │
│                                  │     (KubeExec target)│        │
│                                  └─────────┬────────────┘        │
│                                            │                     │
│                                  ┌─────────▼────────────┐        │
│                                  │ es-cluster-minio PVC │ ◄──┐   │
│                                  │ /data/es-snapshots/  │    │   │
│                                  └──────────────────────┘    │   │
└──────────────────────────────────────────────────────────────┼───┘
                                                               │
                                          Kasten CSI snapshot ─┘
```

- An S3 keeper `Deployment` (RustFS) with a permanent PVC stores the Elasticsearch S3
  snapshot archive. Kasten snapshots **only this PVC** (the ES data PVCs are
  excluded — they are recreated from the ES snapshot on restore).
- The keeper pod has a `tools` sidecar container (`kanister-tools`, which has
  `curl`, `kando` and `sync`) so that the blueprint can `KubeExec` into the
  keeper to talk to the Elasticsearch HTTP API. Using
  `KubeExec` into the keeper rather than a `KubeTask` in `kasten-io` lets the
  blueprint use `.Object.metadata.labels` / `.Object.metadata.namespace`
  directly — the keeper Deployment **is** the BlueprintBinding context.
- Workload identity (ES cluster name + kind) is carried as labels on the
  keeper Deployment so that one generic blueprint can serve many keepers.

### Blueprint actions

| Action | What it does |
|---|---|
| `backupPrehook` | `KubeExec` into the keeper's `tools` sidecar → `PUT /_snapshot/kasten-repo/kasten-<timestamp>?wait_for_completion=true` on the Elasticsearch HTTP API to create a new native snapshot, checks `state=SUCCESS`, runs `sync` (the last step before the snapshot: it flushes the keeper writes from the page cache to the disk), then emits the snapshot name as a `kastenSnapshot` artifact. |
| `backupPosthook` | No-op (placeholder for symmetry). |
| `restorePosthook` | Where the actual recovery happens. Waits for the ES cluster to be reachable after Kasten restores the keeper PVC, re-registers the snapshot repository (idempotent), deletes the live copies of the snapshot's user indices, then `POST /_snapshot/kasten-repo/<snapshot>/_restore` with `include_global_state: false`. Polls until the restore completes. Works identically whether the cluster is live (in-place) or freshly rebuilt by ECK (DR). |
| `delete` | Called by Kasten when a restore point is retired. Receives the `kastenSnapshot` artifact and issues `DELETE /_snapshot/kasten-repo/<snapshot>`. Idempotent — treats `snapshot_missing_exception` as success. |

## Snapshot lifecycle (no per-backup cleanup)

Unlike [`elasticsearch-eck-example/`](../elasticsearch-eck-example/), this blueprint **never
deletes Elasticsearch snapshots during a backup run**. Each backup creates a
new ES snapshot with a unique name; the snapshot's identity is stored as a
Kanister artifact alongside the Kasten restore point. When Kasten retires the
restore point, the `delete` action removes the corresponding ES snapshot from
the live MinIO bucket.

The total size of the live MinIO bucket grows in proportion to the number of
live Kasten restore points (not to the absolute backup count), since each
retired restore point causes its ES snapshot to be deleted.

## Prerequisites

- ECK operator installed in `elastic-system`.
- A CSI storage class that supports volume snapshots. Examples used here:
  - `ebs-sc` — AWS EBS CSI on EKS.

  > **Storage class**: `ebs-sc` is the AWS EBS CSI storage class used in our
  > first test environment (the last test ran on kind with `csi-hostpath-sc`). Replace it with a storage class that supports CSI
  > snapshots on your cluster (e.g. `managed-csi` on AKS, `standard-rwo` on
  > GKE, your custom class on bare-metal). The class must have a matching
  > `VolumeSnapshotClass` registered with Kasten. Do **not** use legacy
  > in-tree classes (e.g. `gp2`) — they do not support CSI snapshots.

## Deployment

### 1. Create the namespace and the S3 keeper (RustFS)

```bash
kubectl create ns elasticsearch-eck-minio
kubectl apply -f minio-keeper.yaml
kubectl wait --for=condition=Available --timeout=180s \
  deployment/es-cluster-minio -n elasticsearch-eck-minio
```

### 2. Create the bucket used by Elasticsearch

```bash
kubectl run s3-init -n elasticsearch-eck-minio --image=amazon/aws-cli:2.42.4 \
  --restart=Never --rm -i \
  --env=AWS_ACCESS_KEY_ID=minioadmin --env=AWS_SECRET_ACCESS_KEY=minioadminpassword \
  --env=AWS_DEFAULT_REGION=us-east-1 -- \
  --endpoint-url http://es-cluster-minio:9000 s3 mb s3://es-snapshots
```

Expected: `make_bucket: es-snapshots`.

### 3. Create the Elasticsearch cluster

```bash
kubectl apply -f elasticsearch.yaml
kubectl wait --for=jsonpath='{.status.health}'=green --timeout=600s \
  elasticsearch/es-cluster -n elasticsearch-eck-minio
```

> **Single-node test cluster (kind).** Three Elasticsearch nodes with the ECK default memory
> (`2Gi` each) do not fit on a small single-node cluster that already runs Kasten: the pods stay
> `Pending` with `Insufficient memory`. For the kind test we applied a copy of
> [elasticsearch.yaml](elasticsearch.yaml) with `count: 2` (enough for `number_of_replicas: 1`
> and a `green` cluster) and this `podTemplate` under the node set. The repository file is
> unchanged.
>
> ```yaml
>       podTemplate:
>         spec:
>           containers:
>             - name: elasticsearch
>               env:
>                 - name: ES_JAVA_OPTS
>                   value: "-Xms512m -Xmx512m"
>               resources:
>                 requests: {memory: 1Gi, cpu: 250m}
>                 limits: {memory: 1Gi}
> ```
>
> `node.store.allow_mmap: false` is already set in the file, so no `vm.max_map_count` change
> is needed on the node.

### 4. Register the S3 snapshot repository (one-time setup)

The repository is registered once at deployment time and **not** managed by the
blueprint. Re-running this command is safe (PUT is idempotent).

```bash
ELASTIC_PASS=$(kubectl get secret es-cluster-es-elastic-user \
  -n elasticsearch-eck-minio -o jsonpath='{.data.elastic}' | base64 -d)

kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" \
       -X PUT "https://localhost:9200/_snapshot/kasten-repo" \
       -H 'Content-Type: application/json' \
       -d '{
             "type": "s3",
             "settings": {
               "bucket": "es-snapshots",
               "endpoint": "es-cluster-minio.elasticsearch-eck-minio.svc:9000",
               "protocol": "http",
               "path_style_access": true
             }
           }'
echo
```

Expected response: `{"acknowledged":true}`.

### 5. Create test data (small dataset for fast dev cycle)

```bash
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" \
       -X PUT "https://localhost:9200/test-index" \
       -H 'Content-Type: application/json' \
       -d '{"settings":{"number_of_shards":1,"number_of_replicas":1}}'
echo

for i in 1 2 3 4 5; do
  kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
    curl -sk -u "elastic:${ELASTIC_PASS}" \
         -X POST "https://localhost:9200/test-index/_doc" \
         -H 'Content-Type: application/json' \
         -d "{\"id\":${i},\"msg\":\"hello number ${i}\"}"
  echo
done

kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" \
       -X POST "https://localhost:9200/test-index/_refresh"
echo
```

Verify the documents are searchable:

```bash
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" \
       "https://localhost:9200/test-index/_search?pretty"
```

## Blueprint deployment

Apply the Blueprint and BlueprintBinding to `kasten-io`:

```bash
kubectl apply -f blueprint.yaml
kubectl apply -f blueprintbinding.yaml
```

The BlueprintBinding matches any `Deployment` carrying the label
`minio-s3-keeper=true` that does **not** already have a
`kanister.kasten.io/blueprint` annotation — so any other keeper Deployment
intended for a non-Elasticsearch S3-protocol workload (e.g.
[`cnpg-barman-example/`](../cnpg-barman-example/) uses its own label `cnpg-barman-minio`)
is unaffected.

Then create the Kasten backup policy. The policy needs:

1. A **Location profile** (not Infra) for both `backup` and `export` actions —
   blueprint phases need it, and DR restores need the data in S3.
2. An **`export` action** with `exportData: enabled: true` so the restore
   point survives namespace deletion. Without export, the in-cluster CSI
   snapshots are deleted along with their VolumeSnapshot CRs when the
   namespace is gone (`deletionPolicy: Delete` is the default on most CSI
   classes), and DR restores fail with `InvalidSnapshot.NotFound`.
3. An `excludeResources` filter that excludes **every ECK-managed resource**
   (label `common.k8s.elastic.co/type=elasticsearch`) — but **keeps the
   Elasticsearch CR**. ECK regenerates StatefulSet, Services, Secrets,
   ConfigMaps, Pods, and data PVCs from the CR on restore. Restoring them
   from the backup interferes with ECK's reconciliation (see "Why exclude
   ECK-managed resources" below).

```yaml
spec:
  actions:
    - action: backup
      backupParameters:
        profile:
          name: <location-profile>
          namespace: kasten-io
        filters:
          excludeResources:
            # Exclude every ECK-managed resource. ECK rebuilds them from
            # the ES CR on restore. The ES CR itself does NOT carry this
            # label and stays in the backup.
            - matchLabels:
                common.k8s.elastic.co/type: elasticsearch
    - action: export
      exportParameters:
        profile:
          name: <location-profile>
          namespace: kasten-io
        frequency: '@onDemand'
        exportData:
          enabled: true
```

### Why exclude ECK-managed resources (but keep the ES CR)

ECK normally creates and reconciles the StatefulSet, ES Services, ES
Secrets, ES ConfigMaps, ES Pods, and data PVCs — all derived from the
Elasticsearch CR. If the backup captures those derived resources and the
restore re-applies them:

- The restored StatefulSet creates pods bound to **either** the restored
  ES data PVCs (in-place) **or** fresh empty ones (DR). In the in-place
  case the pods read "I was node X in cluster [X,Y,Z]" from the persisted
  state and refuse to elect a master without quorum; ECK then scales the
  STS down to 1 in a "limit master node creation" safety, and the cluster
  deadlocks.
- In the DR case the restored objects pre-empt ECK's reconciliation —
  Kasten waits for the restored pods to be Ready before restoring the ES
  CR, but the pods can never be Ready without the CR's bootstrap config.

Excluding the ECK-managed resources lets the ES CR be the **single source
of truth** on both paths: ECK sees the (re-)applied CR, builds a fresh
StatefulSet with the correct bootstrap config, creates fresh empty data
PVCs, and elects a master cleanly. `restorePosthook` then orchestrates the
ES snapshot restore via the REST API to re-hydrate the data.

## Restore

From a user's point of view there is **one restore procedure**: select a
restore point in the Kasten dashboard and restore it. The same flow covers
both an in-place rollback and a full disaster recovery — the only thing
that differs is whether the namespace still exists, and Kasten handles that
transparently. There is **nothing to delete by hand**; all the recovery
logic lives in `restorePosthook`.

### Why no CR deletion

It is tempting to delete the live Elasticsearch CR before restoring (to let
ECK rebuild a clean cluster). That step is **unnecessary and harmful**, for
three reasons:

1. **Kasten never overwrites the live CR** — by default restore skips any
   resource that already exists (unless you explicitly choose "overwrite
   existing"). So the CR is left untouched whether or not you delete it.
2. **The StatefulSet is excluded by the backup filter**
   (`common.k8s.elastic.co/type=elasticsearch`), so Kasten never restores it
   and never contends with ECK over it.
3. **`restorePosthook` does the actual recovery through the ES REST API** —
   it deletes the affected indices and restores them from the MinIO
   snapshot. This works directly against the live, healthy cluster with
   **zero downtime**.

Deleting the CR would tear down a healthy cluster and force a full ECK
rebuild (fresh empty data PVCs) just to re-hydrate a few indices — slower
and riskier, for no benefit. An earlier version of this blueprint deleted the
CR before restoring; that step has been removed.

### In-place rollback (live cluster still up)

Use case: rollback after data loss inside an otherwise-healthy cluster
(dropped index, bad migration, etc.).

Trigger the restore from the Kasten dashboard (or a `RestoreAction`).
Kasten restores the keeper PVC; the live ES CR and StatefulSet are left
alone; `restorePosthook` deletes the affected indices and restores them
from the snapshot. The cluster is never torn down.

### Disaster recovery / migration (namespace destroyed, fresh cluster)

Use case: full namespace loss, cross-cluster migration, restore into a new
cluster after a regional outage.

Restore from the **exported** restore point in the Kasten dashboard — the
UI re-creates the namespace, the `RestorePoint`, and the `RestoreAction`
for you (no manual API objects needed). Kasten restores the ES CR + the
keeper; ECK rebuilds the cluster from scratch with fresh empty PVCs;
`restorePosthook` waits for ES to be reachable, **(re-)registers the
snapshot repository** (idempotent — needed because the rebuilt cluster's
state is brand-new), and restores the user indices from the snapshot.

> If you prefer the CLI over the dashboard, see
> [AGENTS.md / Troubleshooting → Triggering a RestoreAction](../AGENTS.md)
> for the `RestorePoint` + `RestoreAction` manifests. Point the
> `RestorePoint` at the exported `RestorePointContent` (the one carrying a
> `k10.kasten.io/exportProfile` label) so the restore survives namespace
> deletion.

Verified end-to-end: a 5-doc `test-index` was recovered identically after
`kubectl delete ns elasticsearch-eck-minio`.

## End-to-end test (in-place flow)

```bash
# 1. Trigger a backup via Kasten policy (UI or RunAction)
#    Wait for both backup AND export to reach Complete.

# 2. Drop test-index to simulate data loss
ELASTIC_PASS=$(kubectl get secret es-cluster-es-elastic-user \
  -n elasticsearch-eck-minio -o jsonpath='{.data.elastic}' | base64 -d)
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" -X DELETE "https://localhost:9200/test-index"

# 3. Trigger a Kasten restore (dashboard or RestoreAction). No CR deletion
#    needed — restorePosthook re-hydrates the index via the ES REST API.

# 4. After restore completes, verify documents are back.
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" -X POST "https://localhost:9200/test-index/_refresh"
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" "https://localhost:9200/test-index/_count"
# Expected: {"count":5,...}

# 5. Retire the restore point to trigger the blueprint's `delete` action.
#    NOTE: `kubectl delete restorepoint <name>` alone does NOT fire the
#    delete action — Kasten only invokes it when the underlying
#    RestorePointContent (RPC) is removed. Either delete the RPC directly
#    or use the Kasten dashboard / RetireAction:
RPC=$(kubectl get restorepointcontent \
  -l k10.kasten.io/appNamespace=elasticsearch-eck-minio \
  -o jsonpath='{.items[0].metadata.name}')
kubectl delete restorepointcontent "${RPC}"
# Watch the delete actionset run and confirm the ES snapshot was removed:
kubectl exec -n elasticsearch-eck-minio sts/es-cluster-es-default -- \
  curl -sk -u "elastic:${ELASTIC_PASS}" \
       "https://localhost:9200/_snapshot/kasten-repo/_all"
```

## Teardown

```bash
# Delete any Kasten restore points that point at this app first, then:
kubectl delete -f blueprintbinding.yaml --ignore-not-found
kubectl delete -f blueprint.yaml --ignore-not-found
kubectl delete -f elasticsearch.yaml --ignore-not-found
kubectl delete -f minio-keeper.yaml --ignore-not-found
kubectl delete ns elasticsearch-eck-minio --ignore-not-found
kubectl delete policy <your-policy> -n kasten-io --ignore-not-found

# Clean up retained Kasten snapshot contents (replace selector if needed)
kubectl delete restorepointcontent \
  -l k10.kasten.io/appNamespace=elasticsearch-eck-minio --ignore-not-found
```

## Status

All lifecycle phases pass end-to-end:

- **Backup** — `backupPrehook` creates a timestamped ES snapshot
  (`kasten-YYYYMMDDHHMMSS`), syncs the keeper filesystem, emits the
  snapshot name as a Kanister artifact. Kasten then snapshots the MinIO
  PVC and exports the data to S3. The ES data PVCs are never touched.
- **In-place rollback** — no CR deletion, no cluster downtime. Kasten
  restores the keeper PVC; the live ES CR and StatefulSet are untouched;
  `restorePosthook` deletes the affected indices and re-hydrates them
  from the snapshot via the ES REST API.
- **DR restore** — `kubectl delete ns elasticsearch-eck-minio` + restore
  from the **exported** RPC (the Kasten UI re-creates the namespace and
  restore objects). ECK builds the cluster from scratch from the restored
  CR; `restorePosthook` (re-)registers the snapshot repo on the brand-new
  cluster and restores the user indices. Verified: 5-doc `test-index`
  recovered identically after full namespace delete.
- **Delete** — When the RestorePointContent is removed, Kasten invokes
  the blueprint's `delete` action, which calls
  `DELETE /_snapshot/kasten-repo/<snapshot>` against the live ES.
  Verified end-to-end against a real Kasten policy.

---

## Initial prompt

> Create a blueprint for Elasticsearch but unlike elasticsearch-eck implement pattern 5 (like
> cnpg-barman) with MinIO keeper. Also contrary to elasticsearch-eck do not delete all the
> Elasticsearch snapshots at each backup. The Elasticsearch snapshot is deleted by the delete action
> invoked each time a restore point is retired.
