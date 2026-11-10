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
> - **Validated on the Starter deployment only**, on a test cluster
>   ([install-cp4ba.md](install-cp4ba.md)). It was **not** validated on a production CP4BA
>   installation, which has external databases, more replicas, and more components.
> - **The restore was validated by hand, in the same namespace, on the same cluster.** A
>   disaster-recovery restore (namespace deleted, CP4BA installed again) is not validated yet.
>
> Like every directory in this repository, this one is an **example**, not a general-purpose
> solution. Copy it, adapt it, and validate it again on your own deployment. See the ownership
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
by hand for a restore ([§8](#8-restore-manual-procedure)), so the backup and the restore always use
the same code.

### 5.1 `scale-down.sh` — offline

| Step | What it does | Why |
|---|---|---|
| 1 | Suspend every CronJob that is not already suspended, and mark it with `blueprint-ai/suspended=true` | `icp4adeploy-rr-backup` runs every 5 minutes and mounts `icp4adeploy-dba-rr-pvc`. A Job starting during the backup writes to a PVC that should be still |
| 2 | Set `pg.ibm.com/hibernation=on` on each PostgreSQL `Cluster`, and wait until its pods are gone | The PostgreSQL clusters belong to IBM's EDB operator (`pg.ibm.com`), not to a Deployment, so scaling cannot stop them. Hibernation shuts PostgreSQL down cleanly and keeps the PVCs. Only `ibm-pg-operator` acts on the annotation, so this must happen **before** the operators stop |
| 3 | Scale the operators to 0 (the Deployments with the OLM label `olm.owner`) | Otherwise they scale the workloads back up |
| 4 | Scale every other Deployment and StatefulSet to 0 | Stops CPE, Navigator, GraphQL, ODM, LDAP, Zen, Foundational Services |
| 5 | Delete the pods that the `ICP4ACluster` creates directly (for example `icp4adeploy-dba-rr-*`) | No Deployment owns them. `ibm-cp4a-operator` creates them again after scale-up |
| 6 | Delete the Jobs whose pods mount a PVC, finished Jobs included | A pod in phase `Succeeded` still holds its PVC (observed, see [§8.3](#83-what-we-discovered)) |
| 7 | Wait until **no pod, in any phase,** references a PVC; fail after 10 minutes | Stopping a pod unmounts its volumes and flushes the page cache, so the [sync rule](../../AGENTS.md#always-sync-the-filesystem-before-kasten-snapshots-the-pvc) is met by the stop itself. The check proves it |

Before scaling a workload to 0, the script saves its replica count in the annotation
`blueprint-ai/replicas` **on the workload itself**. A workload already at 0 is skipped, so running
the script twice never overwrites a saved count with 0.

### 5.2 `scale-up.sh` — online

| Step | What it does |
|---|---|
| 1 | Start `ibm-pg-operator`. Give back to each PostgreSQL PVC its `ownerReference` to its `Cluster` if it has lost it (after a restore, see [§8.3](#83-what-we-discovered)). Remove the hibernation annotation and wait until each cluster is `Ready` |
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
CronJobs are suspended. A restore of the PVCs only (§8) does not use these objects. A restore of
the whole namespace would bring them back in that offline state, and `scale-up.sh` would then start
them — this is what the disaster-recovery test will check ([§10](#10-not-validated-yet)).

---

## 8. Restore: manual procedure

The restore is done **by hand**, with the same scripts as the backup. It restores **only the
PVCs**, in the same namespace. All other objects (CRs, Secrets, Deployments) stay as they are on
the cluster, so the operators never see a half-restored object.

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

# 2. Restore the PVCs only (see below), and wait until the RestoreAction is Complete.

# 3. Bring CP4BA online. This also repairs the PostgreSQL PVCs (§8.3).
./scale-up.sh cp4ba-starter
```

**Step 2 from the dashboard:** *Applications → cp4ba-starter → Restore*, pick the restore point,
choose a restore of **data only** (volumes), keep the same namespace, and select every PVC. This is
how the test was run: the RestoreAction listed all 28 PVCs by name.

**Step 2 from the command line** (equivalent; Kasten restores every PVC in the restore point):

```bash
kubectl get restorepoint -n cp4ba-starter
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
    name: <RESTORE_POINT_NAME>
    namespace: cp4ba-starter
  targetNamespace: cp4ba-starter
  dataOnly: true
EOF
kubectl get restoreaction -n cp4ba-starter -w
```

Kasten deletes each existing PVC and creates it again from the snapshot. On the test cluster the
restore of 28 PVCs took about 13 minutes.

**After the restore**, run the restore check:
[mini-tutorial-filenet.md §4.4](mini-tutorial-filenet.md#44-the-restore-check). Listing the
documents is not enough: download each one and compare it with `checksums.txt`.

### 8.3 What we discovered

These are the problems the first restore ran into. The scripts now handle all of them.

| Symptom | Cause | Fix in the scripts |
|---|---|---|
| The operators scale the workloads back up | They reconcile the replica count | Operators are stopped first (step 3), workloads after. OLM did **not** scale the operators back up during about 40 minutes at 0 |
| The PostgreSQL pods keep running after scaling everything to 0 | They belong to a `pg.ibm.com` `Cluster`, not to a Deployment or StatefulSet | Hibernation (step 2), **while** `ibm-pg-operator` still runs: it is the only thing that acts on the annotation |
| The restore waits forever on `icp4adeploy-dba-rr-pvc`, which stays `Terminating` | The CronJob `icp4adeploy-rr-backup` (every 5 minutes) started a pod that mounts it. The `pvc-protection` finalizer keeps a PVC while any pod references it | CronJobs suspended (step 1), their Jobs deleted (step 6) |
| Same PVC, still `Terminating` after the running Job is gone | A **finished** pod (phase `Succeeded`) of an earlier run still referenced the PVC, and this was enough to block it | Step 6 deletes finished Jobs too; step 7 checks pods in every phase |
| After the restore, the three PostgreSQL clusters report *"Cluster is unrecoverable and needs manual intervention"*, and `scale-up.sh` waits forever | The operator counts only the PVCs that have an `ownerReference` to their `Cluster`. Kasten creates the restored PVC **without** ownerReferences, because they name the UID of an object from the backup. The operator sees no PVC and refuses to create the primary (*"refusing to create the primary instance while the latest generated serial is not zero"*). The data is intact | `scale-up.sh` puts the `ownerReference` back on each PVC that carries the label `pg.ibm.com/cluster` and has no owner, before it ends the hibernation |

The restore was validated on 2026-11-10: `kasten-doc-2.txt` was back and its content matched
`checksums.txt`, `kasten-doc-4.txt` was gone, `kasten-doc-1.txt` was at version 2, and the
`ICP4ACluster` reported `Ready=True`.

Other restored PVCs (for example `icp4adeploy-dba-rr-pvc`) also come back without their
ownerReferences. Nothing failed because of it; the reference is used when the owning CR is
deleted, to delete the PVC with it.

---

## 9. Clean up

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

## 10. Not validated yet

| Topic | Status |
|---|---|
| **Disaster recovery**: delete the namespace, install CP4BA again, restore | Next step. The restore point holds the objects in their offline state (§7.3), which should help |
| **ODM restore check** | The design treats ODM like FileNet (its state is in PostgreSQL, which is captured offline). The ODM check in [mini-tutorial-odm.md](mini-tutorial-odm.md) was not run |
| **Production deployments** | External databases, several replicas, other capabilities: not tested (see the warning at the top) |
| **PVCs IBM says not to back up** | The 25.0.0 page lists some (for example `user-home-pvc`). This blueprint backs up every PVC of the Starter namespace; a production deployment may want a policy filter |
| **Foundational Services** | Stopped and restarted with the rest. Whether a DR restore needs IBM's separate Foundational Services procedure is not measured |

---

## 11. Versions

| Component | Version |
|---|---|
| OpenShift / Kubernetes | 4.21.30 / 1.34.9 |
| Kasten | 9.0.10 (OLM operator) |
| Kasten tools image | `ghcr.io/kastenhq/blueprint-ai/kasten-tools:9.0.10` |
| CP4BA | 26.0.0-IF003, Starter |
| Content (FileNet, IBM Content Cortex) | 26.0.0 (`productVersion` of the CPE pod) |
| ODM | 9.6.0.0-IF005 (`productVersion` 26.0.0) |
| PostgreSQL | 16.15, IBM EDB operator `ibm-pg-operator` 28.4.3 |
| Storage | OpenShift Data Foundation (`ocs-storagecluster-ceph-rbd`, `ocs-storagecluster-cephfs`) |

---

## 12. Initial prompt

> Create a blueprint to protect a CP4BA install that deploys FileNet and ODM.
