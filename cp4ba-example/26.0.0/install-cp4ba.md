# CP4BA install note — Starter deployment, Decisions (ODM) and Content (FileNet)

IBM Cloud Pak for Business Automation **26.0.0-IF003**, *Starter* deployment type, two capabilities
— **Operational Decision Manager (ODM)** and **FileNet Content Manager** — on OpenShift 4.21.

> ## ⚠️ This is not installation documentation — use IBM's
>
> **To install CP4BA, follow IBM's official documentation, not this file.**
> CP4BA 26.0.0 docs → *Installing* → *Installing a starter deployment*:
> <https://www.ibm.com/docs/en/cloud-paks/cp-biz-automation/26.0.0>
>
> IBM owns the supported procedure, keeps it current, and covers the prerequisites, sizing and
> platform variations that this note does not. The commands below are **a record of one install on
> one cluster** — the version is pinned to 26.0.0-IF003, and the storage classes, namespaces and
> cluster are specific to our test environment.
>
> **A Starter deployment is for demonstration and evaluation only.** IBM does not support it in
> production and it **cannot be upgraded** — to change version you delete it and install again.
> That is acceptable here: the goal is a realistic ODM and FileNet to design a backup against, not a
> supported service.
>
> ### Status of this file
>
> **§4 is a record of the install run on the lab cluster on 2026-10-20** (both scripts, the prompts
> as asked, the CR as applied). The other sections were written beforehand from IBM's documentation
> and the 26.0.0-IF003 scripts; those marked *(to be measured)* are filled in once the deployment
> has finished reconciling. Until then, treat them as a plan, not as an observation.

---

## 1. What is being installed, and why this version

