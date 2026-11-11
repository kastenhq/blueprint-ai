# CP4BA (FileNet Content Manager and ODM) — Kasten blueprint

> ## ⚠️ Read this before you use this blueprint
>
> - **Two capabilities only.** It was designed for a CP4BA deployment with the **Content**
>   (FileNet) and **Decisions** (ODM) capabilities. It was not designed or tested for any other
>   CP4BA capability (Workflow, Automation Decision Services, Document Processing, Business
>   Automation Insights, and so on).
> - **Content on PVCs only.** It does **not** protect a storage area or a database that lives
>   **outside** the namespace: a FileNet storage area on an external S3 bucket, an external
>   database (Db2, Oracle, SQL Server, an external PostgreSQL), or any other external data service.
>   Those must be protected by their own means, at the same moment as the Kasten backup
>   ([§4.4](#44-when-content-is-in-s3)).
> - **Another component means updating the offline/online scripts.** The backup is consistent
>   only because *everything that writes* is stopped first. If your deployment adds a component —
>   a new capability, a workload type the scripts do not stop, a new CronJob that mounts a PVC —
>   check and update [scripts/scale-down.sh](scripts/scale-down.sh) and
>   [scripts/scale-up.sh](scripts/scale-up.sh), then validate again.
> - **The disaster recovery script is written for this deployment.** [scripts/dr-restore.sh](scripts/dr-restore.sh)
>   knows the operators, the webhooks and the OLM objects of a Starter namespace where every
>   operator is installed in the CP4BA namespace. Another capability, another operator, a
>   different install mode, or Foundational Services in their own namespace will need changes to
>   the script, and a new validation.
> - **Validated on the Starter deployment only**, on a test cluster
>   ([install-cp4ba.md](install-cp4ba.md)). It was **not** validated on a production CP4BA
>   installation, which has external databases, more replicas, and more components.
> - **Restore and disaster recovery are two different procedures**
>   ([§8](#8-restore)). The restore brings back the PVCs into a running CP4BA. The disaster
>   recovery ([§9](#9-disaster-recovery)) brings back the whole namespace after it was deleted, and
>   was validated **on the same cluster** only. Both use the **exported** restore point.
>
> Like every directory in this repository, this one is an **example**, not a general-purpose
> solution and not a configuration that covers every CP4BA deployment. Copy it, adapt it, and validate it again on your own deployment. See the ownership
> disclaimer in the [repository README](../../README.md).

---

## 1. Files in this directory

| File | What it is |
|---|---|
| [README.md](README.md) | This file: scope, IBM's backup documentation, design, deployment, backup and restore |
| [blueprint.yaml](blueprint.yaml) | The Kasten blueprint: action hooks that take CP4BA offline and back online |
| [policy.yaml](policy.yaml) | The backup policy, with the three hooks |
| [scripts/scale-down.sh](scripts/scale-down.sh) | Takes the namespace offline. Used by the blueprint **and** by hand before a restore |
| [scripts/scale-up.sh](scripts/scale-up.sh) | Brings the namespace back online. Used by the blueprint **and** by hand after a restore |
| [scripts/dr-restore.sh](scripts/dr-restore.sh) | Disaster recovery of a deleted namespace from the exported restore point ([§9](#9-disaster-recovery)). Run by hand only |
| [install-cp4ba.md](install-cp4ba.md) | How CP4BA is installed on the test cluster (Starter, Decisions + Content) |
| [mini-tutorial-filenet.md](mini-tutorial-filenet.md) | FileNet crash course, test dataset, and restore check |
| [mini-tutorial-odm.md](mini-tutorial-odm.md) | ODM crash course, test dataset, and restore check |

---

## 2. What is protected

CP4BA bundles several older IBM products. This example covers the two with the largest installed
base before containers:

| CP4BA capability | Earlier product | Where its state lives |
|---|---|---|
| **Content** | FileNet P8 | Databases (GCD, object stores, Navigator) **and** files on PVCs (storage areas, configuration) |
| **Decisions** | ODM (ILOG JRules) | Databases only (expected) |

FileNet is the harder of the two and drives the design. ODM is included because it shares the
same deployment, the same PostgreSQL, the same LDAP and the same operator, so one backup covers
both.

### 2.1 Vocabulary: a FileNet *object store* is not S3 object storage

The two terms sound alike and mean different things:

| Term | Meaning |
|---|---|
| FileNet **object store** | A **document repository** inside FileNet: one database (metadata, folders, versions, security) plus one or more storage areas (the document content). Backing it up is the whole point of this blueprint |
| **Object storage** (S3) | A storage service that keeps files as objects in buckets (AWS S3, MinIO, IBM Cloud Object Storage) |

They meet in one place: a FileNet **advanced storage area** can write its content to an S3 bucket
(an *S3 storage device*). See [§4.4](#44-when-content-is-in-s3).

---

## 3. IBM's official backup documentation

Read these before this file. They are the supported procedure; this file only explains how it maps
to Kasten.

| Document | What it says |
|---|---|
| [CP4BA 25.0.0 — Backing up your environment](https://www.ibm.com/docs/en/SSYHZ8_25.0.0/com.ibm.dba.managing/topics/tsk_backup.html) | The main procedure. Offline or online backup, the secrets, ConfigMaps, PVCs and databases to capture, and component-specific steps. *(The 26.0.0 page was not found by search; check the same path under `SSYHZ8_26.0.0`.)* |
| [CP4BA 24.0.1 — Backing up your environment](https://www.ibm.com/docs/SSYHZ8_24.0.1/com.ibm.dba.managing/topics/tsk_backup.html) | Earlier version, with the `oc scale` commands |
| [CP4BA 24.0 — Disaster recovery](https://ibm.com/docs/SSYHZ8_24.0.0/com.ibm.dba.managing/topics/tsk_overview_dr.html) | Replication and failover. Notes that Foundational Services need their own backup procedure |
| [FileNet P8 5.5 — Backing up the data in your FileNet P8 domain](https://www.ibm.com/support/knowledgecenter/en/SSNW2F_5.5.0/com.ibm.p8.common.admin.doc/backup_restore/backup_enterprise.htm) | What a FileNet backup contains |
| [FileNet P8 5.6 — Online backup options](https://ibm.com/docs/SSNW2F_5.6.0/com.ibm.p8.common.admin.doc/backup_restore/backup_online.html) | The conditions for a backup without stopping FileNet |
| [FileNet P8 5.5 — Recovering the data in your FileNet P8 domain](https://www.ibm.com/docs/SSNW2F_5.5.0/com.ibm.p8.common.admin.doc/backup_restore/restore_enterprise.htm) | What a restore must bring back |
| [FileNet P8 5.5.11 — Recovering an object store only](https://www.ibm.com/docs/SSNW2F_5.5.11/com.ibm.p8.common.admin.doc/backup_restore/restore_partial_files_object.html) | Partial restore, followed by the Content Consistency Checker |
| [FileNet P8 5.5.11 — Advanced storage areas](https://ibm.com/docs/SSNW2F_5.5.11/com.ibm.p8.ce.admin.tasks.doc/p8pcc220.html) | Storage areas on file systems or S3, with replication between devices |

### 3.1 What these documents say, in short

**CP4BA offers two backup modes:**

| Mode | IBM's instruction |
|---|---|
| **Offline** | *"Before you start an offline backup, stop the environment to prevent changes to persistent volumes (PVs) and databases."* Scale down the operators (`ibm-cp4a-operator`, `ibm-content-operator`, `ibm-pfs-operator`) and every deployment and StatefulSet whose name matches the CR name (`icp4adeploy`). Back up. Scale up again |
| **Online** | *"Back up all databases at the same time, or as close to it as possible."* No more detail than that |

**For FileNet, CP4BA adds:** back up the GCD and object store databases, the `ibm-fncm-secret`, and
the Navigator `icn-*` PVCs, then follow the FileNet backup documentation.

**The FileNet documentation sets the consistency rule.** An online backup must capture all FileNet
data — databases, storage areas, index areas — *"at the same instant in time"*, *"within a maximum
of a matter of seconds"*. That requires storage **consistency groups** (snapshots of several volumes
taken as one) and the ability to **suspend database writes** briefly. If the stores are captured at
different moments, IBM names both results as inconsistencies:

- a database row that points to a content file that does not exist — the document is listed but
  cannot be opened;
- a content file that no database row refers to.

On restore: *"Only by restoring all pieces of the backup can you ensure complete consistency in the
recovered system."* When only one object store is restored, IBM requires the **Content Consistency
Checker**, which compares the object store database with the files in its storage areas.

**IBM marks what a backup must include** with the label `cp4ba.ibm.com/backup-type=mandatory` on the
CR, secrets and ConfigMaps. The 25.0.0 procedure asks you to add the label where it is missing.

---

## 4. Design reasoning

### 4.1 Online backup is not reachable with Kasten alone

Kasten snapshots each PVC with its own CSI `VolumeSnapshot`. It does not group them into a
consistency group, so it cannot guarantee that the database PVCs and the storage area PVC are
captured at the same instant while CPE keeps writing. A backup taken that way can produce exactly
the broken document described above, and the error only appears when somebody opens that document,
possibly months later.

### 4.2 Offline backup is IBM's procedure, and it fits Kasten

The offline procedure removes the problem instead of working around it. Once CPE, Navigator, the
ODM servers and their databases are stopped, nothing writes to the databases or to the storage
areas. The order in which Kasten snapshots the PVCs no longer matters, because nothing changes
between the first snapshot and the last.

This is the **Quiesce** pattern of this repository (pattern 2 in
[AGENTS.md](../../AGENTS.md#step-1--choose-pattern-and-document-it)), applied to the whole
namespace at once.

**What is stopped.** IBM's rule stops the CP4BA operators and the workloads whose name contains
`icp4adeploy`. This blueprint stops **everything in the namespace** instead: all operators, all
Deployments and StatefulSets (Foundational Services and Zen included), the PostgreSQL clusters,
and the CronJobs. It is simpler — no name list to keep up to date — and it is a superset of IBM's
rule, so it cannot miss a writer that IBM's rule would catch.

**What it costs.** During an offline backup CP4BA is **unavailable**, not even read-only. That is
true of IBM's own offline procedure too: once CPE and Navigator are stopped, nobody can open a
document, whichever other workloads are left running. Stopping everything therefore does not make
the outage longer in a way users notice. Most of the outage is the time CPE, Navigator and Decision
Center take to start again (§7.2).

### 4.3 Why an action hook on the policy, not a BlueprintBinding

The repository prefers BlueprintBindings, which attach a blueprint to one workload. That does not
fit here:

- **Offline is one operation on the whole namespace**, not one per workload. A per-workload hook
  would stop each workload on its own, in an order Kasten chooses, while the others keep writing.
- **There is no single workload to bind to.** The state is spread over about 40 Deployments, one
  StatefulSet, three PostgreSQL clusters and the operators.
- **An action hook runs once, before discovery and before any snapshot**, and the `onSuccess` /
  `onFailure` hooks run once, after the backup. That is exactly the shape of IBM's procedure:
  stop everything, back up, start everything.

The cost of an action hook is that it is attached to a **policy**, and its only context is the
namespace name. That is acceptable here because the namespace holds one CP4BA deployment and
nothing else.

### 4.4 When content is in S3

A FileNet advanced storage area can write content to an S3 bucket. In that case the content is
**outside the cluster**: Kasten does not capture it, and the bucket must be protected separately
(versioning or object lock on the bucket). The offline approach still matters there: the database
snapshot and the bucket must describe the same moment, which means no document may be added or
deleted between the database snapshot and the bucket protection point.

The same is true of an **external database**: Kasten captures only the PVCs in the namespace. The
Starter deployment keeps all its content and databases on PVCs, which is what this example covers.

### 4.5 Patterns considered and ruled out

| Pattern | Why not |
|---|---|
| 1 — Fence and quiesce a replica | CPE has no read-only replica that can be fenced from the live system |
| 3–9 — Database dumps on a PVC | Dumping the databases while CPE runs does not solve consistency with the storage area files, and a dump loses the incrementality that PVC snapshots keep. They also add a restore procedure IBM does not document for CP4BA |
| 11 — Vendor operator data mover | CP4BA has no backup API in its operator. IBM's automated option is IBM Storage Fusion recipes, which is a different product |

---

## 5. How the scripts take CP4BA offline and back online

The blueprint does not contain the logic itself. It runs two scripts, which an operator also runs
by hand for a restore ([§8](#8-restore)), so the backup and the restore always use
the same code.

### 5.1 `scale-down.sh` — offline

| Step | What it does | Why |
|---|---|---|
| 1 | Suspend every CronJob that is not already suspended, and mark it with `blueprint-ai/suspended=true` | `icp4adeploy-rr-backup` runs every 5 minutes and mounts `icp4adeploy-dba-rr-pvc`. A Job starting during the backup writes to a PVC that should be still |
| 2 | Set `pg.ibm.com/hibernation=on` on each PostgreSQL `Cluster`, and wait until its pods are gone | The PostgreSQL clusters belong to IBM's EDB operator (`pg.ibm.com`), not to a Deployment, so scaling cannot stop them. Hibernation shuts PostgreSQL down cleanly and keeps the PVCs. Only `ibm-pg-operator` acts on the annotation, so this must happen **before** the operators stop |
| 3 | Scale the operators to 0 (the Deployments with the OLM label `olm.owner`) | Otherwise they scale the workloads back up |
| 4 | Scale every other Deployment and StatefulSet to 0 | Stops CPE, Navigator, GraphQL, ODM, LDAP, Zen, Foundational Services |
| 5 | Delete the pods that the `ICP4ACluster` creates directly (for example `icp4adeploy-dba-rr-*`) | No Deployment owns them. `ibm-cp4a-operator` creates them again after scale-up |
| 6 | Delete the Jobs whose pods mount a PVC, finished Jobs included | A pod in phase `Succeeded` still holds its PVC (observed, see [§8.4](#84-what-we-discovered)) |
| 7 | Wait until **no pod, in any phase,** references a PVC; fail after 10 minutes | Stopping a pod unmounts its volumes and flushes the page cache, so the [sync rule](../../AGENTS.md#always-sync-the-filesystem-before-kasten-snapshots-the-pvc) is met by the stop itself. The check proves it |

Before scaling a workload to 0, the script saves its replica count in the annotation
`blueprint-ai/replicas` **on the workload itself**. A workload already at 0 is skipped, so running
the script twice never overwrites a saved count with 0.

### 5.2 `scale-up.sh` — online

| Step | What it does |
|---|---|
| 1 | Start `ibm-pg-operator`. Give back to each PostgreSQL PVC its `ownerReference` to its `Cluster` if it has lost it (after a restore, see [§8.4](#84-what-we-discovered)). Remove the hibernation annotation and wait until each cluster is `Ready` |
| 2 | Scale every Deployment and StatefulSet back to its saved count — databases first, then workloads |
| 3 | Scale the operators back. They find workloads that already run, so they have nothing to undo |
| 4 | Remove the `blueprint-ai/replicas` annotations |
| 5 | Resume the CronJobs that `scale-down.sh` suspended. Last, so that no Job runs against an application that is still starting |

Only what `scale-down.sh` recorded is started. A workload that was at 0 before, or a CronJob that
was already suspended, stays as it was.

---

## 6. Deploy the blueprint

### 6.1 Prerequisites

- Kasten `9.0.10` or later, with a **Location** profile (the hooks need one).
- The CSI storage classes used by CP4BA support snapshots. On the test cluster:
  `ocs-storagecluster-ceph-rbd` and `ocs-storagecluster-cephfs` (OpenShift Data Foundation), both
  with a `VolumeSnapshotClass` registered with Kasten.
- An **export** to the Location profile. The restore and the disaster recovery use the exported
  restore point (§8). For CephFS, a shallow exporter storage class (`backingSnapshot: "true"`,
  here `ocs-storagecluster-cephfs-shallow`) avoids a full copy of every file at each export; see
  [policy.yaml](policy.yaml).
- The shared image `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.10` (`kanister-tools` +
  `kubectl` + `jq`, [Dockerfile](../../images/kasten-tools/Dockerfile)). Its tag must match your
  Kasten version.
- The CP4BA namespace holds **one** CP4BA deployment and nothing else (§4.3).

### 6.2 Install

```bash
cd cp4ba-example/26.0.0

# 1. The scripts, as a ConfigMap the hook pod mounts. Run this again after any change to them.
kubectl create configmap cp4ba-offline-scripts -n kasten-io \
  --from-file=scripts/scale-down.sh --from-file=scripts/scale-up.sh \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. The blueprint.
kubectl apply -f blueprint.yaml

# 3. The policy: replace <LOCATION_PROFILE> first, and the namespace if it is not cp4ba-starter.
kubectl apply -f policy.yaml
```

The hook pods run in `kasten-io` with Kasten's service account, which can scale workloads, patch
CRs and delete pods in the application namespace. No extra RBAC is needed.

**To add the hooks to an existing policy** (this is what was done on the test cluster):

```bash
kubectl patch policy cp4ba-starter-backup -n kasten-io --type=json -p '[{"op":"add",
  "path":"/spec/actions/0/backupParameters","value":{
    "profile":{"name":"<LOCATION_PROFILE>","namespace":"kasten-io"},
    "hooks":{
      "preHook":  {"blueprint":"cp4ba-offline-bp","actionName":"backupPrehook"},
      "onSuccess":{"blueprint":"cp4ba-offline-bp","actionName":"backupPosthook"},
      "onFailure":{"blueprint":"cp4ba-offline-bp","actionName":"backupPosthookOnFailure"}}}}]'
```

`/spec/actions/0` must be the `backup` action. Check with
`kubectl get policy cp4ba-starter-backup -n kasten-io -o jsonpath='{.spec.actions[0].action}'`.

### 6.3 Blueprint actions

| Action | Policy hook | What it does |
|---|---|---|
| `backupPrehook` | `preHook` | Runs `scale-down.sh`: CP4BA offline, no pod references a PVC (§5.1) |
| `backupPosthook` | `onSuccess` | Runs `scale-up.sh`: CP4BA online again (§5.2) |
| `backupPosthookOnFailure` | `onFailure` | Runs `scale-up.sh`. Safe at any point, even after a half-finished `scale-down.sh`, because it starts only what was recorded |

---

## 7. Backup test

### 7.1 Procedure

1. **Create the test data** before the backup:
   - FileNet: [mini-tutorial-filenet.md §4.1 and §4.2](mini-tutorial-filenet.md#41-prepare-the-test-files)
     (three documents, a second version of the first, and `checksums.txt`);
   - ODM: [mini-tutorial-odm.md §4.1 to §4.3](mini-tutorial-odm.md#4-hands-on-deploy-and-call-a-decision-service)
     (import, deploy and call a decision service).
2. **Run the policy** from the dashboard, or:

   ```bash
   kubectl create -f - <<EOF
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RunAction
   metadata:
     generateName: run-cp4ba-offline-
   spec:
     subject:
       kind: Policy
       name: cp4ba-starter-backup
       namespace: kasten-io
   EOF
   ```

3. **Follow it.** The hook output is in the Kanister logs:

   ```bash
   kubectl logs -n kasten-io -l app=kanister-svc --tail=200
   kubectl get backupactions -n cp4ba-starter --sort-by=.metadata.creationTimestamp
   ```

4. **Check CP4BA is back**: every Deployment available, the `ICP4ACluster` `Ready`, and the
   CronJobs resumed:

   ```bash
   kubectl get icp4acluster icp4adeploy -n cp4ba-starter -o jsonpath='{.status.conditions}'
   kubectl get cronjob -n cp4ba-starter
   ```

### 7.2 Result on the test cluster

Run on 2026-11-10 through the policy `cp4ba-starter-backup` (backup and export), with the
FileNet dataset of the tutorial in place. Times are from the Kasten and Kubernetes objects (UTC).

| Time | Event |
|---|---|
| 13:41:53 | `backupPrehook` starts `scale-down.sh` |
| 13:42:16 | Every Deployment at 0, PostgreSQL hibernated, no pod references a PVC |
| 13:43:35 – 13:43:57 | Kasten requests the 28 CSI snapshots (after discovery and the capture of the specs) |
| 13:43:59 | `backupPosthook` starts `scale-up.sh` |
| 13:45:59 | BackupAction `Complete` |
| 13:45:43 – 13:49:14 | ExportAction `Complete`, while CP4BA is already online again |
| 13:51:31 | The last Deployment (CPE, Navigator, GraphQL, Decision Center) is available again |

| Measurement | Value |
|---|---|
| Offline (prehook start to posthook start) | about 2 minutes |
| Users cannot work (prehook start to last Deployment available) | about 10 minutes |
| Backup action | 4 minutes, 28 PVCs |
| Export | 3.5 minutes, outside the outage |

**Most of the outage is the restart**, not the snapshots: CPE, Navigator and Decision Center are
Java servers that take 5 to 7 minutes to become ready. With CSI snapshots on Ceph, the snapshot
step itself takes about 25 seconds.

> **If you compare snapshot times with hook times, check the clocks first.** On the test cluster,
> the `status.creationTime` of the CephFS snapshots was about 21 seconds **after** the time the
> posthook started, which looks like snapshots taken while CP4BA was already online. It was clock
> skew: the control-plane nodes disagreed by about 20 seconds, and the Ceph pods ran about 22
> seconds ahead of them. In Kasten `9.0.10` the post hook runs only after the snapshot phase has
> finished, and that phase waits until every snapshot is cut (`snapshot_data_phase.go`:
> *"only return a result once the snapshot has finished"*). Fix NTP on the nodes rather than
> trusting timestamps from different machines.

### 7.3 What the restore point contains

Kasten captures the Kubernetes objects **while the namespace is offline**. So in the restore
point, every Deployment and StatefulSet has `replicas: 0` and its saved count in
`blueprint-ai/replicas`, every PostgreSQL `Cluster` has `pg.ibm.com/hibernation: on`, and the
CronJobs are suspended. The restore (§8) does not use these objects: it restores the PVCs only.
The disaster recovery (§9) does: it brings them back in that offline state, and `scale-up.sh`
starts them.

The restore point also contains the `Namespace` object, with the OpenShift UID range and SELinux
label of the namespace (`openshift.io/sa.scc.uid-range`, `sa.scc.supplemental-groups`,
`sa.scc.mcs`). The disaster recovery needs them (§9.3).

---

## 8. Restore

**Restore or disaster recovery?**

|  | Restore (§8) | Disaster recovery (§9) |
|---|---|---|
| **When** | CP4BA is installed and runs, but its data is wrong: deleted documents, a bad change, a corrupted database | The namespace is gone, or CP4BA cannot be repaired in place |
| **What comes back** | The 28 PVCs only. The CRs, Secrets and Deployments on the cluster are kept | Everything in the namespace: operators (through OLM), CRs, Secrets, ConfigMaps, certificates, workloads and PVCs |
| **Procedure** | `scale-down.sh`, a restore of the PVCs, `scale-up.sh` | [scripts/dr-restore.sh](scripts/dr-restore.sh), on a cluster where the namespace does not exist |
| **Do not** | Restore Secrets or CRs: they match the running installation | Run IBM's install scripts first (§9.1) |

**Both use the exported restore point**, the copy in the Location profile, never the local one:

- The local restore point is lost with the namespace. On the test cluster every
  `VolumeSnapshotClass` has `deletionPolicy: Delete`, and Kasten keeps the `VolumeSnapshot`
  objects in the application namespace, so deleting the namespace deletes the snapshots.
- 18 of the 28 PVCs are CephFS. A restore from a local snapshot clones it, and a CephFS clone is a
  full copy of every file: about 20 minutes for 1 GB on Ceph, against a few seconds for RBD. See
  [the CephFS notes](https://github.com/kastendevhub/enterprise-blueprint/tree/main/ceph/cephfs).
  The same notes explain the shallow exporter class that [policy.yaml](policy.yaml) uses for the
  export.

### 8.1 Before the restore: change the data

So that the test proves something, change the data after the backup:
[mini-tutorial-filenet.md §4.5](mini-tutorial-filenet.md#45-changes-the-restore-must-undo) (delete
`kasten-doc-2.txt`, add `kasten-doc-4.txt`), and
[mini-tutorial-odm.md §4.4](mini-tutorial-odm.md#44-make-a-change-that-only-one-store-sees).

### 8.2 Steps

```bash
cd cp4ba-example/26.0.0/scripts

# 1. Take CP4BA offline. The restore cannot replace a PVC that a pod still uses.
./scale-down.sh cp4ba-starter

# 2. Restore the PVCs from the EXPORTED restore point (below). Wait until it is Complete.

# 3. Bring CP4BA online. This also repairs the PostgreSQL PVCs (§8.4).
./scale-up.sh cp4ba-starter
```

**Step 2.** List the restore points and pick the exported one. Its `RestorePointContent` has the
label `k10.kasten.io/exportProfile`:

```bash
kubectl get restorepoint -n cp4ba-starter \
  -o custom-columns=NAME:.metadata.name,CREATED:.metadata.creationTimestamp,CONTENT:.spec.restorePointContentRef.name
kubectl get restorepointcontent -l k10.kasten.io/appNamespace=cp4ba-starter \
  -o custom-columns=NAME:.metadata.name,EXPORTED_TO:.metadata.labels.k10\\.kasten\\.io/exportProfile

kubectl create -f - <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata:
  generateName: restore-
  namespace: cp4ba-starter
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: <EXPORTED_RESTORE_POINT>
    namespace: cp4ba-starter
  targetNamespace: cp4ba-starter
  filters:
    includeResources:
    - {group: "", version: v1, resource: persistentvolumeclaims}
EOF
kubectl get restoreaction -n cp4ba-starter -w
```

From the dashboard: *Applications → cp4ba-starter → Restore*, pick the **exported** restore
point, keep the same namespace, and under the resources select **only** the
`persistentvolumeclaims` (all 28). Do not choose *data only*.

**After the restore**, run the restore check:
[mini-tutorial-filenet.md §4.4](mini-tutorial-filenet.md#44-the-restore-check). Listing the
documents is not enough: download each one and compare it with `checksums.txt`.

### 8.3 Why a filter on PVCs, and not *data only*

Both restore only volumes, but Kasten decides differently which existing PVCs it may replace.
Read in the Kasten `9.0.10` source (`restore_app_prepare.go`, `restore_precheck.go`), then
reproduced in a test namespace:

| Restore | Which existing PVCs Kasten deletes before restoring | Result here |
|---|---|---|
| `dataOnly: true` | Only the PVCs mounted by a **Deployment, StatefulSet or Pod of the restore point that exists in the namespace now**. For a StatefulSet it counts the PVCs from its **current** replica count | 17 of 28. Not deleted: the 4 PostgreSQL PVCs (owned by a `Cluster`, which is not a workload for Kasten), the 3 `zen-minio` PVCs (the StatefulSet is at 0 after `scale-down.sh`, so Kasten counts 0 PVCs), and 4 PVCs mounted only by CronJob pods or by pods the `ICP4ACluster` creates directly |
| filter `persistentvolumeclaims` | Every PVC it restores | 28 of 28 |

With the exported restore point, a PVC that Kasten did not delete is not overwritten. Kasten
stamps every PVC it creates with the ID of the restore that created it. When the name is already
taken by a PVC without that stamp — from the IBM install, or from an earlier Kasten restore — it
stops: *"Target PVC already exists and is not owned by this restore manifest; refusing to
overwrite"*. The RestoreAction fails, names each PVC, and Kasten deletes the PVCs it had already
created. The same 11 PVCs were refused in two separate tests.

The filter has a second benefit. After any restore, Kasten copies the manually set
`ownerReferences` of the objects in the restore point onto the objects in the namespace. With
*data only*, that includes the PostgreSQL `Cluster`, which has a reference to the `ICP4ACluster`
or the `ZenService`. Updating it goes through IBM's PostgreSQL webhook (`failurePolicy: Fail`),
which does not answer while `ibm-pg-operator` is at 0. The RestoreAction then ends `Failed` after
the volumes are restored. With the filter, only PVCs are in scope, and their references are set
by a controller (`controller: true`), which Kasten skips (`add_owner_refs.go`).

### 8.4 What we discovered

These are the problems the first restore ran into. The scripts now handle all of them.

| Symptom | Cause | Fix in the scripts |
|---|---|---|
| The operators scale the workloads back up | They reconcile the replica count | Operators are stopped first (step 3), workloads after. OLM did **not** scale the operators back up during about 40 minutes at 0 |
| The PostgreSQL pods keep running after scaling everything to 0 | They belong to a `pg.ibm.com` `Cluster`, not to a Deployment or StatefulSet | Hibernation (step 2), **while** `ibm-pg-operator` still runs: it is the only thing that acts on the annotation |
| The restore waits forever on `icp4adeploy-dba-rr-pvc`, which stays `Terminating` | The CronJob `icp4adeploy-rr-backup` (every 5 minutes) started a pod that mounts it. The `pvc-protection` finalizer keeps a PVC while any pod references it | CronJobs suspended (step 1), their Jobs deleted (step 6) |
| Same PVC, still `Terminating` after the running Job is gone | A **finished** pod (phase `Succeeded`) of an earlier run still referenced the PVC, and this was enough to block it | Step 6 deletes finished Jobs too; step 7 checks pods in every phase |
| After the restore, the three PostgreSQL clusters report *"Cluster is unrecoverable and needs manual intervention"*, and `scale-up.sh` waits forever | The operator counts only the PVCs that have an `ownerReference` to their `Cluster`. That reference is set by a controller, and Kasten does not restore controller references (§8.3). The operator sees no PVC and refuses to create the primary (*"refusing to create the primary instance while the latest generated serial is not zero"*). The data is intact | `scale-up.sh` puts the `ownerReference` back on each PVC that carries the label `pg.ibm.com/cluster` and has no owner, before it ends the hibernation |

The first restore was validated on 2026-11-10, from the **local** restore point with *data only*:
`kasten-doc-2.txt` was back and its content matched `checksums.txt`, `kasten-doc-4.txt` was gone,
`kasten-doc-1.txt` was at version 2, and the `ICP4ACluster` reported `Ready=True`. So the
PostgreSQL data was restored that time, although the rule of §8.3 says *data only* does not
delete the PostgreSQL PVCs. That restore used the local path, which creates PVCs differently, and
it was not analysed further. Do not rely on it: the local restore point has the problems listed at
the top of §8, and the procedure above does not depend on that difference.

The procedure above (exported restore point, PVC filter) was not yet run as a restore into the
running CP4BA (§11). The PVC filter itself was tested: restored into an empty test namespace, it
brought back all 28 PVCs.

Other restored PVCs (for example `icp4adeploy-dba-rr-pvc`) also come back without their
ownerReferences. Nothing failed because of it; the reference is used when the owning CR is
deleted, to delete the PVC with it.

---

## 9. Disaster recovery

Use this procedure only when the CP4BA namespace is **gone**: deleted (here with IBM's
`cp4a-clean-up.sh`), or lost with its operators. If the namespace and its operators still run and
you only need older data, use the restore of §8.

[scripts/dr-restore.sh](scripts/dr-restore.sh) **is** the recovery: it rebuilds the whole namespace
from the exported restore point, and OLM installs the operators again from the restored
Subscriptions. You do not run IBM's deployment scripts. Validated on 2026-11-11 on the test cluster.

### 9.1 Why not "install CP4BA again, then restore the PVCs"

This was tried first, on 2026-11-10: IBM's two install scripts with the same answers as the first
install, then `scale-down.sh`, a restore of the PVCs, `scale-up.sh`. The data came back, but
CP4BA did not start until five more problems were fixed by hand:

| Problem | Cause |
|---|---|
| PostgreSQL crashed with `chmod: operation not permitted`; on CephFS the files were not even readable | The new namespace got a new OpenShift UID range. The files on the PVCs belong to the old one |
| FileNet, then LDAP, then IAM rejected their credentials | The install generated new passwords, OAuth clients and encryption keys, and rendered them into ConfigMaps. The restored databases contain the old ones |
| After restoring the Secrets and ConfigMaps too, some certificates were invalid | cert-manager compares the private key in each Secret with the current `CertificateRequest`. The requests were the fresh install's, so every restored TLS Secret was flagged *"Secret contains a private key that does not match the current CertificateRequest"* and reissued. The CA `cs-ca-certificate` has `rotationPolicy: Always`, so it got a **new** key, and some leaves were signed during the change |
| Three routes returned 503 | Their `destinationCACertificate` still held the fresh install's CA |
| The PVC restore was refused for 11 PVCs, then ended `Failed` | §8.3 |

The install creates the same objects as the backup holds, with different values, and every one of
them must then be replaced. Restoring the whole namespace, and letting OLM install the operators,
avoids the problem instead of repairing it.

### 9.2 Procedure

Requirements:

- The **exported** restore point (§8). Find its `RestorePointContent`; it survives the namespace:

  ```bash
  kubectl get restorepointcontent -l k10.kasten.io/appNamespace=cp4ba-starter \
    -o custom-columns=NAME:.metadata.name,CREATED:.metadata.creationTimestamp,EXPORTED_TO:.metadata.labels.k10\\.kasten\\.io/exportProfile
  ```

- The cluster-scoped prerequisites are still there: the CP4BA CRDs, cert-manager, IBM Licensing.
  IBM's clean-up keeps them. This procedure recovers a deleted namespace **on the same cluster**;
  a new cluster is not validated (§11).
- The namespace does **not** exist. Do not run IBM's install scripts.

```bash
cd cp4ba-example/26.0.0/scripts
./dr-restore.sh cp4ba-starter <EXPORTED_RESTORE_POINT_CONTENT>
```

If the script stops, fix the cause and resume from the step that failed:
`FROM_STEP=<N> ./dr-restore.sh cp4ba-starter <EXPORTED_RESTORE_POINT_CONTENT>`. Every step can run
again. After a failed step 7, resume from step 6.

Then check the result like a restore: [mini-tutorial-filenet.md §4.4](mini-tutorial-filenet.md#44-the-restore-check).
The users and passwords are the ones of the backup, because LDAP is restored too.

### 9.3 What the script does, and why

| Step | What it does | Why |
|---|---|---|
| 1 | Create the namespace with the UID range, supplemental groups and SELinux label read from the restore point, then a `RestorePoint` bound to the exported content | OpenShift assigns a new range only when these annotations are missing. With the old range, the files on the PVCs belong to the pods again |
| 2 | Restore the `CatalogSources`, the `OperatorGroup` and the `Subscriptions`. Remove `status` and the annotation `olm.generated-by` from the Subscriptions | Kasten restores the Subscriptions **with** their status and their `olm.generated-by` annotation. Both name InstallPlans that no longer exist; OLM then logs *"installplans … not found"* and installs nothing, for every Subscription of the namespace |
| 3 | Wait until OLM has installed as many CSVs as the backup holds | The catalog images are pinned by digest, so OLM installs the same operator versions (checked: the 21 CSVs are identical) |
| 4 | Restore `CommonService`, `OperandConfig` and `OperandRegistry`, overwriting, while `ibm-common-service-operator` runs | Its validating webhook (`failurePolicy: Fail`, `CREATE` and `UPDATE`) must answer |
| 5 | `scale-down.sh`, then start `ibm-pg-operator` again | The operators must not reconcile while the restore runs. The PostgreSQL webhook must answer for the `Cluster` objects. `ibm-pg-operator` creates nothing by itself |
| 6 | Delete the objects the operators created during the few minutes they ran: a new CA (`cs-ca-certificate`, its issuers and Secret), the `meta-api` keys, a few ConfigMaps. Keep what OLM created | While a Certificate exists, cert-manager reissues it as soon as its Secret changes. Without this step, the restore of the backup's CA Secret triggers a reissue of the CA with a new key, during the restore |
| 7 | Restore everything else, overwriting. Excluded: what OLM creates (CSVs, InstallPlans, OperatorConditions, objects labelled `olm.owner`, objects owned by a CSV), the three CommonService objects, and the webhook certificate Secrets of OLM (`<operator>-service-cert`) | The PVCs come back with their data, the workloads at 0, PostgreSQL hibernated, the CronJobs suspended (§7.3). Secrets, certificates, CertificateRequests and routes come back **together**, so they agree with each other |
| 8 | `scale-up.sh` | As after a backup |

### 9.4 What we discovered

| Symptom | Cause | Fix in the script |
|---|---|---|
| OLM installs no operator; all Subscriptions stay without status | Kasten restores the Subscription status and `olm.generated-by`, which point to deleted InstallPlans | Step 2 removes both |
| `failed calling webhook "mcluster.pg.ibm.com": x509: certificate signed by unknown authority` | OLM generates a serving certificate for each operator webhook, in a Secret `<operator>-service-cert` with no label and no owner. The restore overwrote it with the backup's copy, signed by the CA of the OLM that installed the operators the first time | Step 7 excludes these Secrets. The script finds them through the Role of the same name, which a CSV owns |
| `admission webhook "webhook.cert-manager.io" denied the request: … cannot change spec after creation`, and the real error is hidden | Kasten **retries** a failed restore job. On the retry, the objects created by the first attempt exist, and *overwrite* means an update. cert-manager forbids any update of a `CertificateRequest`. Read the first error in the executor logs, not the one in the RestoreAction | Fix the first error, then resume from step 6, which removes what the failed attempt restored |
| A new CA and leaf certificates reissued during the restore | The operators ran a few minutes before `scale-down.sh` and created their own CA | Step 6 |

### 9.5 Result on the test cluster

| Check | Result |
|---|---|
| Secrets compared with the namespace before the deletion (SHA-256 of the data) | 110 identical. Different: the OpenShift `dockercfg` pull secrets (generated per ServiceAccount), the 3 OLM webhook certificates (generated by OLM, see §9.4) and `icp4adeploy-rr-jwt-secret` (it holds the backup's value; the live one had changed since) |
| Certificates | All 19 `Ready`. The 17 issued by a CA verify against it with `openssl verify` |
| Routes | No change needed; `cpd`, Navigator, ACCE and Decision Center answer |
| CPE health page | Domain `P8DOMAIN`, 4 object stores, 8 storage areas |
| Logs | No database, LDAP, IAM or TLS error |
| Documents | Checked in Navigator as `cp4admin` with the password from before the deletion: `kasten-doc-1.txt` at version 2, `kasten-doc-2.txt` and `kasten-doc-3.txt` present, `kasten-doc-4.txt` absent, as at the time of the backup |

| Step | Duration |
|---|---|
| 1–2: namespace, OLM objects | 1 minute |
| 3: OLM installs the 21 operators | 8 minutes |
| 4–6 | 2 minutes |
| 7: restore of the specs and the 28 PVCs | 7 minutes |
| 8: `scale-up.sh`, then until the last Deployment is available | 11 minutes |
| **Total, from an empty cluster to CP4BA available** | **about 30 minutes** |

IBM's `cp4a-clean-up.sh`, which deleted the namespace for the test, took about 21 minutes and is
not part of the recovery.

---

## 10. Clean up

```bash
# The policy, the blueprint and the scripts ConfigMap
kubectl delete policy cp4ba-starter-backup -n kasten-io
kubectl delete blueprint cp4ba-offline-bp -n kasten-io
kubectl delete configmap cp4ba-offline-scripts -n kasten-io

# The restore points of the test namespace
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=cp4ba-starter
```

To remove CP4BA itself, see [install-cp4ba.md](install-cp4ba.md).

---

## 11. Not validated yet

| Topic | Status |
|---|---|
| **Restore with the procedure of §8.2** | The exported restore point with a PVC filter was tested into an empty namespace (28/28 PVCs), not yet as a restore into the running CP4BA with a data check |
| **Export with the shallow CephFS class** | The `exporterStorageClassName` override in [policy.yaml](policy.yaml) is applied and the policy validates; no backup has run with it yet |
| **Disaster recovery on a new cluster** | Validated on the same cluster only (§9). A new cluster also needs IBM's cluster-scoped prerequisites (CRDs, cert-manager, IBM Licensing), without the namespace part of the install |
| **ODM restore check** | The design treats ODM like FileNet (its state is in PostgreSQL, which is captured offline). The ODM check in [mini-tutorial-odm.md](mini-tutorial-odm.md) was not run |
| **Production deployments** | External databases, several replicas, other capabilities: not tested (see the warning at the top) |
| **PVCs IBM says not to back up** | The 25.0.0 page lists some (for example `user-home-pvc`). This blueprint backs up every PVC of the Starter namespace; a production deployment may want a policy filter |
| **Foundational Services** | Stopped, restarted and restored with the rest of the namespace. IBM documents a separate procedure for them; the DR test did not need it on the same cluster |

---

## 12. Versions

| Component | Version |
|---|---|
| OpenShift / Kubernetes | 4.21.30 / 1.34.9 |
| Kasten | 9.0.10 (OLM operator) |
| Kasten tools image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.10` |
| CP4BA | 26.0.0-IF003, Starter |
| Content (FileNet, IBM Content Cortex) | 26.0.0 (`productVersion` of the CPE pod) |
| ODM | 9.6.0.0-IF005 (`productVersion` 26.0.0) |
| PostgreSQL | 16.15, IBM EDB operator `ibm-pg-operator` 28.4.3 |
| cert-manager | cert-manager Operator for Red Hat OpenShift (namespace `cert-manager`) |
| Storage | OpenShift Data Foundation (`ocs-storagecluster-ceph-rbd`, `ocs-storagecluster-cephfs`, and `ocs-storagecluster-cephfs-shallow` for the export) |

---

## 13. Initial prompt

> Create a blueprint to protect a CP4BA install that deploys FileNet and ODM.
