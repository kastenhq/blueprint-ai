# CP4BA (FileNet Content Manager and ODM) — Kasten blueprint

> ## ⚠️ Work in progress — no blueprint yet
>
> This directory records the **design** of a Kasten backup for IBM Cloud Pak for Business
> Automation (CP4BA). CP4BA is not installed yet, nothing below has been tested, and there is no
> `blueprint.yaml`. Every statement about the cluster is a plan until the measurements in
> [mini-tutorial-filenet.md §5](mini-tutorial-filenet.md#5-what-the-cluster-actually-shows-to-be-measured)
> and [mini-tutorial-odm.md §5](mini-tutorial-odm.md#5-what-the-cluster-actually-shows-to-be-measured)
> are done.
>
> Like every directory in this repository, this one is an **example**, not a general-purpose
> solution. It is built for one CP4BA version, one deployment type (Starter), one storage layout
> and one cluster. Copy it, adapt it, and validate it again on your own deployment. See the
> ownership disclaimer in the [repository README](../README.md).

---

## 1. Files in this directory

| File | What it is |
|---|---|
| [README.md](README.md) | This file: scope, IBM's backup documentation, and the design reasoning |
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

The offline procedure removes the problem instead of working around it. Once CPE, Navigator and the
ODM servers are scaled down, nothing writes to the databases or to the storage areas. The order in
which Kasten snapshots the PVCs no longer matters, because nothing changes between the first
snapshot and the last.

This maps to the **Quiesce** pattern of this repository (pattern 2 in
[AGENTS.md](../AGENTS.md#step-1--choose-pattern-and-document-it)):

| Phase | What it does |
|---|---|
| `backupPrehook` | Scale down the CP4BA operators, then every `icp4adeploy` deployment and StatefulSet, and wait until their pods are gone |
| Kasten | Snapshots every PVC in the namespace and captures the Kubernetes objects |
| `backupPosthook` | Scale up the `icp4adeploy` workloads, then the operators |

**Accepted:** this is the approach chosen for this example (2026-10-20), because it is IBM's own
procedure. It is confirmed formally in Step 1 of the workflow, once the measurements in the
tutorials show the real PVCs and databases.

### 4.3 What the offline path costs, and what is still open

| Topic | Expected | To check |
|---|---|---|
| **Downtime** | Users cannot open documents or call decision services between scale-down and scale-up. On Ceph, CSI snapshots take seconds, so most of the outage is the time CPE and Navigator need to stop and start | Measure scale-down, snapshot and scale-up times |
| **PostgreSQL** | The databases keep running, but with no client connected nothing writes to them. A snapshot of an idle PostgreSQL is crash-consistent and replays its WAL on start | Is the Starter PostgreSQL managed by an `icp4adeploy` StatefulSet (then IBM's procedure stops it too) or by the CloudNativePG operator? |
| **File system flush** | Stopping a pod unmounts its volumes, which flushes the page cache. So the [sync rule](../AGENTS.md#always-sync-the-filesystem-before-kasten-snapshots-the-pvc) is met by the scale-down itself | Confirm the pods are gone, not only terminating, before the snapshot |
| **Which workloads to stop** | IBM's rule matches names with `icp4adeploy`. Foundational Services (IAM, Zen) do not match, and are left running | Does Zen write anything a FileNet restore depends on? |
| **Operators** | Must be scaled down first, or they scale the workloads back up | Which operators exist in 26.0.0 — the names come from the 25.0.0 page |
| **PVCs IBM excludes** | The 25.0.0 page lists PVCs not to back up (for example `user-home-pvc`, some Elasticsearch PVCs) | Which of these exist in a Starter deployment, and how to exclude them in the policy |

### 4.4 When content is in S3

A FileNet advanced storage area can write content to an S3 bucket. In that case the content is
**outside the cluster**: Kasten does not capture it, and the bucket must be protected separately
(versioning or object lock on the bucket). The offline approach still matters there: the database
snapshot and the bucket must describe the same moment, which means no document may be added or
deleted between the database snapshot and the bucket protection point.

This example covers content on PVCs only, which is what the Starter install is expected to create
*(to be measured)*. S3 storage devices are out of scope.

### 4.5 Patterns considered and ruled out

| Pattern | Why not |
|---|---|
| 1 — Fence and quiesce a replica | CPE has no read-only replica that can be fenced from the live system |
| 3–9 — Database dumps on a PVC | Dumping the databases while CPE runs does not solve consistency with the storage area files, and a dump loses the incrementality that PVC snapshots keep. They also add a restore procedure IBM does not document for CP4BA |
| 11 — Vendor operator data mover | CP4BA has no backup API in its operator. IBM's automated option is IBM Storage Fusion recipes, which is a different product |

---

## 5. Restore — to be designed

To be written after Step 3. What is already known:

- IBM restores **all** pieces from the same backup. A restore of the databases without the storage
  area, or the reverse, is not supported.
- The operators must not reconcile half-restored resources. The restore order (operators last, as
  in the backup) is to be established in Step 3.
- After a restore, the check in
  [mini-tutorial-filenet.md §4.4](mini-tutorial-filenet.md#44-the-restore-check) is run: every test
  document **opens**, its content matches the recorded checksum, and the version history is intact.
  Listing documents is not enough.
- Foundational Services have their own backup procedure ([CP4BA DR](https://ibm.com/docs/SSYHZ8_24.0.0/com.ibm.dba.managing/topics/tsk_overview_dr.html)).
  Whether a same-namespace restore needs it is to be measured.

---

## 6. Versions

Planned versions — see [install-cp4ba.md §1](install-cp4ba.md#1-what-is-being-installed-and-why-this-version):

| Component | Version |
|---|---|
| OpenShift / Kubernetes | 4.21.30 / 1.34.9 |
| Kasten | 9.0.8 (OLM operator) |
| CP4BA | 26.0.0-IF003, Starter |
| ODM | 9.6.0.0-IF005 |
| FileNet Content Manager | *(to be measured)* |
| PostgreSQL | *(to be measured)* |

---

## 7. Blueprint actions — planned

No blueprint exists yet. This table lists the actions planned in §4.2; it becomes the
authoritative table, kept in sync with `blueprint.yaml`, once the YAML is written.

| Action | Planned behaviour |
|---|---|
| `backupPrehook` | Scale down the CP4BA operators, then the `icp4adeploy` workloads; wait until their pods are gone |
| `backupPosthook` | Scale up the `icp4adeploy` workloads, then the operators |

---

## 8. Initial prompt

> Create a blueprint to protect a CP4BA install that deploys FileNet and ODM.