| | |
|---|---|
| Product | IBM Cloud Pak for Business Automation (CP4BA) |
| Version | **26.0.0-IF003** (interim fix 3, released 2026-09-30) |
| CASE package | `ibm-cp-automation` 26.0.3 |
| Operator channel | `v26.0` |
| Operator image | `icr.io/cpopen/icp4a-operator:26.0.0-IF003` |
| ODM level | based on ODM 9.6.0.0-IF005 |
| FileNet level | *(to be measured: Content Platform Engine and Navigator image tags)* |
| Foundational Services | 4.19.3 |
| Deployment type | `Starter` |
| Patterns | `decisions` → Decision Center, Decision Server (console + runtime), Decision Runner |
| | `content` → Content Platform Engine (CPE), Business Automation Navigator, Content Services GraphQL |
| Database | PostgreSQL (IBM's CloudNativePG build), deployed by the Starter install |
| Users | OpenLDAP, deployed by the Starter install |
| Custom resource | `icp4a.ibm.com/v1` kind `ICP4ACluster`, name `icp4adeploy` |

**Why 26.0.0-IF003 and nothing older:**

| Candidate | Problem |
|---|---|
| 26.0.0 GA | Starter does not work on GA: it needs IBM's PostgreSQL operator, which was not ready at GA. IBM fixed it in IF001 ([technote](https://www.ibm.com/support/pages/node/7256438)) |
| 25.0.1-IF001 | Ships Foundational Services 4.15. Foundational Services supports OpenShift 4.21 only from 4.17 ([support matrix](https://www.ibm.com/docs/en/SSRV9V_4.19.x/installer/support_os.html)), so 25.0.1 is very likely not supported on our cluster |
| **26.0.0-IF003** | Newest level; Foundational Services 4.19.3 supports OpenShift 4.21 and 4.22 |

> **Confirmed in IBM's compatibility report** (report version 26.0.0, last modified 2026-10-01,
> *Containers* tab): the CP4BA 26.0.0 containers support OpenShift Container Platform **4.18 to
> 4.22**, "and future fix packs" of each. Check it again for your own OpenShift version:
> <https://www.ibm.com/software/reports/compatibility/clarity/index.html?name=IBM%20Cloud%20Pak%20for%20Business%20Automation>

Release notes for this level: <https://www.ibm.com/support/pages/node/7286023>.

**Why two capabilities, and why these two.** CP4BA bundles several older IBM products. The two
installed here are the ones with the largest installed base before containers, and they are very
different to back up:

| Capability | Earlier product | Where its state lives | Why it matters for backup |
|---|---|---|---|
| Decisions | ODM (ILOG JRules) | Databases only (expected) | The simple case: a database backup |
| Content | FileNet P8 | Databases **and** files on PVCs | The hard case: the database rows and the files must be captured at a consistent moment |

FileNet also sits under Business Automation Workflow, which keeps its documents and cases in FileNet
object stores. A FileNet backup design is therefore reusable for Workflow later.

Both are selected **at install time**. IBM documents adding capabilities to an existing deployment
by editing `sc_deployment_patterns`, but a Starter deployment creates its databases and LDAP users
during the install, and adding a pattern afterwards was not verified. Choosing both up front avoids
depending on it.

---

## 2. Starting state of the cluster

| Item | Value | How checked |
|---|---|---|
| OpenShift | 4.21.30, channel `stable-4.21` | `kubectl get clusterversion` |
| Kubernetes | 1.34.9 | `kubectl version` |
| Nodes | 3 × amd64 bare metal, 24–32 cores, ~380 GiB RAM each | `kubectl get nodes` |
| RWX storage class | `ocs-storagecluster-cephfs` (ODF), snapshot class `ocs-storagecluster-cephfsplugin-snapclass` | `kubectl get sc`, `kubectl get volumesnapshotclass` |
| RWO storage class | `ocs-storagecluster-ceph-rbd` (ODF, default), snapshot class `ocs-storagecluster-rbdplugin-snapclass` | same |
| cert-manager | **Red Hat** cert-manager operator (namespaces `cert-manager`, `cert-manager-operator`) | `kubectl get ns` |
| IBM License Service | present in `ibm-licensing` (left over from CP4D) | `kubectl get ns ibm-licensing` |
| Kasten | 9.0.8 (OLM operator) | `kubectl get csv -n kasten-io` |
| Free capacity (CP4D still installed) | CPU requests: ~59 of 78.5 cores already requested, so **~19 cores free**. Memory requests: ~200 of ~1130 GiB | `kubectl describe nodes` → *Allocated resources*, 2026-10-20 |
| IBM entitlement | "IBM Software Access 1 Year" — covers all container software, including CP4BA. Verified: the key pulls `cp/cp4a/icp4a-operator` and `cp/cp4a/odm/*` from `cp.icr.io` | registry API, 2026-10-20 |

### 2.1 CP4D is removed first

The cluster previously ran IBM Software Hub (CP4D) 5.4.0 Patch 7, used by
[cp4d-example](../../cp4d-example/) and [datastage-example](../../datastage-example/). It is removed
before CP4BA is installed. Remove it with IBM's procedure (*IBM Software Hub 5.4.x docs →
Uninstalling*), and keep in mind:

- **Keep the cluster-wide singletons**: Red Hat cert-manager, the IBM License Service in
  `ibm-licensing`, and the IBM entitlement key in the global pull secret. CP4BA uses all three.
- **Delete the Kasten objects the CP4D tests left behind**, because they outlive CP4D: the test
  blueprint `cp4d-projects-backup-bp` in `kasten-io`, and the test RBAC in `cp4d-projects-backup`.

CPU **requests** are the constraint, not memory: a pod that cannot reserve its requested CPU stays
`Pending`. *(To be measured: the CPU requests of the CP4BA Starter deployment with both
capabilities, to decide whether the install can start before CP4D is fully removed.)*

Why remove it rather than install side by side: a Starter deployment must go into a namespace with
**no existing Foundational Services**, and both Cloud Paks would share the license service and the
cert-manager. Running only one Cloud Pak keeps the backup scope of this example readable.

---

## 3. Prerequisites on the workstation

The install is driven by two interactive shell scripts from IBM's `cert-kubernetes` repository.
They run on your workstation (IBM calls it the *bastion host*), not in the cluster.

| Tool | Requirement | Note |
|---|---|---|
| `oc` | match the cluster: **4.21** | An old client (4.13 here) is the first thing to update |
| `kubectl` | any recent | |
| Java | 17 or newer | Needed by the deployment script; pass `--java-path` if it is not on `PATH` |
| `timeout` | GNU coreutils | On macOS: `brew install coreutils`. If it is missing, the script tries `sudo ln -s /usr/local/bin/gtimeout …`, which is the wrong path on Apple silicon |
| `yq` | bundled | On macOS the scripts use the bundled `helper/yq/yq_darwin_amd64`, an **Intel** binary: on an Apple silicon Mac it needs Rosetta 2 (`softwareupdate --install-rosetta`) |
| Entitlement key | from <https://myibm.ibm.com/products-services/containerlibrary> | The admin script asks for it |

```bash
git clone -b 26.0.0-IF003 https://github.com/icp4a/cert-kubernetes.git
cd cert-kubernetes/scripts
oc login --server=https://api.<your-cluster>:6443 ...
```

---

## 4. The install — two scripts

Values used on our cluster. **Substitute your own** — the storage classes are cluster-specific:

```bash
export CP4BA_NS=cp4ba-starter                         # new namespace, no Foundational Services in it
export STG_CLASS_FILE=ocs-storagecluster-cephfs       # RWX — cluster-specific
export STG_CLASS_BLOCK=ocs-storagecluster-ceph-rbd    # RWO — cluster-specific
```

> **Storage class**: `ocs-storagecluster-cephfs` and `ocs-storagecluster-ceph-rbd` are the
> OpenShift Data Foundation classes of our test environment. Replace them with storage classes that
> support CSI snapshots on your cluster. Each class must have a matching `VolumeSnapshotClass`
> registered with Kasten.

### 4.1 Cluster admin setup — operators

```bash
./cp4a-clusteradmin-setup.sh
```

Answers for this install:

| Prompt | Answer |
|---|---|
| Installation mode | Online |
| Platform | OpenShift Container Platform |
| Deployment type | **Starter** |
| Private catalog | Yes (the default) — the catalog sources go into `${CP4BA_NS}`, not `openshift-marketplace` |
| Namespace | `cp4ba-starter` |
| Existing username in your cluster | the **number** of a user in the displayed list, not the name (typing the name gives `invalid option`). This user becomes administrator of the project. The script suggests a non-admin user; we chose our own cluster-admin account, which is acceptable for a lab |
| Do you have an Entitlement Registry key? | Yes (the default) |
| Entitlement key | the key from §2 — nothing is echoed while you paste it |

What it does:

- Creates the namespace, the `ibm-entitlement-key` pull secret, and the catalog sources
  (`ibm-cp4a-operator-catalog` plus the Foundational Services, IAM, Zen and PostgreSQL catalogs).
- Detects the existing **Red Hat** cert-manager and skips IBM's: the script logs *"Cluster has a
  non ibm-cert-manager-operator already installed, skipping"*. IBM recommends Red Hat's cert-manager
  anyway; its own has been deprecated since Foundational Services 4.6.
- Runs `setup_singleton.sh --enable-licensing` for the License Service. Observed: it **reused**
  the `ibm-licensing` instance left by CP4D and upgraded it in place (`ibm-licensing-operator`
  4.2.24 → 4.2.25). It also creates a Role and RoleBinding so `ibm-cp4a-content-operator` can read
  secrets in `ibm-licensing`.
- Installs **every** CP4BA operator, whatever capability you deploy later: ODM, Workflow, Content,
  ADS, Document Processing, Process Federation, Insights Engine, Workflow Process Service, plus
  Foundational Services 4.19.3, ODLM and Redis. The capability is chosen only in §4.2. Observed:
  12 catalog sources and 13 `26.0.3` CSVs in `cp4ba-starter`, all `Succeeded`.
- **Ends by printing the storage classes and returning to the prompt.** That is the normal end of
  the script, not a failure — it lists them so you can choose in §4.2.
- In Starter, **operators and operands go into the same namespace**. Production deployments
  separate them; Starter does not.

Check the operators before going on:

```bash
kubectl get csv -n ${CP4BA_NS}
kubectl get pods -n ${CP4BA_NS}
```

### 4.2 Deployment — the custom resource

```bash
./cp4a-deployment.sh -n ${CP4BA_NS}
```

The prompts as the 26.0.0-IF003 script actually asks them:

| Prompt | Answer |
|---|---|
| Additional BAW license — *Press Enter* | Enter |
| Do you accept the IBM Cloud Pak for Business Automation license? | `Yes` |
| Data collection notice — *Press Enter* | Enter |
| Did you deploy Content CR (CRD: `contents.icp4a.ibm.com`) in current cluster? | `No` (the default). The `Content` CR is a separate way to deploy FileNet alone; we use `ICP4ACluster` |
| Select the capability to install | `1` (*Content Cortex Essentials* — this is FileNet), then `2` (*Operational Decision Manager*), then Enter on an empty line |
| Content Cortex Essentials: optional components | Enter — **none** (see the note below) |
| Operational Decision Manager: optional components | Enter — **none**. The only option, `1`, is Business Automation Insights |
| File storage classname (RWX) | `ocs-storagecluster-cephfs` |
| Block storage classname (RWO) | `ocs-storagecluster-ceph-rbd` |
| Summary — enter `Yes` to proceed | `Yes`, **only after checking it lists both capabilities and "Optional components: None"** |

Three traps in these menus:

- **The capability and component menus are toggles.** Typing a number that is already selected
  unselects it. Check the selection markers before pressing Enter. In our run, ODM silently dropped
  out once, and Business Automation Insights was added once by typing `1` in the ODM list.
- **Answering `No` at the summary does not restart the questions**: it asks which item (1–4) to
  change, and the capability menu then opens again.
- The red *"You must select at least one of ODM components"* is misleading: Decision Center, Rule
  Execution Server and Decision Runner are always installed. Press Enter.

The menu also states that **Business Automation Navigator is installed automatically** as part of the
foundation platform, whatever capability you choose. And for Starter the script forces
`generate_sample_network_policies` and `enable_instana_monitoring` to `true`. The network
policies matter for Kasten: they may block the pods Kanister starts in the namespace.

If the script stops early with *"Your input value for CP4BA operands … is NOT equal to the value of
services_namespace"*, look just above that line: when it follows `Unable to connect to the server:
net/http: TLS handshake timeout`, the comparison failed because the read of the
`ibm-cp4ba-common-config` ConfigMap timed out (a VPN drop in our case). Nothing has been created at
that point; rerun the script.

**Optional components.** For Content the script offers extra components (for example Content
Search Services for full-text search, CMIS, Task Manager). They are left out to keep the first
install small. Content Search Services is the first candidate to add later: it keeps a full-text
index on a PVC, which is one more store that must stay consistent with the object store database.

The script writes the final CR and applies it (`icp4acluster.icp4a.ibm.com/icp4adeploy created`).
It works in a **temporary directory** (`/var/folders/…/T/tmp.XXXX/cert-kubernetes/scripts/generated-cr/project/<namespace>/ibm_cp4a_cr_final.yaml`
on macOS), so the file may not survive a reboot: read the CR back from the cluster instead
(`kubectl get icp4acluster icp4adeploy -n ${CP4BA_NS} -o yaml`).

The part that matters, as applied on our cluster (comments removed):

```yaml
apiVersion: icp4a.ibm.com/v1
kind: ICP4ACluster
metadata:
  name: icp4adeploy
  labels:
    cp4ba.ibm.com/backup-type: mandatory    # IBM's own marker for what a backup must include
spec:
  appVersion: 26.0.0
  ibm_license: accept
  shared_configuration:
    sc_deployment_patterns: foundation,content,decisions   # "foundation" is added by the script
    sc_optional_components: decisionCenter,decisionRunner,decisionServerRuntime
    sc_deployment_type: Starter
    sc_deployment_platform: OCP
    sc_deployment_license: non-production
    sc_deployment_fncm_license: non-production
    sc_generate_sample_network_policies: true
    sc_enable_instana_metric_collection: true
    image_pull_secrets: [ibm-entitlement-key]
    storage_configuration:
      sc_dynamic_storage_classname: ocs-storagecluster-cephfs
      sc_block_storage_classname: ocs-storagecluster-ceph-rbd
  odm_configuration:
    decisionCenter: {enabled: true}
    decisionRunner: {enabled: true}
    decisionServerRuntime: {enabled: true}
    image: {pullSecrets: ibm-entitlement-key}
```

There is no `ecm_configuration` or `navigator_configuration` section in the Starter CR: in Starter
the operator derives the FileNet and Navigator configuration from `sc_deployment_patterns` alone.

**Commit nothing from `generated-cr/`** — it is specific to the cluster, and later versions of the
script may add generated secrets to it.

Watch the reconciliation:

```bash
kubectl get icp4acluster icp4adeploy -n ${CP4BA_NS} -o jsonpath='{.status.conditions}' | jq
kubectl get pods -n ${CP4BA_NS} -w
```

*(To be measured: wall-clock time of each step, and how long until Decision Center answers after
the CR reports ready.)*

---

## 5. What the install creates *(to be measured)*

These sections are filled in from the cluster after the install. They are the reason this file
exists: what the blueprint must protect follows from what the install actually creates.

### 5.1 Pods and footprint

Expected, from IBM's documentation:

- ODM: Decision Center, Decision Server Console, Decision Server Runtime, Decision Runner.
- Content: Content Platform Engine (CPE), Business Automation Navigator, Content Services GraphQL.
- The PostgreSQL cluster(s) holding the ODM and FileNet databases.
- OpenLDAP (Starter only), with `cp4admin` and `user1`…`user10`.
- Foundational Services at size `starterset`: IAM, the Platform UI (Zen), and its own metadata
  database.

### 5.2 PVCs

Expected: ODM mounts no PVC for its data. CPE and Navigator do — configuration, logs, and above all
the **file storage area**, where FileNet writes the document content. The question to answer is
whether the Starter object store keeps its content on a PVC or inside the database.

```bash
kubectl get pvc -n ${CP4BA_NS} -o custom-columns=NAME:.metadata.name,SIZE:.spec.resources.requests.storage,CLASS:.spec.storageClassName,MODE:.spec.accessModes
```

**OpenLDAP has no PVC.** Measured: the `icp4adeploy-openldap-deploy` pod keeps its database
(`/var/lib/ldap`) and its configuration (`/etc/openldap/slapd.d`) in `emptyDir` volumes. At every
start it rebuilds the directory from the LDIF in the Secret `icp4adeploy-openldap-customldif`,
which the operator creates from the `ICP4ACluster` CR. So:

- The backup protects the users through that Secret and the CR, which Kasten captures with the
  other Kubernetes objects. No volume is needed.
- A change made in LDAP itself (a new user, a changed password) is lost at the next pod restart,
  with or without a restore. Starter's OpenLDAP is a demo directory, not a store of record.
- To check: whether FileNet records users by DN or by a generated attribute such as `entryUUID`.
  A generated attribute changes when the directory is rebuilt, so a restored document's access
  control list would no longer match its users. The restore check in
  [mini-tutorial-filenet.md §4.4](mini-tutorial-filenet.md#44-the-restore-check) shows it if it
  happens.

In production the directory is the customer's own LDAP, outside the cluster, and is not part of
this backup.

### 5.3 Databases and what is in them

Expected for Content: the **GCD** (Global Configuration Database — FileNet's directory of the whole
domain: object stores, storage areas, LDAP connection), one database **per object store** (document
metadata, folders, security, versions), and the **Navigator** configuration database.

```bash
kubectl get clusters.pg.ibm.com -n ${CP4BA_NS}   # IBM's own PostgreSQL operator (group pg.ibm.com)
```

### 5.4 Objects IBM marks for backup

The CR carries `cp4ba.ibm.com/backup-type: mandatory`. List everything carrying that label — it
is IBM's own statement of what a backup cannot skip:

```bash
kubectl get all,secret,configmap,pvc -n ${CP4BA_NS} -l cp4ba.ibm.com/backup-type -o name
```

---

## 6. Access

All CP4BA URLs go through the Platform UI route `cpd-<namespace>.<apps-domain>`.

```bash
# URLs of the ODM and FileNet applications
kubectl get cm icp4adeploy-cp4ba-access-info -n ${CP4BA_NS} -o jsonpath='{.data}' | jq

# Platform administrator (cpadmin)
kubectl get secret platform-auth-idp-credentials -n ${CP4BA_NS} -o jsonpath='{.data.admin_password}' | base64 -d; echo

# LDAP users (cp4admin, user1..user10) and their passwords
kubectl get secret icp4adeploy-openldap-customldif -n ${CP4BA_NS} -o jsonpath='{.data.ldap_user\.ldif}' | base64 -d
```

| Application | Path under the `cpd-…` route |
|---|---|
| Decision Center | `/odm/decisioncenter` |
| Decision Server Console | `/odm/res` |
| Decision Server Runtime | `/odm/DecisionService` |
| Decision Runner | `/odm/DecisionRunner` |
| Administration Console for Content Platform Engine (ACCE) | `/cpe/acce` *(to be measured)* |
| Navigator | `/icn/navigator` *(to be measured)* |
| Content Services GraphQL | `/content-services-graphql` *(to be measured)* |

*(To be measured: the exact keys of the access-info config map and of the LDIF secret, and the
FileNet paths above, which were not verifiable before the install.)*

The ODM roles are granted to users in the Platform UI. The first steps are in
[mini-tutorial-odm.md](mini-tutorial-odm.md#3-first-login-and-roles). The FileNet equivalent is
[mini-tutorial-filenet.md](mini-tutorial-filenet.md#3-first-login).

---

## 7. Uninstall

```bash
cd cert-kubernetes/scripts
./cp4a-clean-up.sh -n ${CP4BA_NS}
```

From reading the 26.0.0-IF003 script: it removes the CP4BA and Foundational Services custom
resources, the CP4BA namespace and `cs-control`. It does **not** delete the CRDs (off by default),
nor the `ibm-licensing` and cert-manager namespaces. `cp4a-clean-up-for-redeploy.sh` is the lighter
variant for reinstalling into the same namespace.

Then check that nothing is left holding storage:

```bash
kubectl get pvc -A | grep -i ${CP4BA_NS}
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=${CP4BA_NS}
```
