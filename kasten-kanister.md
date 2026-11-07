# Kasten + Kanister — Reference Documentation

## Overview

Kasten K10 uses [Kanister](https://kanister.io) Blueprints — Kubernetes Custom Resources — to execute application-specific logic around backup and restore operations. In the Kasten context, blueprints serve two purposes:

1. **Application hooks** — attached to a resource (StatefulSet, Deployment, custom resource, and so on) through annotation or BlueprintBinding. Kasten calls reserved action names at fixed points in its backup/restore lifecycle.
2. **Action hooks** — defined in the spec of a Kasten Action (BackupAction, RestoreAction, or ExportAction). ImportAction does not support hooks. The context is the namespace, not a specific resource.

---

## Vocabulary

 - **Fencing a database**: Isolate the database from the network (put a fence around it) and ensure that all transactions are flushed to disk for a consistent capture
 of its storage. The way you fence a database does not belong to Kasten specifically
 but to the database vendor. In this [example](https://github.com/kastendevhub/enterprise-blueprint/blob/efe6ef727d813ef54c45cde825443833e6e32bd7/edb/edb-hooks.yaml#L26C122-L26C140) the blueprint invokes a script in a pod that the database vendor has advertised in an annotation. Only database vendor documentation
 can confirm if fencing is supported and how.
 - **Database Snapshot**: A filesystem dump. When the next dump runs, only files
 that materialize the changes are appended. Example: Cassandra nodetool snapshot or
 Elasticsearch snapshot.
 - **Fleet automation**: Apply the same workflow for workloads that
 have the same type. For instance, use BlueprintBinding to make sure that all CNPG
 PostgreSQL clusters are backed up using the same blueprint.

## Invocation Mode 1 — Resource Annotation / BlueprintBinding

### PVC Discovery and Ownership Chain

Kasten supports not only standard workloads (StatefulSet, Deployment) but also operator-managed custom resources such as `postgresql.k8s.enterprisedb.io/Cluster` that directly control their own pods.

Discovery works as follows:

1. Kasten finds PVCs mounted by running pods in the namespace.
2. For each pod, Kasten walks the **owner reference chain** upward (pod → ReplicaSet → Deployment, or pod → StatefulSet, or pod → custom resource, and so on) until it reaches the root owner.
3. If a blueprint is annotated on **any resource in that ownership chain**, Kasten associates all PVCs transitively owned by that resource with the blueprint.
4. `backupPrehook` is called **before** any of those PVCs snapshots are initiated.
5. `backupPosthook` is called **after** all of those PVCs snapshots are ready.

A blueprint attached to a high-level custom resource (for example, an EDB `Cluster`) will correctly scope hook execution to all PVCs in that ownership subtree, even if pods and PVCs are several levels below.

> **Critical timing constraint — PVC discovery runs before `backupPrehook`.**
> Kasten discovers and registers all PVCs for snapshotting **at backup-action start**, before
> `backupPrehook` executes. Any PVC created during `backupPrehook` is therefore invisible to the
> snapshot phase and will **not** appear in the restore point. This has a direct impact on pattern
> selection:
>
> - **Permanent PVCs** pre-exist at discovery time → they are correctly snapshotted. A
>   BlueprintBinding on the workload that owns (or mounts) the PVC is sufficient.
> - **Temporary PVCs** (created inside `backupPrehook`) are never discovered → by default they
>   are never snapshotted. Two ways make a temporary PVC visible to Kasten:
>   - **Late capture** (Kasten `9.0.10` or later, feature flag `features.lateCapture=true`):
>     `backupPrehook` *declares* the PVC in a `lateCapture` output artifact, and Kasten adds it to
>     the snapshot set after the hook. The blueprint stays resource-bound and works with
>     BlueprintBinding. See [Late Capture](#late-capture--hook-created-resources-in-the-restore-point).
>   - **BackupAction preHook** (action hook), which runs before PVC discovery. Namespace context
>     only, no BlueprintBinding. See [Invocation Mode 2 — Action Hooks](#invocation-mode-2--action-hooks)
>     and the [action-hook patterns](#action-hook-patterns--temporary-pvc-created-before-discovery).
>
> **Labels and annotations added to PVCs during `backupPrehook` are not reflected in the restore
> point.** Kasten captures the PVC spec at discovery time (before `backupPrehook` runs). When
> restoring, Kasten recreates PVCs from those captured specs — so any label added to a PVC inside
> `backupPrehook` will not appear on the restored PVC. If you need to track which
> PVC was quiesced or modified during backup, use a **Kanister artifact** (`kando output` /
> `outputArtifacts`) — artifacts are stored alongside the restore point and remain available to
> `restorePosthook` through `inputArtifactNames`.
>
> Note: Kasten also snapshots **orphan PVCs** (PVCs not mounted by any running pod). However,
> since there is no ownership chain to attach a blueprint to, no `backupPrehook` runs for orphan
> PVCs through a BlueprintBinding. If backup data must be written to the PVC before the snapshot,
> you must use a keeper Deployment (pattern 4 below), a staging PVC with late capture (pattern 6),
> or an action preHook. An orphan PVC
> with no prehook is only valid when the PVC already holds valid, up-to-date backup data that
> does not need to be refreshed before snapshotting.
>
> 📖 **Official reference:** the exact ordering described here — PVC discovery running before any
> per-resource hook — is documented in
> [Phase Lifecycle → Per-Resource Backup Processing](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#per-resource-backup-processing)
> (discovery runs in step 3, before per-resource processing in step 4; a PVC created during
> `backupPrehook` is not captured). See also
> [Phase Lifecycle → Namespace-Level Backup Phases](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#namespace-level-backup-phases)
> for the namespace-level `preHook` that runs *before* discovery.

### Permanent PVC Discovery — Three Mechanisms

A permanent PVC is always pre-existing when Kasten runs its discovery pass. However, a
BlueprintBinding targets a **workload resource**, not a PVC directly. How Kasten links the PVC
to a BlueprintBinding depends on whether the PVC is mounted by the target workload. These three
mechanisms map directly to patterns 3, 4, and 5 in the Blueprint patterns section below:

**Pattern 3 — PVC mounted by the workload pod (preferred)**

The permanent backup PVC is added to the workload's pod spec as an extra volume mount. Kasten
discovers it transitively through the owner reference chain. The BlueprintBinding targets the
workload CR (StatefulSet, Deployment, or custom resource). No extra deployment is needed.
The `backupPrehook` uses `KubeExec` to run backup commands directly in the workload pod, or
spawns a short-lived pod through `KubeTask` if a separate tool image is needed.
Example: Elasticsearch ECK, where the snapshot repository PVC is declared in the Elasticsearch CR.

**Pattern 4 — PVC not mounted by the workload pod (dedicated keeper Deployment)**

The permanent backup PVC exists as a standalone resource, not mounted by the workload's pods at
rest. Create a **keeper Deployment** that runs the backup tool image (for example, `couchbase/server:7.6.6`
for Couchbase, or a custom image containing the database dump tool) and mounts the backup PVC
permanently. This serves two purposes:

- Kasten discovers the PVC through the Deployment's ownership chain and snapshots it every run.
- The `backupPrehook` uses `KubeExec` into the keeper pod to run backup commands directly —
  no need to create/delete a temporary pod per backup cycle, and no `ReadWriteOnce` conflict
  because the PVC is already mounted by the keeper.

Apply a BlueprintBinding to this keeper Deployment (not to the main workload CR). The keeper
Deployment does not need to do anything at runtime — it simply keeps the PVC mounted so Kasten
can discover it and so that `KubeExec` has a stable target. Use a lightweight long-running command
such as `sleep infinity` if the image does not have a natural entrypoint.

**Pattern 5 — Local MinIO keeper (S3-protocol data movers)**

For workloads with a native S3-compatible backup mechanism (for example, pgBackRest, wal-g, Cassandra
Medusa), a MinIO Deployment in the application namespace acts as the local S3 endpoint, backed by
a permanent PVC. Kasten discovers the PVC through the MinIO Deployment's ownership chain. The
blueprint is bound to the MinIO Deployment, not to the main workload CR.

See [Pattern 5](#5-database-dump-or-snapshot-through-local-minio-keeper-pvc-mounted-by-keeper-keeper-exposes-s3-protocol) in the
Blueprint patterns section for the full description, deployment YAML, BlueprintBinding, and phase
skeleton.

### Late Capture — Hook-Created Resources in the Restore Point

> ⚠️ **Kasten `9.0.10` or later, and only with the feature flag on:**
>
> ```bash
> helm upgrade k10 kasten/k10 -n kasten-io --reuse-values --set features.lateCapture=true
> kubectl get cm k10-features -n kasten-io -o jsonpath='{.data.lateCapture}'   # must print true
> ```
>
> **Without the flag, nothing fails.** Kasten logs a warning (*"Late-capture declarations present
> but the lateCapture feature flag is not set; ignoring them"*) and continues. The staging PVC is
> created, never snapshotted, and deleted by `backupPosthook`. The restore point is green and holds
> no dump. Check the flag before you trust a late-capture blueprint.

Late capture lets a **resource-bound** `backupPrehook` create a PVC, a ConfigMap or a custom
resource and still have it in the restore point, although discovery ran before the resource
existed. It replaces the BackupAction preHook for this purpose, so the blueprint keeps the
resource context (`.StatefulSet.Name`, `.Object`, and so on) and works with BlueprintBinding.

**Declaration.** Any `backupPrehook` output artifact whose name starts with `lateCapture`
(case-sensitive) is a declaration. One artifact declares one resource; use `lateCaptureDump`,
`lateCaptureCR`, and so on for several. `KubeOps` returns the five fields as phase outputs:

```yaml
actions:
  backupPrehook:
    outputArtifacts:
      lateCapture:
        keyValue:
          apiVersion: "{{ .Phases.createStagingPVC.Output.apiVersion }}"  # version only: v1
          group: "{{ .Phases.createStagingPVC.Output.group }}"            # empty for core
          resource: "{{ .Phases.createStagingPVC.Output.resource }}"      # persistentvolumeclaims
          name: "{{ .Phases.createStagingPVC.Output.name }}"
          namespace: "{{ .Phases.createStagingPVC.Output.namespace }}"
```

Only `backupPrehook` artifacts count. A declaration in `backupPosthook` or `backup` is ignored,
because it would come after the snapshots.

**Where it runs in the per-resource sequence:**

| Step | What happens |
|---|---|
| 4a | `backupPrehook` creates the resource, writes the dump, declares it |
| 4b | **Late capture**: Kasten reads the `lateCapture*` artifacts, adds declared PVCs to the snapshot set, captures ConfigMaps and CRs as specs |
| 4c | Volume snapshots (the workload's PVCs **and** the declared PVCs) |
| 4d | `backupPosthook` — always runs, even if 4c failed. Must delete the staging PVC |
| 4e | **Staging PVC check**: each declared PVC must be gone or terminating, otherwise the backup fails |

**Supported resources:** PVCs, ConfigMaps, custom resources. **Not** Secrets, and not CRs that
Kasten restores with dedicated handling (KubeVirt VMs, CDI DataVolumes, NetworkAttachmentDefinitions).

**Requirements for a declared PVC** — each one fails the whole backup if not met:

| Requirement | Why |
|---|---|
| The blueprint is bound to a resource that **owns pods** (StatefulSet, Deployment, operator CR) | A blueprint bound to a ConfigMap or Secret has no volume snapshot step. Kasten fails with *"Late capture supports PVC declarations only in workload-bound blueprints"* rather than restore an empty volume. This rules out late capture for an external database bound to a ConfigMap |
| The blueprint has **no `backup` action** | A `backup` action replaces volume snapshots, so the PVC would never be snapshotted |
| The PVC is **Bound** when `backupPrehook` ends | With `WaitForFirstConsumer` it binds only when a pod mounts it — the dump phase does that |
| `Filesystem` volume mode, CSI-provisioned, CSI snapshots (no generic volume snapshot) | |
| Same namespace as the bound workload; namespace-scoped application backup (not a VM backup) | |
| The policy's resource filters do **not** exclude it | Check this when the policy excludes the application PVCs by label: the staging PVC must not carry that label |
| The PVC **did not exist at discovery** | See "Leftover PVC" below |
| `backupPosthook` **deletes** it | See "Why the PVC must be deleted" below |

ConfigMaps and CRs have none of the PVC restrictions. If discovery already captured them, the
late-captured copy replaces the discovered one, so they may stay on the cluster between backups.

**Why the PVC must be deleted after each backup.** A staging PVC left on the cluster is
discovered by the next backup and snapshotted **before** the next `backupPrehook` writes a new
dump. The restore point would hold the old dump. Kasten enforces the deletion twice (verified in
the `9.0.10` source, `k10/kio/exec/phases/backup/late_capture.go`):

1. **After `backupPosthook` (step 4e)** — `GET` on each declared PVC name. `NotFound` or a set
   `deletionTimestamp` passes; a PVC still present fails the backup with *"Late-captured PVC was
   not deleted by the backupPosthook; staging PVCs must be cleaned up"*. The check is skipped when
   an earlier hook already failed (the backup fails anyway). It only looks at **declared names**,
   so a PVC with a generated name left behind by an earlier run is not detected — it is simply
   snapshotted at every backup until someone deletes it.
2. **At capture time (step 4b)** — if discovery already recorded the declared PVC in this backup
   (the PVC existed before the hook), the backup fails with *"Late-captured PVC was snapshotted
   before the pre-backup hook; its data in this restore point would be stale"*. The same message
   comes from a commit-time check when the same PVC name appears twice in the snapshot set.

**Leftover PVC after a crash or a cancel.** `backupPosthook` did not run, so the PVC is still
there. **With a deterministic name, exactly one backup fails, and the next one succeeds.** Tested
on 9.0.10 with a `Bound` leftover holding a stale file: the next backup failed with *"Late-captured
PVC was snapshotted before the pre-backup hook; its data in this restore point would be stale"*
(its `backupPrehook` also failed, because `KubeOps create` found the PVC). `backupPosthook` runs
even when the backup fails, and it deleted the leftover by name, so the backup after that completed
normally. No manual step is needed, but one scheduled backup is lost.

A "delete if exists" guard at the start of `backupPrehook` does not save that backup: discovery
has already recorded the PVC before the guard runs. Use a **deterministic name**
(`{{ .StatefulSet.Name }}-dump`): with a generated name, `backupPosthook` deletes only the PVC of
the current run, and the leftover is snapshotted at every backup until someone deletes it.

A leftover that was never mounted (`Pending`) fails discovery instead, with *"PVC not bound"*.
Delete it by hand.

**Restore.** The restore point carries the declared PVC (with its snapshot) and the
`lateCapture` artifact. Kasten restores the PVC like any other. `restorePosthook` lists the
artifact in `inputArtifactNames`, mounts the PVC by `{{ .ArtifactsIn.lateCapture.KeyValue.name }}`,
replays the dump, then **deletes the restored PVC** — otherwise the next backup fails on it.

**Spec only, never status.** A late-captured CR is restored through the Kubernetes API, which
drops `status` on create. Values that live in a CR's status (a backup ID, a stanza name) must
also be recorded as plain output artifacts.

**Incrementality.** Each backup snapshots a **new volume**. On drivers where snapshot
incrementality follows the volume's history (EBS, for example), every local snapshot is a full
copy of the dump. Only the export to a location profile can deduplicate, because Kopia
deduplicates by content — and only when the changes are grouped. Measured on a 28 MB MongoDB
dump ([mongodb-logical-late-capture-example](mongodb-logical-late-capture-example/README.md#step-6--export-deduplication)):
1% of records **added** exported 12% of the dump; 1% **updated at random positions** exported
100%, because the changes touched every chunk. `gzip` did not change either result. A tool that needs its previous backup on the volume (an incremental
chain, append-only snapshot files) cannot use a staging PVC: it is empty at every backup.

Reference blueprints: [mongodb-logical-late-capture-example](mongodb-logical-late-capture-example/),
[mysql-logical-late-capture-example](mysql-logical-late-capture-example/),
[postgresql-logical-late-capture-example](postgresql-logical-late-capture-example/) and
[mssql-logical-late-capture-example](mssql-logical-late-capture-example/) — ports of the Kasten
documentation's logical backup examples.

### Reserved Action Names — Backup

| Action | When Kasten calls it | Typical use |
|---|---|---|
| `backupPrehook` | **Before** the attached PVCs snapshots are initiated and after the preHook associated action| Quiesce the application, or create a dump PVC |
| `backup` | When the blueprint fully manages data movement (no PVC snapshot) | Avoid in Kasten context — use PVC snapshots instead |
| `backupPosthook` | **After** the PVC snapshots are ready and before the onSuccess/onFailure associated action | Unquiesce the application, or delete the temporary dump PVC |

> 📖 **Official reference:** [Reserved Action Names → Backup Action Names](https://docs.kasten.io/latest/kanister/reference/action-names#backup-action-names)
> and [Phase Lifecycle → Per-Resource Backup Processing](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#per-resource-backup-processing)
> (step 4a `backupPrehook` → step 4b snapshots/`backup` → step 4c `backupPosthook`, which always runs).

### Reserved Action Names — Restore

| Action | When Kasten calls it | Typical use |
|---|---|---|
| `restore` | When the blueprint fully manages data movement | Avoid in Kasten context |
| `restorePosthook` | **After** the PVCs are restored and after restore of CR metadata and before the onSuccess/onFailure associated action | Post-restore initialization, cache warming |

> Any preparation the operator must perform before triggering a restore (pausing an operator,
> scaling down, deleting a CR, and so on) is documented as manual steps in each blueprint's `README.md`.

> **Any action name other than the recognized ones above is silently ignored by Kasten.**

> 📖 **Official reference:** [Reserved Action Names → Restore Action Names](https://docs.kasten.io/latest/kanister/reference/action-names#restore-action-names)
> and [Phase Lifecycle → Per-Resource Restore Processing](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#per-resource-restore-processing).

> **Default action timeouts** (overridable through Helm values — see
> [Reserved Action Names → Timeout Configuration](https://docs.kasten.io/latest/kanister/reference/action-names#timeout-configuration)):
>
> | Action | Helm value | Default |
> |---|---|---|
> | hook actions (`*Prehook` / `*Posthook`) | `timeout.blueprintHooks` | 20 min |
> | `backup` | `timeout.blueprintBackup` | 45 min |
> | `restore` | `timeout.blueprintRestore` | 600 min |
> | `delete` | `timeout.blueprintDelete` | 45 min |

> **`backupPrehook` only fires when the bound resource exists in the live namespace** — which is
> always true by construction, since you cannot back up a workload that does not exist.

## Backup and Restore Workflow Managed by Kasten

When a blueprint defines `backup` and `restore` actions, the blueprint itself becomes the data
mover: it uses `kando location push` / `kando location pull` to transfer data directly to and
from the location profile, bypassing Kasten's own pipeline. In that mode Kasten only captures
the PVC *manifests* — not the data on the PVCs. This approach has six serious drawbacks that
make it unsuitable for production blueprints:

1. **Custom image required.** `kando` is only distributed inside
   `gcr.io/kasten-images/kanister-tools`. To use `kando location push` the blueprint must run
   in a container that also has the database dump tools (`mongodump`, `pg_dump`, and so on). You
   must build and maintain a combined image. `MultiContainerRun` moderates this by letting you
   sidecar the dump tool next to `kanister-tools`, but that adds its own complexity (pod
   lifecycle, shared volumes, inter-container coordination) compared to a simple `KubeExec`
   into the existing workload container.

2. **No immutability.** Kasten enforces immutability on the restore point objects it manages.
   Data pushed through `kando location push` sits outside that tracking — it can be overwritten or
   deleted without Kasten knowing, silently breaking a restore and voiding any compliance
   guarantee.

3. **No incrementality.** Kasten's PVC snapshot export is block-incremental: only changed
   blocks are transferred on subsequent backups. `kando location push` always pushes a full
   stream — backup time and egress cost grow linearly with data size, regardless of how little
   changed since the last backup.

4. **Artifacts are opaque and hard to test.** You never see or inspect the artifact that
   `kando location push` wrote. There is no way to browse, verify, or manually restore from
   it outside the blueprint. Debugging a failed restore means replaying the entire blueprint
   just to get back to a state you can examine.

5. **No escape hatch when the blueprint breaks.** With the PVC-snapshot approach, a Kasten
   restore point is a standard CSI snapshot — if the blueprint's restore logic no longer
   matches your needs (schema change, operator upgrade, and so on) you can still do a granular PVC
   restore and recover individual databases or tables manually. With `kando location push` the
   data is locked inside an opaque artifact: the only way to access it is to run the blueprint.
   If the blueprint no longer works, you must first fix or rewrite it to recreate the backup
   artifact before you can recover anything.

6. **Data mover selection is ignored.** Kasten policies let you choose the data mover (Kopia,
   Veeam VBR, and so on) for PVC export. `kando location push` always uses Kopia internally — it
   does not honor the policy's data mover selection. If your organization requires VBR for
   compliance or deduplication, a blueprint that uses `kando location push` will silently
   bypass it and write through Kopia regardless.

Avoid `backup` / `restore` actions and `kando location push` / `kando location pull` in Kasten
blueprints. Use PVC snapshots (patterns 1–10) and let Kasten be the data mover.

Owned PVCs means the PVCs owned by the resource on which the blueprint applies.

The following describes the backup and restore workflow:

### Backup

- BackupAction.preHook 
- for each resource in the namespace
  - Resource.backupPrehook 
  - Late capture of the resources declared in `lateCapture*` artifacts (Kasten 9.0.10+, flag on)
  - Owned PVCs snapshot start OR Resource.backupHook starts if defined (one excludes the other; see the previous section)
  - Owned PVCs snapshot ready OR Resource.backupHook finish if defined (one excludes the other; see the previous section)
  - Resource.backupPosthook 
  - Check that every late-captured PVC is deleted or terminating
- BackupAction.onSuccess or BackupAction.onFailure

### Restore

- RestoreAction.preHook
- for each resource in the restorePoint
  - Deletion of Owned PVC 
  - Restore of Owned PVC OR recreation of empty PVC (manifest only) if Resource.restoreHook defined 
- Wait for all resource restored (including CR) to be ready 
- for each resource in the restorePoint
  - Resource.restoreHook
  - Resource.restorePostHook
- RestoreAction.onSuccess or RestoreAction.onFailure

> ⚠️ **Local snapshots do not survive the deletion of the application namespace.** Kasten keeps
> the `VolumeSnapshot` of each backed-up PVC **in the application namespace**, bound to a
> `VolumeSnapshotContent` with the `VolumeSnapshotClass` deletion policy (usually `Delete`).
> Deleting the namespace deletes the `VolumeSnapshot`, and the CSI driver deletes the storage
> snapshot. The restore point is still listed, and its restore fails with *"can not find snapshot
> for snapshotID …"*. Observed on Kasten `9.0.10` with the EBS CSI driver, for an ordinary PVC and
> for a late-captured staging PVC alike. To test a restore after the namespace is lost, **export**
> the restore point and restore from the exported copy.


### Assigning Blueprint — Manual Annotation

```bash
kubectl -n <app-namespace> annotate <resource-type>/<resource-name> \
  kanister.kasten.io/blueprint=<blueprint-name>
```

### Assigning Blueprint — BlueprintBinding (Fleet Automation, Preferred)

```yaml
apiVersion: config.kio.kasten.io/v1alpha1
kind: BlueprintBinding
metadata:
  name: <app>-binding
  namespace: kasten-io
spec:
  blueprintRef:
    name: <blueprint-name>
    namespace: kasten-io
  resources:
    matchAll:
      - type:
          group: apps
          resource: statefulsets
      - namespace:
          nameSelector:
            matchNames: ["<app-namespace>"]
```

BlueprintBindings take priority over manual annotations. This does not contradict the
use of the `DoesNotExist` operator:
```
      - annotations:
          key: kanister.kasten.io/blueprint
          operator: DoesNotExist
```
Because the BlueprintBinding stops targeting this object anymore, hence no conflict
with the annotation.


### Template Context for Annotation/Fleet Blueprints

The context object is the Kubernetes resource the blueprint is bound to:

| Parameter | Description |
|---|---|
| `.StatefulSet.Namespace` | Namespace of the StatefulSet |
| `.StatefulSet.Name` | Name of the StatefulSet |
| `.StatefulSet.Pods` | List of pod names (`index .StatefulSet.Pods 0` for first) |
| `.Deployment.Namespace` | Namespace of the Deployment |
| `.Deployment.Pods` | List of pod names |
| `.Object.metadata.name` | Name of the annotated Kubernetes object |
| `.Object.metadata.namespace` | Namespace of the annotated object — works for **any** bound resource type; prefer this over `.Deployment.Namespace` or `.StatefulSet.Namespace` when the blueprint must cover multiple resource types |
| `.Phases.<phaseName>.Secrets.<objName>.Data.<key>` | Secret value — declared through `objects` at phase level |
| `.Phases.<phaseName>.ConfigMaps.<objName>.Data.<key>` | ConfigMap value — declared through `objects` at phase level |
| `.Phases.<name>.Output.<key>` | Output from a previous phase **within the same action execution** |
| `.ArtifactsIn.<artifactName>.KeyValue.<key>` | Artifact value passed from a backup action — only available in restore actions and delete actions (`restore`, `restorePosthook`) through `inputArtifactNames` |

> **`.Namespace.Name` is NOT available in resource-bound blueprints.** It is only populated
> when Kasten invokes an **action hook** (policy preHook / onSuccess / onFailure). In
> annotation- or BlueprintBinding-bound blueprints, `.Namespace.Name` resolves to an empty
> string. Use `.Object.metadata.namespace`, `.Deployment.Namespace`, or
> `.StatefulSet.Namespace` instead.

In the Kasten context, Secrets and ConfigMaps are declared under an `objects` field **at the phase level**, not at the action level. There is **no separate `configMaps:` field** — both Secrets and ConfigMaps use `objects:` with their respective `kind:` value (`Secret` or `ConfigMap`). Kasten resolves the object at runtime using the template expression for `name` and `namespace`, for instance if the name of secret is also the name of the statefulset:

```yaml
phases:
  - func: KubeExec
    name: quiesceApp
    objects:
      pgSecret:
        kind: Secret
        name: '{{ .StatefulSet.Name }}'
        namespace: '{{ .StatefulSet.Namespace }}'
    args:
      command:
        - sh
        - -c
        - |
          password='{{ index .Phases.quiesceApp.Secrets.pgSecret.Data "password" | toString }}'
          <use password in quiesce command>
```

### Passing Data from Backup Actions to Restore Actions Through outputArtifacts

A backup action (`backupPrehook`, `backup`, `backupPosthook`) can declare `outputArtifacts` to store key-value metadata in the Kasten restore point alongside the object manifest. Restore actions (`restore`, `restorePosthook`) can then declare `inputArtifactNames` to consume that metadata through `.ArtifactsIn.<artifactName>.KeyValue.<key>`.

This is useful when a restore action needs information that was only known at backup time (for example, a PVC name, a database identifier, a configuration value).

```yaml
actions:
  backupPrehook:
    
    outputArtifacts:
      appMeta:
        keyValue:
          dbName: "{{ .Phases.captureInfo.Output.dbName }}"
    phases:
      - func: KubeExec
        name: captureInfo
        args:
          # ... phase that emits dbName through: kando output dbName <value>

  restorePosthook:
    
    inputArtifactNames:
      - appMeta
    phases:
      - func: KubeExec
        name: postRestoreInit
        args:
          command:
            - sh
            - -c
            - |
              db="{{ .ArtifactsIn.appMeta.KeyValue.dbName }}"
              <use db name in post-restore initialization>
```

> **This mechanism is only available for resource-bound blueprints (annotation / BlueprintBinding).** Action hooks cannot create output artifacts or consume input artifacts.

---

## Invocation Mode 2 — Action Hooks

A Kasten Action (BackupAction, RestoreAction, or ExportAction — ImportAction does not support hooks) can define blueprint hooks (`preHook`, `onSuccess`, `onFailure`) in its spec. The blueprint is bound to the namespace, not to a specific resource. See [Kasten action hooks documentation](https://docs.kasten.io/latest/kanister/hooks).

> 📖 **Official reference:** the distinction between resource-bound actions and namespace-level
> action hooks (and where each sits in the lifecycle) is documented in
> [Reserved Action Names → Resource-Bound Actions and Action Hooks](https://docs.kasten.io/latest/kanister/reference/action-names#resource-bound-actions-and-action-hooks).
> Action-hook `preHook` runs at the **namespace level, before PVC discovery** — see
> [Phase Lifecycle → Namespace-Level Backup Phases](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#namespace-level-backup-phases) — which is why, before Kasten 9.0.10, it was the only place a temporary PVC could be created and still captured. From 9.0.10, a resource-bound `backupPrehook` can declare it for [late capture](#late-capture--hook-created-resources-in-the-restore-point) instead.

Any action name is valid for action hooks. Recommended convention:

| Action | Trigger |
|---|---|
| `backupPrehook` | Before the backup runs, snapshots are not initiated |
| `backupPosthook` | After a successful backup, snapshots are completed |
| `backupPosthookOnFailure` | After a failed backup |

### Template Context for Action Hooks

The context is the **namespace**, not a specific resource:

| Parameter | Description |
|---|---|
| `.Namespace.Name` | The namespace being backed up |
| `.Phases.<phaseName>.Secrets.<objName>.Data.<key>` | Secret value — declared through `objects` at phase level |
| `.Phases.<phaseName>.ConfigMaps.<objName>.Data.<key>` | ConfigMap value — declared through `objects` at phase level |

---

## Why Kasten Blueprints Must Not Manage Data Movement

In the standalone Kanister framework, blueprints manage the full backup/restore cycle including data movement (`KanisterBackupData`, `KanisterRestoreData`, `KanisterBackupDataDelete`, `kopiaSnapshot` artifacts).

**Do not use these in the Kasten context.** Kasten is the datamover — it snapshots the attached PVCs and handles export to the location profile. Blueprints handle only the surrounding logic.

Do not use in Kasten blueprints:
- `KanisterBackupData` / `KanisterRestoreData` / `KanisterBackupDataDelete`
- `outputArtifacts` with `kopiaSnapshot`
- `inputArtifactNames` referencing kopia snapshots

---

## Key Kanister Functions

The following functions are commonly used in a blueprint and how to use them in the Kasten context.

| Function | When to Use |
|---|---|
| `KubeExec` | Run a command in an **existing** running container (like `kubectl exec`) |
| `KubeTask` | Spin up a **new temporary pod** with any image to run a command — **cannot mount existing PVCs**; use `KubeOps` + `WaitV2` + `KubeExec` instead |
| `KubeExecAll` | Run in parallel across **multiple pods/containers** |
| `KubeOps` | Create, patch, or delete Kubernetes resources (PVCs, ConfigMaps, and so on). On `create` it returns `apiVersion`, `group`, `resource`, `name`, `namespace` as phase outputs — exactly the fields a [late-capture](#late-capture--hook-created-resources-in-the-restore-point) declaration needs |
| `PrepareData` | Start a temporary pod in the application namespace with **existing PVCs mounted** (`volumes: {<pvc>: <mountPath>}`), run one command, delete the pod. The simplest way to write a dump to, or replay it from, a staging PVC. Supports `podOverride` (security context, env from a Secret). **Always set `failOnError: true`** — the default `false` ignores the command's exit code |
| `WaitV2` | Wait for a condition to be true on a kubernetes resource. The condition is a Go template evaluated against the **full resource object** (not just `.status`). Use `.status.<field>` notation to access status fields. Verify the correct syntax with: `kubectl get <resource> -n <ns> <name> -o go-template='<condition>'` — it should return `true` when the condition is met. Example for Elasticsearch: `{{ if and (or (eq .status.health "green") (eq .status.health "yellow")) (eq .status.phase "Ready") }}true{{ else }}false{{ end }}`. **`objectReference` format**: `apiVersion` is the version ONLY (for example, `v1`), NOT the combined `group/version`. For CRDs, provide the API group separately in the `group` field. Example for a core resource (`apps/v1 deployments`): `apiVersion: v1, group: apps`. Example for a CRD (`elasticsearch.k8s.elastic.co/v1 elasticsearches`): `apiVersion: v1, group: elasticsearch.k8s.elastic.co`. Core resources (Pods, PVCs, and so on) have no group: `apiVersion: v1` with no `group` field. |

- Use `KubeExec` when you need the application's running environment (credentials, Unix sockets, and so on)
- Use `KubeTask` when you need a specific tool image or isolation from the app container
- **KubeTask namespace choice** — two distinct rules:
  - **`kubectl` operations** (patching CRs, scaling StatefulSets, managing resources across namespaces): run the KubeTask in the **`kasten-io` namespace**. The pod inherits the Kasten service account which has cluster-wide RBAC permissions. The base `gcr.io/kasten-images/kanister-tools` image does NOT include `kubectl` — you must build a custom image that adds it (see [Example Dockerfile](#example-dockerfile)).
  - **Network calls to in-cluster services** (curl to a database HTTP API, gRPC, and so on): run the KubeTask in the **application namespace** (`{{ .Object.metadata.namespace }}`). Network policies may restrict cross-namespace traffic; running in the same namespace as the target service avoids policy violations. No special RBAC is needed for network calls.
- Use `KubeOps` to create/patch/delete Kubernetes resources (PVCs, Pods, ConfigMaps, and so on). To run a command against an existing PVC, use `PrepareData`, or `KubeOps` to create a pod with the PVC pre-mounted, `WaitV2` to wait for it to be ready, and `KubeExec` to execute inside it
- **`PrepareData` and `failOnError`.** With `failOnError: false` (the default) `PrepareData` reads
  the pod's logs and returns success **whatever the exit code** (verified in the Kanister version
  pinned by Kasten `9.0.10`). A failed `mongodump` or `pg_dump` then leaves an empty or truncated
  file, Kasten snapshots it, and the restore point is green. Always set `failOnError: true` and
  start the script with `set -euo pipefail`.
- **`PrepareData` and file ownership.** A new EBS (or similar) volume is owned by `root`. An image
  that runs as a non-root user (Bitnami images run as `1001`) cannot write to it. Set
  `podOverride.securityContext.fsGroup` to that user's group.

  ```yaml
  - func: PrepareData
    name: dumpDatabase
    args:
      namespace: "{{ .StatefulSet.Namespace }}"
      image: <database image that contains the dump tool>
      volumes:
        "{{ .Phases.createStagingPVC.Output.name }}": /backup
      failOnError: true
      podOverride:
        securityContext: {runAsUser: 1001, runAsGroup: 1001, fsGroup: 1001}
        containers:
          - name: container        # PrepareData's container name
            env:
              - name: DB_PASSWORD  # from the Secret: never rendered into the pod command
                valueFrom:
                  secretKeyRef: {name: "{{ .StatefulSet.Name }}", key: <password-key>}
      command: ["bash", "-c", "set -euo pipefail; <dump to /backup>; sync"]
  ```
---

## Dump-Tool Image

Wherever a blueprint template shows `<dump-tool-image>`, you need an image that provides:
- **`kando`** — the Kanister CLI, required for `kando output` (emitting phase outputs). It ships in the official Kasten image `gcr.io/kasten-images/kanister-tools:<kasten-version>`.
- **Application-specific dump tools** (for example, `mongodump`, `pg_dump`, `mysqldump`) added on top.

### Why Build on `kanister-tools`

`kando` is not publicly available as a standalone binary — it is only distributed inside `gcr.io/kasten-images/kanister-tools`. Your dump image must therefore use it as the base layer. The `KASTEN_VERSION` build arg should match your deployed Kasten version.

### Example `Dockerfile`

```dockerfile
ARG KASTEN_VERSION=8.0.8
FROM gcr.io/kasten-images/kanister-tools:${KASTEN_VERSION}

ARG HELM_VERSION=3.7.1
ARG KUBECTL_VERSION=1.32.0
ARG YQ_VERSION=v4.35.2

USER root

RUN microdnf install -y \
    tar \
    jq \
    nc \
    && microdnf clean all \
    && curl -L "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
         -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && curl -LO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && curl -LO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" \
    && tar -zxvf "helm-v${HELM_VERSION}-linux-amd64.tar.gz" \
    && mv linux-amd64/helm /usr/local/bin/helm \
    && rm -rf linux-amd64 "helm-v${HELM_VERSION}-linux-amd64.tar.gz"

USER 1001

ENTRYPOINT ["/bin/sh"]
```

This produces an image with: `kando`, `kubectl`, `helm`, `yq`, `jq`, `tar`, `nc`. Add your application dump tool (for example, `mongodump`) with an additional `RUN` layer.

### Usage in Blueprint Phases

**KubeTask** — when you only need to run a command and do not need to mount an existing PVC:

```yaml
- func: KubeTask
  name: runCommand
  args:
    namespace: "{{ .StatefulSet.Namespace }}"
    image: <your-registry>/kasten-tools:<kasten-version>  # built from the Dockerfile above
    command:
      - bash
      - -c
      - |
        <your command here>
```

**KubeOps + WaitV2 + KubeExec** — when you need to mount an existing PVC (KubeTask cannot do this):

```yaml
- func: KubeOps
  name: createDumpPod
  args:
    spec:
      spec:
        containers:
          - name: dump
            image: <your-registry>/kasten-tools:<kasten-version>  # built from the Dockerfile above
```

---

## Blueprint Patterns

We can list the different blueprint patterns observed in the field. We use the word `dump` for a logical backup (for example, `mysqldump` or `mongodump`) and `database snapshot` for an application-aware snapshot (for example, Cassandra `nodetool snapshot` or the Elasticsearch snapshot API).

The ordering follows two questions:

1. **Does the blueprint keep the Single Responsibility Principle (SRP) and the Open/Closed
   Principle (OCP)?** Patterns 1–7 are resource-bound and work with BlueprintBinding: each
   workload type has its own blueprint, and adding a workload type never modifies an existing
   blueprint. Patterns 8–10 need a BackupAction preHook (action hook), which is namespace-scoped:
   one blueprint handles every workload in the namespace, which violates SRP and OCP.
2. **Does the backup tool need its previous backup on the volume?** An incremental chain or
   append-only snapshot files need a volume that **persists between backups** (patterns 3–5). A
   full dump or a full database snapshot does not: it can go on a **staging PVC** that exists only
   during the backup (pattern 6, late capture), which needs no keeper and holds no storage between
   backups.

**Incrementality is part of the choice.** Patterns 1–2 snapshot the database volumes: every
local snapshot and every export is incremental at block level. Patterns 3–5 with an append-only
tool are incremental too. Every dump pattern (6–10, and 3–5 with a full dump) rewrites the
whole dump at every backup. Only the export to a location profile can recover incrementality,
because Kopia deduplicates by content. With a staging PVC (pattern 6) the local snapshot is also a
full copy each time, because the volume is new at every backup. Measure the export growth (Step 6
in [AGENTS.md](AGENTS.md#step-6--measure-export-deduplication-efficiency)) before you claim that a
dump pattern is cheap.

A resource (PVC, ConfigMap, custom resource such as a `cnpg.io` `Backup`) created during a
resource-bound `backupPrehook` is not discovered, because discovery runs first. It reaches the
restore point only if the hook declares it for [late capture](#late-capture--hook-created-resources-in-the-restore-point)
(Kasten 9.0.10+), or if it is created in a BackupAction preHook instead.



### 1. Fence and Quiesce Replica

- **Principle**: Fence (halt) replication synchronization and quiesce a replica to take a backup of the application volumes.
- **Example**: [cnpg-example](./cnpg-example/)
- **Pro**: No impact on the primary, because only a replica is quiesced.
- **Cons**: At restore time, the operator must be able to promote the replica to primary, or you have to manually reconfigure your workload.
- **BlueprintBinding**: Yes — attach to the workload CR.
- **Filter PVC**: Yes — you can take only the replica volumes.

### 2. Quiesce

- **Principle**: Ensure that data is flushed to disk using a database primitive and that all transactions are persisted on the volumes before backing them up.
- **Example**: [mariadb-community-operator-standalone-example](./mariadb-community-operator-standalone-example/), [MongoDB](https://docs.kasten.io/latest/kanister/mongodb/install_app_cons) or [PostgreSQL](https://docs.kasten.io/latest/kanister/postgresql/install_app_cons)
- **Pro**: Backup is incremental and backup time remains constant as the database grows (unlike a dump strategy).
- **Cons**: When restoring database volumes you cannot be granular (for example, restore only one database or specific tables/collections).
- **BlueprintBinding**: Yes — attach to the workload CR.
- **Filter PVC**: No — you must take all application volumes.

### 3. Database Snapshot on Permanent Workload PVC (PVC Mounted by Workload)

- **Principle**: The workload is configured to mount a permanent backup PVC. The `backupPrehook` runs the database snapshot tool through `KubeExec` into the workload pod (or a sidecar). The PVC is pre-existing when Kasten runs its discovery pass.
- **Example**: [elasticsearch-eck-example](./elasticsearch-eck-example/)
- **Pro**: Database snapshot allows efficient incrementality by appending files at each snapshot. BlueprintBinding works because the PVC is discovered through the workload's ownership chain.
- **Cons**: Requires changing the workload configuration to mount the backup PVC.
- **BlueprintBinding**: Yes — attach to the workload CR.
- **Filter PVC**: Yes — you can take only the permanent PVC used for the snapshot.

### 4. Database Dump or Snapshot on Permanent Keeper PVC (PVC Mounted by Keeper)

- **Principle**: A dedicated keeper Deployment mounts the backup PVC permanently and runs the backup tool image (for example, the database server image that includes the backup tool). The `backupPrehook` uses `KubeExec` into the keeper pod to write the dump or snapshot to the PVC. Kasten discovers the PVC through the keeper Deployment's ownership chain.
- **Example**: [couchbase-operator-example](./couchbase-operator-example/)
- **Pro**: No change to the main workload configuration. PVC is always discovered because the keeper keeps it mounted. `KubeExec` avoids temporary pod lifecycle management. BlueprintBinding attaches to the keeper Deployment.
- **Cons**: Requires deploying an extra Deployment alongside the workload. The keeper image must include the backup tool (for example, `couchbase/server:<version>` for `cbbackupmgr`).
- **BlueprintBinding**: Yes — attach to the keeper Deployment using a generic component label (for example, `couchbase-keeper: "true"`) so one binding covers multiple keepers in the same namespace.
- **Env vars**: Set `USERNAME`, `PASSWORD`, and `CLUSTER` as env vars on the keeper Deployment (using `secretKeyRef` for credentials). `KubeExec` phases inherit these from the pod environment — no `objects:` block needed in the blueprint.
- **Filter PVC**: Yes — you can take only the permanent PVC used for the backup.

**Multi-keeper namespaces — naming convention and env-var pattern**

When a namespace contains more than one workload (for example, two independent Couchbase clusters),
deploy one keeper per workload. To keep a single blueprint reusable across all of them, follow
these conventions:

- **Naming**: name each keeper Deployment and its backup PVC after the workload it serves,
  suffixed with `-keeper`. For example, the keeper for `cb-example` is named `cb-example-keeper`
  and its PVC is also `cb-example-keeper`. This makes the relationship immediately visible.
- **Generic component label**: add a stable component label (for example, `couchbase-keeper: "true"`)
  to every keeper Deployment. The BlueprintBinding targets this label — not the specific
  deployment name — so one binding covers all keepers in the fleet.
- **Env vars instead of `objects:`**: set `USERNAME`, `PASSWORD`, and `CLUSTER` directly in the
  keeper Deployment's `env:` block (using `secretKeyRef` for credentials). Because `KubeExec`
  runs inside the keeper pod, these variables are already present in the shell environment when
  the backup script executes. The blueprint phases need no `objects:` block — no ConfigMap or
  Secret is fetched at runtime — making the blueprint fully generic and free of workload-specific
  object references.
- **Cluster name derivation in KubeTask phases**: when a `KubeTask` phase (which runs in a
  separate pod outside the keeper) needs the workload name, derive it from the keeper Deployment
  name by stripping the `-keeper` suffix:
  ```bash
  KEEPER_NAME="{{ .Deployment.Name }}"
  CLUSTER_NAME="${KEEPER_NAME%-keeper}"   # for example, cb-example-keeper → cb-example
  ```

### 5. Database Dump or Snapshot Through Local MinIO Keeper (PVC Mounted by Keeper, Keeper Exposes S3-Protocol)

- **Principle**: The workload has a native S3-compatible backup/restore mechanism. A MinIO
  Deployment is deployed in the **application namespace** as the local S3 endpoint, backed by a
  permanent PVC. Kasten discovers the PVC through the MinIO Deployment's ownership chain. The
  `backupPrehook` triggers the workload's native backup to point at the local MinIO service
  (`http://<minio-svc>:9000`). After Kasten snapshots the MinIO PVC, `restorePosthook` triggers
  the workload to restore from it.
- **Example**: [cnpg-barman-example](./cnpg-barman-example/)
- **Pro**: No custom dump image needed — MinIO is the off-the-shelf keeper image. The workload's
  existing S3-protocol backup mechanism is reused as-is. BlueprintBinding works because MinIO's
  PVC is permanent. Incremental if the workload's S3 tool supports it (for example, wal-g, pgBackRest
  with block-level incremental).
- **Cons**: Requires deploying MinIO alongside the workload. If the backup tool is not already
  available inside the workload pod, a sidecar or a separate pod (with narrow RBAC) is needed to
  invoke it.
- **BlueprintBinding**: Yes — target the MinIO Deployment through the generic label
  `minio-s3-keeper: "true"`.
- **Workload identity**: carry it in labels on the MinIO Deployment (`kasten.io/workload-name`,
  `kasten.io/workload-kind`) rather than deriving it from the Deployment name. Read them in
  blueprint phases through `{{ index .Object.metadata.labels "kasten.io/workload-name" }}`.
- **Backup trigger strategy** — apply in order, stop at the first feasible option:
  1. StatefulSet → `KubeExec` with deterministic pod name `<name>-0`, no discovery.
  2. REST API / backup CR → `KubeTask` (app namespace) or `KubeOps`, no pod name needed.
  3. Annotation-based trigger → `KubeOps` to annotate the workload.
  4. Pod discovery → grant `default` SA in app namespace a namespace-scoped `Role` to `get`/`list` pods;
     run `KubeTask` in the **app namespace** (uses narrow SA, not the broad Kasten SA in `kasten-io`).
- **Filter PVC**: Yes — include only the MinIO PVC, not the workload's own data PVCs.

#### MinIO Keeper Deployment Manifest

Replace `<app-namespace>`, `<workload>`, and `<storage-class>` with your values. Note that the
`emptyDir` from development examples must be replaced with a PVC for production use.

```yaml
# Credentials Secret
apiVersion: v1
kind: Secret
metadata:
  name: <workload>-minio-creds
  namespace: <app-namespace>
type: Opaque
stringData:
  accessKey: minio
  secretKey: minio123        # use a strong secret in production
---
# Backup storage PVC
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <workload>-minio
  namespace: <app-namespace>
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <storage-class>   # must support CSI snapshots
  resources:
    requests:
      storage: 20Gi
---
# MinIO keeper Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <workload>-minio
  namespace: <app-namespace>
  labels:
    app: <workload>-minio
    minio-s3-keeper: "true"             # targeted by BlueprintBinding
    kasten.io/workload-name: <workload> # read by the blueprint
    kasten.io/workload-kind: StatefulSet  # or Deployment
spec:
  replicas: 1
  selector:
    matchLabels:
      app: <workload>-minio
  template:
    metadata:
      labels:
        app: <workload>-minio
    spec:
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: <workload>-minio
      containers:
        - name: minio
          image: quay.io/minio/minio:latest
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            runAsNonRoot: true
            seccompProfile:
              type: RuntimeDefault
          env:
            - name: MINIO_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: <workload>-minio-creds
                  key: accessKey
            - name: MINIO_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: <workload>-minio-creds
                  key: secretKey
          args: ["server", "/data", "--console-address", ":9090"]
          ports:
            - containerPort: 9000
              name: s3
            - containerPort: 9090
              name: console
          livenessProbe:
            httpGet:
              path: /minio/health/live
              port: 9000
            initialDelaySeconds: 30
            periodSeconds: 20
          readinessProbe:
            httpGet:
              path: /minio/health/ready
              port: 9000
            initialDelaySeconds: 30
            periodSeconds: 20
          volumeMounts:
            - mountPath: /data
              name: data
---
# ClusterIP Service — S3 endpoint reachable within the namespace
apiVersion: v1
kind: Service
metadata:
  name: <workload>-minio
  namespace: <app-namespace>
spec:
  selector:
    app: <workload>-minio
  ports:
    - port: 9000
      name: s3
    - port: 9090
      name: console
  type: ClusterIP
```

#### BlueprintBinding

```yaml
apiVersion: config.kio.kasten.io/v1alpha1
kind: BlueprintBinding
metadata:
  name: minio-s3-keeper-binding
  namespace: kasten-io
spec:
  blueprintRef:
    name: <blueprint-name>
    namespace: kasten-io
  resources:
    matchAll:
      - type:
          operator: In
          values:
            - group: apps
              resource: deployments
      - labels:
          key: minio-s3-keeper
          operator: In
          values: ["true"]
      - annotations:
          key: kanister.kasten.io/blueprint
          operator: DoesNotExist
```

#### Blueprint Phase Skeleton

The S3 endpoint for in-namespace access is `http://<workload>-minio:9000`. From a KubeTask
running in `kasten-io` or another namespace, use the full cluster DNS:
`http://<workload>-minio.<app-namespace>.svc.cluster.local:9000`.

```yaml
actions:
  backupPrehook:
    phases:
      # Option 1 — StatefulSet: pod name is deterministic, no discovery needed
      - func: KubeExec
        name: triggerBackup
        objects:
          minioCreds:
            kind: Secret
            name: '{{ index .Object.metadata.labels "kasten.io/workload-name" }}-minio-creds'
            namespace: '{{ .Object.metadata.namespace }}'
        args:
          namespace: '{{ .Object.metadata.namespace }}'
          pod: '{{ index .Object.metadata.labels "kasten.io/workload-name" }}-0'
          container: <workload-container>
          command:
            - bash
            - -c
            - |
              MINIO_SVC="{{ index .Object.metadata.labels "kasten.io/workload-name" }}-minio"
              ENDPOINT="http://${MINIO_SVC}:9000"
              ACCESS_KEY='{{ index .Phases.triggerBackup.Secrets.minioCreds.Data "accessKey" | toString }}'
              SECRET_KEY='{{ index .Phases.triggerBackup.Secrets.minioCreds.Data "secretKey" | toString }}'
              # run the workload-specific S3 backup command, for example:
              # wal-g backup-push --s3-endpoint "${ENDPOINT}" ...
              # pgbackrest --stanza=main --repo1-s3-endpoint "${ENDPOINT}" backup

      # Option 4 — pod discovery with narrow RBAC (last resort)
      # Requires Role + RoleBinding for default SA — see Pattern 5 in the discovery section above.
      - func: KubeTask
        name: triggerBackupByDiscovery
        args:
          namespace: '{{ .Object.metadata.namespace }}'   # app namespace → uses default SA
          image: <image-with-kubectl-and-backup-tool>
          command:
            - bash
            - -c
            - |
              APP_NS="{{ .Object.metadata.namespace }}"
              WORKLOAD="{{ index .Object.metadata.labels "kasten.io/workload-name" }}"
              MINIO_SVC="${WORKLOAD}-minio"
              ENDPOINT="http://${MINIO_SVC}:9000"
              ACCESS_KEY=$(kubectl get secret "${WORKLOAD}-minio-creds" -n "${APP_NS}" \
                           -o jsonpath='{.data.accessKey}' | base64 -d)
              SECRET_KEY=$(kubectl get secret "${WORKLOAD}-minio-creds" -n "${APP_NS}" \
                           -o jsonpath='{.data.secretKey}' | base64 -d)
              POD=$(kubectl get pod -n "${APP_NS}" -l "app=${WORKLOAD}" \
                    --field-selector=status.phase=Running \
                    -o jsonpath='{.items[0].metadata.name}')
              kubectl exec -n "${APP_NS}" "${POD}" -- \
                <workload-specific-backup-command> \
                  --s3-endpoint "${ENDPOINT}" \
                  --access-key "${ACCESS_KEY}" \
                  --secret-key "${SECRET_KEY}"

  restorePosthook:
    phases:
      # After Kasten restores the MinIO PVC, trigger the workload to reload from it
      - func: KubeExec
        name: triggerRestore
        objects:
          minioCreds:
            kind: Secret
            name: '{{ index .Object.metadata.labels "kasten.io/workload-name" }}-minio-creds'
            namespace: '{{ .Object.metadata.namespace }}'
        args:
          namespace: '{{ .Object.metadata.namespace }}'
          pod: '{{ index .Object.metadata.labels "kasten.io/workload-name" }}-0'
          container: <workload-container>
          command:
            - bash
            - -c
            - |
              MINIO_SVC="{{ index .Object.metadata.labels "kasten.io/workload-name" }}-minio"
              ENDPOINT="http://${MINIO_SVC}:9000"
              ACCESS_KEY='{{ index .Phases.triggerRestore.Secrets.minioCreds.Data "accessKey" | toString }}'
              SECRET_KEY='{{ index .Phases.triggerRestore.Secrets.minioCreds.Data "secretKey" | toString }}'
              # run the workload-specific S3 restore command
```

### 6. Logical Dump or Full Database Snapshot on a Staging PVC (Late Capture)

> ⚠️ Kasten `9.0.10` or later with `features.lateCapture=true`. Without the flag the backup
> succeeds and holds **no dump** — see [Late Capture](#late-capture--hook-created-resources-in-the-restore-point).

- **Principle**: `backupPrehook` creates a staging PVC with `KubeOps`, writes a full dump (or a
  full database snapshot) to it with `PrepareData`, runs `sync`, and declares the PVC in a
  `lateCapture` output artifact. Kasten snapshots it with the other PVCs. `backupPosthook` deletes
  it. On restore, Kasten restores the PVC, and `restorePosthook` replays the dump and deletes the PVC.
- **Examples**: [mongodb-logical-late-capture-example](./mongodb-logical-late-capture-example/),
  [mysql-logical-late-capture-example](./mysql-logical-late-capture-example/),
  [postgresql-logical-late-capture-example](./postgresql-logical-late-capture-example/),
  [mssql-logical-late-capture-example](./mssql-logical-late-capture-example/) (bound to a
  Deployment; the `.bak` files pass through the SQL Server container's filesystem first)
- **Pro**: No keeper Deployment, no change to the workload, no storage used between backups.
  Resource-bound, so BlueprintBinding works and several workload types can share a namespace.
  A dump allows a granular restore (one database, one collection). Kasten remains the data mover
  and deletes the snapshot when the restore point expires — no `delete` action.
- **Cons**: No incrementality in the local snapshot (the volume is new each time); only the
  export deduplicates. Cannot hold an incremental chain. A PVC left by a crashed or cancelled
  backup fails the next backup until someone deletes it. Needs a blueprint bound to a resource
  that owns pods, so not an external database bound to a ConfigMap. Needs Kasten 9.0.10+.
- **BlueprintBinding**: Yes — attach to the workload.
- **Filter PVC**: Yes — exclude the database PVCs and keep only the staging PVC. The filter must
  not match the staging PVC: late capture fails the backup if the policy excludes a declared PVC.

### 7. Create Logical Dump or Database Snapshot on PVC Already Used by Database

- **Principle**: When no extra PVC is acceptable and the dump can safely coexist on the database volumes without exhausting storage.
- **Example**: no example
- **Pro**: No change of the workload configuration, no creation of extra storage.
- **Cons**: This is dangerous for the database storage and putting the data on an extra PVC should always be preferred when possible. With Kasten 9.0.10+, pattern 6 almost always replaces it.
- **BlueprintBinding**: Yes — attach to the workload CR.
- **Filter PVC**: No — you must take all the database PVCs.

---

### Action-Hook Patterns — Temporary PVC Created Before Discovery

> ⚠️ **Patterns 8, 9, and 10 create a PVC during the action `preHook`, which runs before the
> discovery of the namespace.** Before Kasten 9.0.10 this was the only way to capture a PVC
> created for the backup. **With Kasten 9.0.10+ and late capture, use pattern 6 instead**, except
> for an external database (pattern 10), where no workload in the cluster owns pods for the
> blueprint to bind to.
>
> A **BackupAction preHook** (action hook) comes with significant architectural constraints:
>
> - **Context is namespace-only**: `{{ .Namespace.Name }}` is the only available template
>   variable. No `.Object`, `.StatefulSet`, or any resource-level field is available. The blueprint
>   must discover workloads in the namespace by itself (for example, through `kubectl get`).
> - **No BlueprintBinding**: The hook is configured per-policy in the BackupAction spec, not through a
>   BlueprintBinding.
> - **SRP violation**: A single action hook blueprint handles all workloads in the namespace. Adding
>   a new workload type that also needs a temporary PVC requires modifying the same blueprint —
>   violating the Open/Closed Principle. To limit this impact, **deploy only one type of workload
>   per namespace** when using these patterns.
>
> Only choose patterns 8–10 when patterns 1–7 are technically infeasible.
>
> 📖 **Official reference:** the reason the action-hook `preHook` is the only place a temporary PVC
> survives is the discovery-before-processing ordering documented in
> [Phase Lifecycle → Per-Resource Backup Processing](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#per-resource-backup-processing)
> versus [Namespace-Level Backup Phases](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#namespace-level-backup-phases).

### 8. Database Snapshot on Temporary PVC (Action Hook)

- **Principle**: Create a temporary PVC inside a BackupAction preHook (before PVC discovery) and write the database snapshot to it. Kasten discovers and snapshots the PVC, then the `backupPosthook` (or `onSuccess` hook) deletes the temporary PVC.
- **Example**: no example
- **Pro**: Captures a PVC created for the backup on Kasten versions without late capture.
- **Cons**: The PVC is new and empty at every backup, so the snapshot tool cannot append to the previous one: this is a full snapshot each time, not an incremental one. Requires a BackupAction preHook; BlueprintBinding cannot be used. See warning above. With Kasten 9.0.10+, use pattern 6.
- **BlueprintBinding**: No — must use BackupAction preHook.
- **Filter PVC**: Yes — you can take only the temporary PVC.

### 9. Logical Dump on Temporary PVC (Action Hook)

- **Principle**: Create a temporary PVC inside a BackupAction preHook and write the logical dump to it. Kasten discovers and snapshots the PVC, then the `onSuccess` hook deletes it.
- **Example**: [Mongoce](https://github.com/kastendevhub/enterprise-blueprint/tree/main/maximo/mongoce)
- **Pro**: A dump enables granular restore.
- **Cons**: No incrementality — as the dump grows over time, backup time grows too. Requires a BackupAction preHook; BlueprintBinding cannot be used. See warning above. With Kasten 9.0.10+, use pattern 6.
- **BlueprintBinding**: No — must use BackupAction preHook.
- **Filter PVC**: Yes — you can take only the temporary PVC.

### 10. Create Dump of External Database on Temporary PVC (Action Hook)

- **Principle**: For databases that live outside the cluster but whose logical dump can be created inside the cluster. Create a temporary PVC inside a BackupAction preHook, write the dump to it, let Kasten snapshot it, then delete it.
- **Example**: https://github.com/prcerda/K10-SampleApps/blob/main/PostgreSQL-External/postgresql-ext-blueprint-alldbs.yaml
- **Pro**: Includes an external database in the Kasten backup.
- **Cons**: No incrementality. Requires a BackupAction preHook; BlueprintBinding cannot be used. See warning above. Late capture (pattern 6) does not replace it: a declared PVC needs a blueprint bound to a resource that owns pods, and an external database has none in the cluster.
- **BlueprintBinding**: No — must use BackupAction preHook.
- **Filter PVC**: Yes — you can take only the temporary PVC.


### 11. Trigger Dump of Managed Database

- **Principle**: Bind the blueprint to a Secret that provides connection information for a managed database, invoke an external API to create a backup, and store the backup identifier in the restore point alongside the Secret manifest.
- **Example**: [RDS](https://github.com/kanisterio/blueprints/tree/main/aws-rds-postgres), [Firestore](https://github.com/michaelcourcy/kasten-firestore) or [MongoDB Atlas](https://github.com/kanisterio/blueprints/tree/main/mongodb-atlas)
- **Pro**: All backup complexity is handed off to the cloud provider.
- **Cons**: You lose control of the data mover and Kasten is no longer responsible for backup immutability. 
- **Filter PVC**: No PVC is involved.

### 12. Use Vendor Operator Data Mover to Handle Backup

- **Principle**: Invoke the vendor operator API to trigger a backup.
- **Example**: [CNPG](https://github.com/michaelcourcy/kasten-cnpg), [K8ssandra Medusa](https://docs.kasten.io/latest/kanister/k8ssandra/policy/) or [Crunchy Postgres](https://docs.kasten.io/latest/kanister/pgo/logical)
- **Pro**: All backup complexity is handed off to the operator.
- **Cons**: You lose control of the data mover and Kasten is no longer responsible for backup immutability, unless the vendor stores the backup in a PVC (for example, the Ansible Automation Platform operator). Often you need to add specific configuration on each resource and if you copy and paste without paying attention you can get backup collision.
- **Filter PVC**: No PVC is involved unless the vendor creates a dedicated backup PVC.

## Use Delete Action When Using Pattern 5, 11, and 12

In patterns 5, 11 and 12, Kasten is no longer the data mover (or indirectly in pattern 5). When Kasten deletes a restore point, it
does **not** automatically clean up the external backup that was created by the blueprint (for example, an
RDS snapshot, an Atlas backup, a CNPG `Backup` CR). To clean up the external backup, implement a
`delete` action in the blueprint. Kasten calls it automatically when the restore point is deleted.

> 📖 **Official reference:** [Reserved Action Names → Delete Action Name](https://docs.kasten.io/latest/kanister/reference/action-names#delete-action-name)
> and [Phase Lifecycle → Retire (Delete) Lifecycle](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle#retire-delete-lifecycle).

### How Delete Action Receives Backup Identifier

The `backup` (or `backupPrehook` / `backupPosthook`) action must emit the external backup identifier
into the restore point using `outputArtifacts`. The `delete` action then retrieves it through
`inputArtifactNames` and `.ArtifactsIn.<artifactName>.KeyValue.<key>`.

```yaml
actions:
  backup:
    outputArtifacts:
      externalBackup:
        keyValue:
          backupId: "{{ .Phases.triggerBackup.Output.backupId }}"
    phases:
      - func: KubeTask
        name: triggerBackup
        args:
          # ... trigger external backup and emit its ID:
          # kando output backupId <id>

  delete:
    inputArtifactNames:
      - externalBackup
    phases:
      - func: KubeTask
        name: deleteExternalBackup
        args:
          namespace: "{{ .Namespace.Name }}"
          image: <tool-image>
          command:
            - bash
            - -c
            - |
              BACKUP_ID="{{ .ArtifactsIn.externalBackup.KeyValue.backupId }}"
              # call external API to delete the backup identified by BACKUP_ID
```

### Execution Context for Delete Action

The `delete` action does **not** run in the context of the original application object — that object
may have been deleted long before the restore point is retired. Instead, it runs in the Kasten
namespace context. Use `{{ .Namespace.Name }}` to refer to the Kasten namespace (works whether
Kasten is installed in `kasten-io` or any other namespace), and run KubeTask phases in that same
namespace.
 
## References

- [Kasten K10 Blueprints Documentation](https://docs.kasten.io/latest/usage/blueprints.html)
- [Kasten Reserved Action Names and Timeouts](https://docs.kasten.io/latest/kanister/reference/action-names) — the six reserved names, default timeouts, resource-bound vs. action-hook invocation, and the delete action.
- [Kasten Blueprint Phase Lifecycle](https://docs.kasten.io/latest/kanister/reference/phase-lifecycle) — exact ordering of backup/restore/export/retire phases, including PVC discovery timing relative to per-resource hooks.
- [Kasten Action Hooks](https://docs.kasten.io/latest/kanister/hooks)
- [Blueprint Bindings API](https://docs.kasten.io/latest/usage/blueprint_bindings.html)
- [Kanister Functions Reference](https://docs.kanister.io/functions.html)
- [Kanister Template Parameters](https://docs.kanister.io/templates.html)
- [Kanister GitHub (community blueprints)](https://github.com/kanisterio/kanister/tree/master/examples)
