# CP4D artifact-level backup with Kasten

> **CP4D 5.4.** This is the 5.4 version of the blueprint. Other versions are listed in [../README.md](../README.md).

Back up **Cloud Pak for Data analytics projects** (notebooks, jobs, data assets, connections)
at the **logical / artifact level** using `cpdctl` project export/import, with **Kasten as the
data mover**. Each CP4D project is exported into its **own PVC**; Kasten snapshots those PVCs.
Restore is **granular and multi-tenant**: pick the project(s) you want, restore only those PVCs
into a new namespace, and the blueprint's restore hook recreates those projects in CP4D. The
whole restore is done from the Kasten UI.

> This is a **complement** to CP4D's platform-level backup (`cpdbr`), not a replacement. `cpdbr`
> covers whole-tenant infrastructure DR; this blueprint covers the lane it does not enter —
> per-project restore, portability between clusters, and self-service recovery. It needs the CP4D
> platform to be up; it does not protect the control plane.
>
> The reasoning behind this design — why the API is the only path to a notebook, what the export
> bundle actually contains, and the credential/data boundaries — is in **[DESIGN.md](../DESIGN.md)**.
> Read that for the "why"; this README is the "how".

---

## Architecture

```
                          cp4d namespace                      cp4d-projects-backup namespace
                     ┌──────────────────────-┐            ┌──────────────────────────────────────┐
   Kasten policy     │  CP4D / Watson Studio │            │  1 PVC per project (permanent, GUID- │
   (backup ns) ─────▶│  projects + cpdctl API│            │  keyed): cp4d-<name>-<guid>          │
        │            │                       │            │   ├─ cp4d-my-new-project-21a0ff72    │
        │ preHook    │  cred secret          │            │   ├─ cp4d-sales-3f9c1a2b             │
        ▼            │  cp4d-backup-cpdctl-  │            │   └─ ...                             │
  backupPrehook      │  creds (OUTSIDE the   │            └──────────────────────────────────────┘
  (action hook)      │  backed-up namespace) │                         ▲   Kasten snapshots these PVCs
        │            └──────────┬────────────┘                         │
        │ runs orchestrate.sh              reads cred secret           │ one export pod per project
        │ (KubeTask in kasten-io,          cross-namespace (RBAC)      │ (keeper SA), writes the
        │  cluster-wide Kasten SA)  ───────────────────────────────────┘ cpdctl export zip to /backup/current
        ▼
  enumerate projects → ensure PVC per project → launch export pods → wait → prune → cleanup pods
```

**Pattern:** hybrid of *Pattern 4 (permanent keeper PVC)* + the *action-hook mechanism*. There is
no `Project`/`Notebook` CR to bind to, so this is an **action blueprint** (a BackupAction preHook),
not a workload blueprint. The preHook runs **before Kasten's PVC discovery**, so every per-project
PVC it creates is included in the restore point. One PVC per project is the whole point: **PVC =
unit of granularity = unit of multi-tenant restore**.

**Key decisions** (see [DESIGN.md § 3](../DESIGN.md#3-how-it-maps-onto-this-projects-blueprint-patterns) for the reasoning):
- **Permanent, GUID-keyed PVCs** (`cp4d-<sanitized-name>-<short-guid>`), created-if-missing and
  refreshed every run, pruned when a project is deleted. Permanence lets CSI block-level
  incremental dedup work across backups.
- **Store the export zip as is** (`/backup/current/export.zip`), and give it back to CP4D unchanged
  at restore (`cpdctl asset import start --import-file`). Earlier versions extracted the zip so that
  a small change rewrote only one file. We stopped: the zip is produced by the CP4D export service,
  its content is IBM's format, and CP4D 5.4 puts **the same entry twice** in one zip: for every
  Watson Pipelines pipeline in the project, `assets/.METADATA/orchestration_flow.<id>.json` appears
  two times, with identical bytes. `unzip` then stops on an interactive `replace ...?` prompt, reads
  end-of-file because the pod has no terminal, and exits with code 1, so the export fails.
  `cpdctl asset import start --import-file` accepts the same zip without error (validated
  2026-11-18, see [Test it end-to-end](#test-it-end-to-end)). The cost: a zip is compressed, so any
  change in the project changes most of its bytes, and each snapshot and each export to object
  storage holds about the full size of the zip. This has not been measured.
- **The scripts are not in the image.** They are in [scripts/](scripts/) and reach the pods through
  the ConfigMap `cp4d-backup-scripts` in `kasten-io`. A script fix during troubleshooting is one
  `kubectl apply`, with no image rebuild and no new image tag. The image only provides the tools
  (`cpdctl`, `kubectl`, `jq`).
- **Restore is a Kasten restore hook, run from the UI.** The user restores the PVCs of the projects
  they want into a new namespace and selects the action `restoreProjects` as the hook to run after
  the restore. The hook imports each restored PVC into CP4D from a pod. Nobody needs `cpdctl` or a
  terminal. An existing project is never deleted: if its name is taken, the backup is restored
  beside it as `<name>-restored-<date>`.
- **Credentials never fan out**: the CP4D API key lives in a Secret in the CP4D instance namespace (`cpd` by default; NOT
  the backed-up namespace), read cross-namespace by the keeper SA, and rendered into an ephemeral
  `emptyDir`/tmpfs cpdctl config — so it is never captured in any restore point.

---

## Versions

Detected on the cluster where this blueprint was last tested (2026-11-19, restore hook): a new
OpenShift cluster on Azure, installed with CP4D 5.4.0 Patch 7, Watson Pipelines and DataStage. The
2026-11-18 validation ran on a cluster with the same versions. The previous
validation (2026-10-16) ran on bare metal with Kasten 9.0.7 (OLM) and ODF
`ocs-storagecluster-ceph-rbd`, after an upgrade from CP4D 5.2.2. It was first developed on OpenShift
4.18.6 / Kubernetes 1.31.6 / Kasten 8.5.12 / CP4D 5.2.x with `cpdctl` 1.8.244.

| Component | Version |
|---|---|
| Kubernetes | 1.34.9 |
| OpenShift | 4.21.30 (installer-provisioned, Azure) |
| Kasten | 9.0.11 (Helm chart `k10-9.0.11`) |
| Storage | Azure Disk CSI `managed-csi` (snapshot class `csi-azuredisk-vsc`) |
| Cloud Pak for Data / IBM Software Hub | 5.4.0 Patch 7 (`ibmcpd-cr` 5.10.7, Watson Studio `ws` 13.0.4, Common Core Services 13.0.6, Watson Pipelines 5.4.7, DataStage Enterprise 5.4.7) |
| cpdctl | 1.10.34 |
| kubectl (in the keeper image) | 1.34.9 |
| Keeper image | `ghcr.io/kastenhq/blueprint-ai/cp4d-backup:5.4-r3` (built from [images/cp4d-backup/](images/cp4d-backup/); tools only. `-r2` and earlier had the scripts inside the image; `-r1` and earlier did not pass `CRED_NS` / `CRED_SECRET` to the export pods, so they read the credential Secret from `cpd` whatever the blueprint set) |

Detect your Kasten version:

```bash
helm ls -n kasten-io                 # Helm
# or (OpenShift OLM): kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'
```

---

## Repository layout

| Path | What |
|---|---|
| [blueprint.yaml](blueprint.yaml) | The action blueprint (`backupPrehook`, `restoreProjects`) |
| [images/cp4d-backup/Dockerfile](images/cp4d-backup/Dockerfile) | Keeper image, tools only (cpdctl + kubectl + jq) |
| [scripts/](scripts/) | The scripts, loaded into the `cp4d-backup-scripts` ConfigMap ([Prerequisite 1b](#1b-load-the-scripts-configmap)) |
| [scripts/orchestrate.sh](scripts/orchestrate.sh) | Backup orchestrator (enumerate → export pods → prune) |
| [scripts/export-project.sh](scripts/export-project.sh) | Per-project export → store the zip atomically on the PVC |
| [scripts/restore-projects.sh](scripts/restore-projects.sh) | Restore hook orchestrator (refuse the backup namespace → one restore pod per restored PVC) |
| [scripts/restore-project.sh](scripts/restore-project.sh) | Per-PVC restore: create the project (own name, or `-restored-<date>` if taken), then import |
| [scripts/import-project.sh](scripts/import-project.sh) | Import the zip from a restored PVC into an existing project |
| [scripts/cpdctl-login.sh](scripts/cpdctl-login.sh) | Headless cpdctl auth (env creds or cross-ns secret) |
| [DESIGN.md](../DESIGN.md) | The rationale: why the API path, pattern mapping, what export captures |
| [DESIGN.pptx](../DESIGN.pptx) | The same rationale as a deck — one slide per DESIGN.md section, full section text in the speaker notes (open with PowerPoint; Keynote refuses the import) |
| [tools/build-design-deck.py](../tools/build-design-deck.py) | Regenerates `DESIGN.pptx` from `DESIGN.md` (`pip install python-pptx`) |

---

## Prerequisites

> **CP4D instance namespace — `CPD_NS`.** The CP4D instance (the namespace that holds the `cpd`
> route, the platform Secrets and the `ZenService` CR) is called `cpd` on our test cluster. Many
> installs use another name, for example `ibm-cpd` or `cpd-instance`. Every command in this README
> uses the shell variable `CPD_NS` instead of a fixed name. Set it once, in the shell you use for
> all the steps below:
>
> ```bash
> oc get zenservice -A                # the NAMESPACE column is your CP4D instance namespace
> export CPD_NS=cpd                   # replace cpd with that namespace, for example ibm-cpd
> ```
>
> The blueprint cannot read your shell, so it receives the value explicitly:
>
> | Where | How it gets `CPD_NS` |
> |---|---|
> | Commands in this README | `-n "${CPD_NS}"` |
> | [blueprint.yaml](blueprint.yaml) (`export CRED_NS="cpd"`, once in each action) | [Deploy](#deploy) step 1 replaces `cpd` with `${CPD_NS}` when it applies the blueprint |
>
> If you forget, the backup or the restore hook looks for the credential Secret in `cpd` and fails
> with `secrets "cp4d-backup-cpdctl-creds" not found`.
>
> The route is named `cpd` by default whatever the namespace is called. Only if your install uses
> a custom route name, replace `cpd` in `oc get route cpd`.

### 1. Build the keeper image

The blueprint and restore both use one self-contained image (Dockerfile committed at
[images/cp4d-backup/](images/cp4d-backup/)): Debian slim + `cpdctl` + `kubectl` + `jq` (and
`unzip`, only to inspect a bundle by hand). The scripts are **not** in the image (see 1b). Runs as an arbitrary (OpenShift-assigned) UID; the
cpdctl config is rendered to a tmpfs path at runtime and never persisted.

```bash
cd images/cp4d-backup
docker buildx build --platform linux/amd64 -t <your-registry>/cp4d-backup:5.4-r3 --push .
```

> Use an **immutable tag** (or `imagePullPolicy: Always`, which the pod specs already set). A reused
> mutable tag can leave stale layers cached on some nodes → `executable ... not found` errors.
> Update the image reference in [blueprint.yaml](blueprint.yaml): it appears twice in each action
> (`image:` and `export IMAGE=`).

### 1b. Load the scripts ConfigMap

Both blueprint actions run the scripts from the ConfigMap `cp4d-backup-scripts` in
`kasten-io`. Create it from the [scripts/](scripts/) folder:

```bash
kubectl create configmap cp4d-backup-scripts -n kasten-io --from-file=scripts/ \
  --dry-run=client -o yaml | kubectl apply -f -
```

Run the same command again after **any** change to a script. The next backup or restore uses the
new version; there is no image to rebuild. A pod cannot mount a ConfigMap from another namespace,
so each backup copies the ConfigMap into `cp4d-projects-backup`, and each restore copies it into
the restore namespace. Do not edit those copies; they are overwritten.

The first line of the `backupPrehook` and `restoreProjects` logs names the version that ran:

```
scripts from ConfigMap kasten-io/cp4d-backup-scripts: sha256 3f1c0a9e5b27
```

Compare it with your local folder: `cat scripts/*.sh | sha256sum | cut -c1-12`.

### 2. CP4D credentials — the `cpadmin` API key in the CP4D instance namespace

The keeper runs as the built-in administrator **`cpadmin`**. It authenticates headlessly with
`cpadmin`'s **platform API key**, which is stored in a Secret **in the CP4D instance namespace (`CPD_NS`)** — deliberately *outside*
the backed-up namespace, so it is never captured in a restore point.

> **Why `cpadmin` and not a dedicated service user.** On Software Hub 5.2 with the Identity
> Management Service (`iamIntegration: true`, the default on a new 5.2 install), CP4D does not store
> passwords. A login identity must already exist in an identity provider: the Identity Management
> default admin (`cpadmin`), LDAP, OpenShift OAuth, SAML or OIDC. `POST /usermgmt/v1/user` only
> gives an existing identity access to the platform. We tested it: a user created that way with a
> `password` field gets `authenticator: external`, and logging in with that password returns
> `Unauthorized`. On our test cluster the only providers were `cpadmin` and the shared OpenShift
> htpasswd list, so `cpadmin` is the only identity whose API key can be obtained by script.
>
> What this costs you:
>
> | Trade-off | Consequence |
> |---|---|
> | No separate identity | Backup activity cannot be audited or revoked apart from the administrator's. |
> | Visible to project owners | `cpadmin` appears as an editor in every project's collaborator list. |
> | Shared key | Whoever regenerates `cpadmin`'s API key (UI or API) breaks the backup until the Secret is updated. |
>
> If your cluster has LDAP or another provider where you can create a dedicated user, you can use
> it instead: set `SVC_USER` to that user and get its API key **as that user** (for a federated
> user, sign in to the web UI, then *Profile and settings → API key*). Every other step is the same.

> 🚨 **THE KEEPER IDENTITY MUST HOLD `manage_project`, AND IT WILL BACK UP EVERY PROJECT IN THE
> CLUSTER.** `cpadmin` does **not** have `manage_project` by default. Both halves matter. Measured
> on CP4D 5.2.x (2026-08-07, re-checked 2026-10-15):
>
> | Capability | What grants it |
> |---|---|
> | **See** a project (`cpdctl project list`) | the **`manage_project`** permission — cluster-wide, no membership needed. Without it a user sees only its own projects, and *the Administrator role is no exemption*: `cpadmin` removed from a project no longer saw it, and saw it again as soon as it held `manage_project`. |
> | **Export** a project (`asset export start`) | **editor-or-above membership on that specific project**. No platform permission substitutes — a role carrying `administrator` + `manage_project` + `monitor_project` still fails `SPACES0045E … missing required editor member role`. |
> | **Add a member** to a project | `manage_project` **also grants this** — a holder that is a member of nothing added *itself* as editor (`HTTP 200`), where `cpadmin` without it was refused `403 "not a member of the project"`. |
>
> These are two separate authorization systems: a **role** bundles *platform* permissions (what you
> may do to the cluster), while **project membership** is a per-project entry governing access to
> that project's *data*. That third row is what lets the blueprint bridge them —
> [orchestrate.sh](scripts/orchestrate.sh) enumerates every project and **enrols itself
> as editor on any it is not already a member of**, before exporting anything. A project nobody
> granted it access to is repaired rather than silently skipped, and if enrolment fails the whole
> backup fails loudly (before any data moves) rather than quietly omitting that project.
>
> **The consequence you must accept:** the keeper backs up **every analytics project in the
> cluster**, including ones created later, and grants itself editor on each to do so. Its API key
> is a cluster-wide data-access credential, so protect the Secret accordingly. If some projects must
> stay untouched, this blueprint is not currently able to exclude them.

**The keeper authenticates with the API key alone — never a password.**
[cpdctl-login.sh](scripts/cpdctl-login.sh) runs
`cpdctl config user set svc --username … --apikey-stdin` (the key is piped in, so it never
appears on a command line or in the process table), and the Secret holds only `url`, `username`,
`apikey` and `uid`. The `cpadmin` password is used once, on your workstation, to obtain the key; it
is never stored in the backup namespace.

`manage_project` is not in any of the eight built-in roles, so the procedure creates a custom role
(`Backup Reader`) for it. The permission catalog validates names strictly — `manage_projects`
(plural) is rejected with *"Request body contains permissions that are not known to the platform"*;
the accepted names are **`manage_project`** and **`monitor_project`**.

### Procedure (run once, as `cpadmin`)

All calls go to the `cpd` route. Nothing is typed by hand: the `cpadmin` credentials are read from
the platform's own Secret.

```bash
CPD_HOST=$(oc get route cpd -n "${CPD_NS}" -o jsonpath='{.spec.host}')
SVC_USER=$(oc get secret ibm-iam-bindinfo-platform-auth-idp-credentials -n "${CPD_NS}" -o jsonpath='{.data.admin_username}' | base64 -d)
SVC_PW=$(oc  get secret ibm-iam-bindinfo-platform-auth-idp-credentials -n "${CPD_NS}" -o jsonpath='{.data.admin_password}' | base64 -d)
echo "SVC_USER=${SVC_USER}"     # cpadmin

# 1. Exchange username + password for a bearer token.
#    Response: {"token":"<JWT>","message":"success","_messageCode_":"success"}
TOKEN=$(curl -sSk -X POST "https://${CPD_HOST}/icp4d-api/v1/authorize" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"${SVC_USER}\",\"password\":\"${SVC_PW}\"}" \
  | jq -r .token)

# Fail loudly instead of carrying an empty token into the next steps.
if [ -z "$TOKEN" ] || [ "$TOKEN" = null ]; then
  echo "auth failed. Re-run the curl WITHOUT '| jq -r .token' to see the real error."
  return 2>/dev/null || exit 1
fi

# 2. Create the role carrying cluster-wide project visibility (once per cluster).
#    Response: {"id":"<uuid>","message":"success"}.
#    If the role already exists: 409 "This role already exists." — harmless, step 3 finds it.
curl -sSk -X POST "https://${CPD_HOST}/usermgmt/v1/role" \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d '{"role_name":"Backup Reader","description":"Cluster-wide project visibility for the backup keeper",
       "permissions":["manage_project","sign_in_only"]}'

# 3. Retrieve the ROLE_ID by name. A custom role gets a generated UUID as its id
#    (built-in roles have readable ids such as zen_administrator_role); the name is under .doc.
ROLE_ID=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/roles" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq -r '.rows[] | select(.doc.role_name=="Backup Reader") | .id')
echo "ROLE_ID=${ROLE_ID}"     # for example 51d5f900-a966-4d82-9645-cbb2aa3c1d50

# 4. Add the role to cpadmin. PUT REPLACES the whole role list, so read the current
#    roles and append — sending only [ROLE_ID] would remove zen_administrator_role.
NEW_ROLES=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/user/${SVC_USER}" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq -c --arg r "$ROLE_ID" '(.user_roles + [$r]) | unique')
echo "NEW_ROLES=${NEW_ROLES}" # must still contain zen_administrator_role

# The body MUST repeat "username", even though it is already in the URL: without it
# the call returns 400 "Parameter (body) failed schema validation" and changes nothing.
curl -sSk -X PUT "https://${CPD_HOST}/usermgmt/v1/user/${SVC_USER}" \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d "{\"username\":\"${SVC_USER}\",\"user_roles\":${NEW_ROLES}}"
# Response: {"uid":"1000331001","_messageCode_":"success","message":"success"}

# 5. Get an API key and the numeric uid.
#    ⚠️ GET /usermgmt/v1/user/apiKey GENERATES A NEW KEY on every call and invalidates the
#    previous one (verified: the old key then returns "Unauthorized"). Anything else that uses
#    cpadmin's API key stops working. Run this step once, and update the Secret right after.
#    The uid is needed by the keeper to add itself as a project member: CP4D rejects a member
#    payload carrying only user_name ("Field state cannot be set to ACTIVE without specifying
#    a member id"), and there is no cpdctl "whoami".
APIKEY=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/user/apiKey" \
  -H "Authorization: Bearer ${TOKEN}" | jq -r .apiKey)
SVC_UID=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/user/${SVC_USER}" \
  -H "Authorization: Bearer ${TOKEN}" | jq -r .uid)

# 6. Verify: the role is assigned and manage_project is now in the permissions.
curl -sSk "https://${CPD_HOST}/usermgmt/v1/user/${SVC_USER}" \
  -H "Authorization: Bearer ${TOKEN}" \
  | jq -c '{user_roles, has_manage_project: (.permissions | index("manage_project") != null)}'
# Expected: {"user_roles":["<ROLE_ID>","zen_administrator_role"],"has_manage_project":true}

# 7. Store url + username + apikey + uid for the keeper to read cross-namespace.
#    create --dry-run | apply makes this re-runnable (it replaces an existing Secret).
kubectl create secret generic cp4d-backup-cpdctl-creds -n "${CPD_NS}" \
  --from-literal=url="https://${CPD_HOST}" \
  --from-literal=username="${SVC_USER}" \
  --from-literal=apikey="${APIKEY}" \
  --from-literal=uid="${SVC_UID}" \
  --dry-run=client -o yaml | kubectl apply -f -

unset SVC_PW TOKEN APIKEY
```

> **Web UI alternative for steps 2–4.** On Software Hub 5.2, user management is **not** in the
> *IBM Cloud Pak for Data* menu, even for `cpadmin`. It is in a separate console, the **IBM Software
> Hub Administration Console**. Its menu contains *Access control*, *Identity providers*,
> *Monitoring*, *Diagnostics* and so on. The old 4.x URL `/zen/#/userManagement` no longer exists
> and redirects to the home page.
>
> 1. Sign in as `cpadmin`.
> 2. Open the Administration Console. Either click the **Switch locations** icon in the toolbar and
>    choose the administration console, or open this URL directly:
>    `https://<CPD_HOST>/zen-admin/?deployment_target=icp4data#/usermgmt-ui`
> 3. **Access control → Roles → New role**: name `Backup Reader`, permission *Manage projects*.
> 4. **Access control → Users** → `cpadmin` → **Assign roles** → tick **Backup Reader** → save.
>
> We found this URL in the platform's own menu registry (`GET /zen-data/v1/extensions`, entry
> `dap-admin-users`, which applies only to the `core` context). It was not taken from the IBM docs.

Verify the Secret is usable before deploying anything else. This mirrors exactly what
[cpdctl-login.sh](scripts/cpdctl-login.sh) does inside the keeper — it reads the same
three keys (`url`, `username`, `apikey`) and runs the same two `cpdctl config` commands:

```bash
kubectl get secret cp4d-backup-cpdctl-creds -n "${CPD_NS}" -o jsonpath='{.data.apikey}' | base64 -d \
  | ./bin/cpdctl config user set svc --apikey-stdin \
      --username "$(kubectl get secret cp4d-backup-cpdctl-creds -n "${CPD_NS}" -o jsonpath='{.data.username}' | base64 -d)"
./bin/cpdctl config profile set svc-check \
  --url "$(kubectl get secret cp4d-backup-cpdctl-creds -n "${CPD_NS}" -o jsonpath='{.data.url}' | base64 -d)" --user svc
./bin/cpdctl config profile use svc-check
./bin/cpdctl project list          # must list EVERY project, including ones cpadmin is not a member of
```

> **Validated** on the test cluster (Software Hub 5.2) on 2026-10-15:
>
> | Check | Result |
> |---|---|
> | Step 2 on a new role name | `200 {"id":"<uuid>"}` |
> | Step 2 when the role exists | `409 "This role already exists."` |
> | Step 4 with `{"username", "user_roles"}` | `200`; `cpadmin` then has `manage_project` |
> | Step 4 without `username` in the body | `400 schema validation`; nothing changed |
> | Step 4 with only `[ROLE_ID]` | `200`, and every other role was **removed** — `PUT` replaces the list |
> | Project created, then `cpadmin` removed from its members | `cpadmin` (administrator, no `manage_project`) **did not** list it |
> | Same project, after step 4 | `cpadmin` listed it, while still not a member |

### Coverage is handled by the blueprint

You do **not** need to add `cpadmin` to each project by hand.
[orchestrate.sh](scripts/orchestrate.sh) does it on every run: it enumerates all
projects, lists the ones it is already an editor on, and enrols itself on the difference before
launching any export pod. Validated end-to-end on 2026-08-07 — a keeper that was an editor on **0**
projects enrolled itself on a freshly created project and exported it (145 KB bundle).

If you want to see the current state, or diagnose an enrolment failure, these are the two lists the
script compares:

```bash
SVC_USER=cpadmin

# A. Every project in the cluster (this is what manage_project buys you)
./bin/cpdctl project list --output json \
  | sed -n '/^{/,$p' | jq -r '.resources[].metadata.guid' | sort > /tmp/visible.txt

# B. The ones it can already export (editor-or-above membership)
./bin/cpdctl project list --member "$SVC_USER" --roles admin,editor --output json \
  | sed -n '/^{/,$p' | jq -r '.resources[]?.metadata.guid' | sort > /tmp/exportable.txt

# A - B = what the next run will enrol itself on.
comm -23 /tmp/visible.txt /tmp/exportable.txt
```

> `sed -n '/^{/,$p'` guards against a pagination line `cpdctl` can print before the JSON, which
> otherwise breaks `jq`. Note `comm` requires **sorted** input — without the `sort` it silently
> reports wrong differences rather than warning.

If enrolment fails, the prehook aborts **before any data moves** with
`BACKUP PREHOOK FAILED: could not become an editor on N project(s)`. The usual causes are
`cpadmin` missing `manage_project` (step 4 not done, or undone by a later role change), or a wrong
`uid` in the credential Secret.

**Notes on the credentials**

| | |
|---|---|
| **The API key does not expire** | `expire_at` comes back `null`. Unlike the bearer token, it is a long-lived credential — treat the Secret accordingly, and rotate it deliberately. |
| **The bearer token is short-lived** | The JWT from step 1 carries a **12-hour** lifetime. It is only needed for the setup calls; the keeper never uses it — it authenticates with the API key (`--apikey-stdin`), which is why no password lives in the cluster. |
| **`-k` skips TLS verification** | The `cpd` route usually presents the cluster's ingress certificate. Drop `-k` and pass `--cacert <ca.crt>` if you have the CA — leaving `-k` in a scripted, repeatable procedure is a habit worth avoiding. |
| **Regenerating the key breaks the Secret** | `GET /usermgmt/v1/user/apiKey` (step 5) and the UI (*Profile and settings → API key*) both **generate a new key and invalidate the old one**. Because the keeper shares `cpadmin`'s key, anyone who regenerates it — by re-running step 5, or for their own scripts — breaks the backup: the stale key fails at `cpdctl` login time, inside `backupPrehook`, failing the whole backup. Update the Secret every time. |
| **Don't leave the key in your shell history** | The commands above keep it in a variable rather than on a command line. If you paste a key literally, clear it afterwards. |

> **Endpoints verified** against CP4D 5.2.x on 2026-08-07: `POST /icp4d-api/v1/authorize` → `200`
> with a `token`; `GET /usermgmt/v1/user/apiKey` → `200` with `apiKey` + `expire_at`. Re-verified on
> 2026-10-15: after a second `GET`, the previous key returned `Unauthorized` on
> `POST /icp4d-api/v1/authorize` (`{"username","api_key"}`) and the new one returned a token.

### 3. Namespace, keeper ServiceAccount, and scoped RBAC

```bash
kubectl apply -f - <<EOF          # unquoted: ${CPD_NS} must expand
apiVersion: v1
kind: Namespace
metadata:
  name: cp4d-projects-backup
  labels:
    pod-security.kubernetes.io/enforce: privileged   # export pods run as arbitrary UID; tighten in prod
---
apiVersion: v1
kind: ServiceAccount
metadata: { name: cp4d-backup-keeper, namespace: cp4d-projects-backup }
---
# CP4D instance namespace: keeper SA may GET only the one credential secret (read cross-namespace, never mounted)
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: cp4d-backup-read-creds, namespace: ${CPD_NS} }
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["cp4d-backup-cpdctl-creds"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: cp4d-backup-read-creds, namespace: ${CPD_NS} }
subjects: [ { kind: ServiceAccount, name: cp4d-backup-keeper, namespace: cp4d-projects-backup } ]
roleRef: { kind: Role, name: cp4d-backup-read-creds, apiGroup: rbac.authorization.k8s.io }
---
# backup ns: keeper SA manages its own export pods + PVCs
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: cp4d-backup-keeper, namespace: cp4d-projects-backup }
rules:
  - apiGroups: [""]
    resources: ["pods","pods/log","persistentvolumeclaims"]
    verbs: ["get","list","watch","create","delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: cp4d-backup-keeper, namespace: cp4d-projects-backup }
subjects: [ { kind: ServiceAccount, name: cp4d-backup-keeper, namespace: cp4d-projects-backup } ]
roleRef: { kind: Role, name: cp4d-backup-keeper, apiGroup: rbac.authorization.k8s.io }
EOF
```

> **Storage class**: the blueprint defaults to `ocs-storagecluster-ceph-rbd`, the ODF RBD class of
> our bare-metal test cluster. The last validation (2026-11-18) ran on Azure with `managed-csi`
> instead. Replace it
> (in [blueprint.yaml](blueprint.yaml), `STORAGE_CLASS`) with a class that supports **CSI
> snapshots** on your cluster (for example `managed-csi` on AKS, `ebs-sc` on AWS, `standard-rwo` on
> GKE) and has a matching `VolumeSnapshotClass` registered with Kasten. Do **not** use legacy
> in-tree classes.

### 4. A Location profile

The Kanister action hook needs a **Location** profile (S3/GCS/Azure Blob — not Infra):

```bash
kubectl get profile <name> -n kasten-io -o jsonpath='{.spec.type}'   # must print: Location
```

---

## Deploy

```bash
# 1. blueprint (kasten-io), with CRED_NS set to your CP4D instance namespace
sed "s/export CRED_NS=\"cpd\"/export CRED_NS=\"${CPD_NS}\"/" blueprint.yaml | kubectl apply -f -
kubectl get blueprint cp4d-projects-backup-bp -n kasten-io -o yaml | grep 'export CRED_NS'   # must show ${CPD_NS}, twice

# 2. policy targeting the cp4d-projects-backup namespace, wired to the action hook
kubectl apply -f - <<'EOF'
apiVersion: config.kio.kasten.io/v1alpha1
kind: Policy
metadata:
  name: cp4d-projects-backup-policy
  namespace: kasten-io
spec:
  comment: "CP4D per-project artifact backup"
  frequency: "@daily"                 # or @onDemand
  actions:
    - action: backup
      backupParameters:
        profile: { name: <your-location-profile>, namespace: kasten-io }
        hooks:
          preHook:
            blueprint: cp4d-projects-backup-bp
            actionName: backupPrehook
  selector:
    matchExpressions:
      - key: k10.kasten.io/appNamespace
        operator: In
        values: [ cp4d-projects-backup ]
EOF
```

The hook attaches at `spec.actions[].backupParameters.hooks.preHook` = `{blueprint, actionName}`.

---

## Blueprint actions

| Action | Trigger | What it does |
|---|---|---|
| `backupPrehook` | Before Kasten's PVC discovery/snapshot | Runs `orchestrate.sh` from the `cp4d-backup-scripts` ConfigMap, after copying that ConfigMap into the backup namespace for the export pods: authenticates cpdctl (cross-ns secret), enumerates **all** CP4D analytics projects, **enrols itself as editor on any project it is not already a member of** (aborting before any data moves if that fails), ensures one permanent GUID-keyed PVC per project, launches one export pod per project (bounded concurrency, one retry; each pod stores the export zip atomically in `/backup/current/export.zip` and flushes the filesystem; a failed pod is detected at once, not after `EXPORT_TIMEOUT`), prunes PVCs of deleted projects, deletes export pods so nothing sensitive is captured. Exits non-zero (fails the whole backup) if any project fails to enrol or export. |

| `restoreProjects` | Selected by the user as the hook to run **after a successful restore** (Kasten UI restore form; API: `RestoreAction` `spec.hooks.onSuccess`). Runs in the restore's **target** namespace | Runs `restore-projects.sh` from the ConfigMap. **Refuses to run** in a namespace that this blueprint backs up: a policy whose `preHook` uses this blueprint selects it, or it contains the `cp4d-backup-keeper` ServiceAccount. Finds the restored project PVCs (label `app.kubernetes.io/managed-by=cp4d-projects-backup`), copies the scripts and a temporary credential Secret into the namespace, and starts one restore pod per PVC (4 at a time, restricted security context). Each pod recreates its project under its own name, or as `<name>-restored-<date>` when that name is taken, then imports the zip. On success the PVC is annotated `cp4d.io/restored-as`, so a second run of the hook skips it. The temporary Secret is always deleted. Exits non-zero (fails the restore) if the namespace is refused, holds no project PVC, or any project fails. |

---

## Restore

The whole restore is done in the **Kasten UI**. Nobody needs `cpdctl`, `kubectl` or a terminal.

1. Kasten restores the PVCs of the projects you want into a **new namespace**.
2. The blueprint action `restoreProjects`, selected as the hook to run after the restore, recreates
   each of those projects in CP4D.

Different users can restore different projects into different namespaces at the same time.

### What happens to each project

| The project in CP4D | What the restore does |
|---|---|
| Was deleted | Recreated under **its own name**, with a new id |
| Still exists (same name) | **Not touched.** The backup is restored **beside it** as `<name>-restored-<YYYYMMDD-HHMM>` (UTC). Compare the two, then delete the one you do not want |

A restore never deletes or overwrites a project. If the import of a project fails, the empty
project the hook just created is deleted again, and the restore is marked **failed**.

> ⚠️ **DataStage Data Sets.** When a project is restored beside the original, both copies point to
> the same Data Set data on the DataStage engine. Do not run the jobs of the `-restored-` copy while
> the original still exists. See [Projects containing DataStage flows](#projects-containing-datastage-flows), point 2.

### In the Kasten UI

1. **Applications** → `cp4d-projects-backup` → **Restore**, and pick a restore point.
2. Restore into a **new namespace**, for example `cp4d-projects-restore`. Never restore into
   `cp4d-projects-backup` itself (see [Guard](#guard-the-backup-namespace) below).
3. In the artifacts, **deselect everything, then select only the volumes** of the projects you want.
   The volume name is `cp4d-<project name>-<first 8 characters of the project id>`. Each volume you
   select becomes a project in CP4D.

   Do not select any spec (ServiceAccounts, Roles, ConfigMaps, and so on). The hook does not need
   them. If you restore the ServiceAccount `cp4d-backup-keeper`, the hook refuses to run, because that
   ServiceAccount marks the backup namespace.
4. In the hooks, choose the hook to run **after the restore, on success**: blueprint
   `cp4d-projects-backup-bp`, action `restoreProjects`.
5. Start the restore. When it is **Completed**, the projects are in CP4D. If it **Failed**, the
   error shown by Kasten contains the hook's output (see [Troubleshooting a restore](#troubleshooting-a-restore)).
6. When you have checked the projects, **delete the restore namespace**. It only holds copies of
   the backup PVCs.

### The same restore with `kubectl`

```bash
kubectl get restorepoint -n cp4d-projects-backup        # pick a restore point
kubectl create ns cp4d-projects-restore

kubectl create -f - <<EOF
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RestoreAction
metadata:
  generateName: restore-cp4d-
  namespace: cp4d-projects-restore          # action ns MUST equal targetNamespace
spec:
  subject:
    apiVersion: apps.kio.kasten.io/v1alpha1
    kind: RestorePoint
    name: <restorepoint-name>
    namespace: cp4d-projects-backup          # the source restore point
  targetNamespace: cp4d-projects-restore
  filters:
    includeResources:                       # only the PVCs of the projects you want
      - { resource: persistentvolumeclaims, name: cp4d-<project>-<guid> }
      - { resource: persistentvolumeclaims, name: cp4d-<other-project>-<guid> }
  hooks:
    onSuccess: { blueprint: cp4d-projects-backup-bp, actionName: restoreProjects }
EOF
```

`onSuccess` runs only when the volume restore succeeded. `{{ .Namespace.Name }}` in the hook is the
restore's **target** namespace (checked in the Kasten source: `restoreHookNamespace` returns
`TargetNamespace`).

### Guard: the backup namespace

The hook refuses to run in a namespace this blueprint backs up. Restoring every project PVC and
importing all of them is never what a user wants. Two checks, so that no namespace name is
hardcoded:

| Check | How |
|---|---|
| A Kasten policy whose `preHook` uses this blueprint selects the namespace | The hook reads the policy's own selector. The key `k10.kasten.io/appNamespace` is replaced by `kubernetes.io/metadata.name`, which every namespace carries, and `kubectl` evaluates the result. A policy with no selector covers every namespace. A selector the hook cannot evaluate fails the hook |
| The namespace contains the ServiceAccount `cp4d-backup-keeper` | Only the backup namespace has it ([Prerequisite 3](#3-namespace-keeper-serviceaccount-and-scoped-rbac)). A restore that selects only volumes does not bring it back |

The refusal reads, in the error of the failed restore:

```
RESTORE HOOK REFUSED: namespace cp4d-projects-backup is backed up by policy cp4d-projects-backup-policy
```

The hook runs **after** Kasten has restored the volumes, so it cannot stop Kasten from overwriting
the PVCs of `cp4d-projects-backup` with older content. This is not a data loss: the next backup
exports every project again into those PVCs. To forbid such a restore before it starts, add an
admission policy (for example Kyverno) that denies a `RestoreAction` with
`targetNamespace: cp4d-projects-backup` to non-admins.

### Troubleshooting a restore

The hook's output is in the Kasten error of a failed restore, and in the `kanister-svc` log, field
`Pod_Out`:

```bash
kubectl logs -n kasten-io deploy/kanister-svc --since=30m \
  | grep -o '"Pod_Out":"[^"]*"' | sed 's/"Pod_Out":"//; s/"$//' | grep -v '^$'
```

A successful run ends with:

```
--- cp4d-fixture-05-9ed27dfe (pod restore-332283dd-…, phase Succeeded)
    no project named 'fixture-05': recreating it under the same name
    …
    RESTORED: 'fixture-05' -> 'fixture-05' (ed27bf93-ed2b-41ed-bf30-0bed586ae0af)
restored 2 of 2 project(s)
OK: every restored PVC in cp4d-projects-restore was imported into CP4D.
```

Each restored PVC records what it became, in the annotation `cp4d.io/restored-as`:

```bash
kubectl get pvc -n cp4d-projects-restore \
  -o custom-columns=PVC:.metadata.name,RESTORED_AS:.metadata.annotations.cp4d\\.io/restored-as
```

Kasten can run the hook again after a transient error. A second run skips every PVC that already
has this annotation, and reuses a restore pod of the previous run that is still running, so a
project is never imported twice.

---

## Create the test fixture (test data)

The CP4D platform itself is assumed already installed. This section creates a **minimum
representative dataset** — one analytics project containing a notebook, a job, a **file** data asset
and a **connected** data asset through a real connection. Those last two are the interesting pair:
they behave **oppositely** on export (bytes travel vs reference only, see
[DESIGN.md § 6](../DESIGN.md#6-the-data-and-credential-boundary-validated-2026-07-10)), so a restore
that recovers both proves the boundary is understood. Everything here is well under 5 KB.

### 1. Get `cpdctl` on your workstation

`cpdctl` is **not vendored in this repo** (`cp4d/bin/` is git-ignored). Download the release for your
OS/arch from [github.com/IBM/cpdctl/releases](https://github.com/IBM/cpdctl/releases) — use the
**latest stable** version, which is backward compatible with all supported CP4D releases (see
[DESIGN.md § 7](../DESIGN.md#7-why-latest-cpdctl-is-the-right-version-policy)):

```bash
mkdir -p bin && cd bin
# with the GitHub CLI — asks for the latest stable tag rather than hardcoding one:
TAG=$(gh release view --repo IBM/cpdctl --json tagName -q .tagName)     # e.g. v1.10.34
gh release download "$TAG" --repo IBM/cpdctl --pattern 'cpdctl_darwin_arm64.tar.gz'
tar xzf cpdctl_darwin_arm64.tar.gz && rm cpdctl_darwin_arm64.tar.gz
xattr -d com.apple.quarantine cpdctl 2>/dev/null                        # macOS Gatekeeper
./cpdctl version
cd ..
```

> Pick the asset matching your platform (`uname -m`); the example is macOS Apple Silicon. Without
> `gh`, grab the same asset from the releases page by hand.

### 2. Point `cpdctl` at the cluster

The blueprint configures its own throwaway profile from the credential Secret, so this step is only
needed for the fixture and verification commands below.

```bash
# CP4D web URL and the bootstrap admin credentials
oc get route cpd -n "${CPD_NS}" -o jsonpath='https://{.spec.host}{"\n"}'
oc get secret platform-auth-idp-credentials -n "${CPD_NS}" -o jsonpath='{.data.admin_username}' | base64 -d; echo
oc get secret platform-auth-idp-credentials -n "${CPD_NS}" -o jsonpath='{.data.admin_password}' | base64 -d; echo

# keep the config file next to the binary and OUT of git (cp4d/bin/ is ignored)
export CPDCONFIG=$PWD/bin/.cpdctl.config.json
# --password-stdin keeps the password off the command line (cpdctl >= 1.10 warns about --password)
printf '%s' '<ADMIN_PASSWORD>' | ./bin/cpdctl config user set admin --username <admin-username> --password-stdin
./bin/cpdctl config profile set cp4d --url https://<cpd-route-host> --user admin
./bin/cpdctl config profile use cp4d
./bin/cpdctl project list                       # verify connectivity
```

> The config file holds the CP4D password — **never commit it**. `cp4d/.gitignore` covers both
> `bin/` and `.cpdctl.config.json` anywhere in the tree. Admin credentials are fine for creating a
> fixture; the *blueprint* authenticates with the `cpadmin` API key instead (Prerequisite 2).

### 3. Create the project and its assets

```bash
# the analytics project (COS-backed 'assetfiles' storage — no external object store needed)
./bin/cpdctl project create --name cp4d-fixture --type cpd --storage-type assetfiles
PID=<project id printed in the Location header>
```

Add a notebook and a job through the CP4D web UI (Watson Studio): a two-cell Python notebook, then
a job that runs it. Then the two data assets:

```bash
# (a) a 3-row CSV on the in-cluster MinIO, used as the external datasource
kubectl run mc-fixture -n minio --image=minio/mc:latest --restart=Never --rm -i --command -- sh -c '
  mc alias set local http://minio.minio.svc:9000 minio minio123
  mc mb -p local/cp4d-fixture
  printf "id,name,value\n1,alpha,100\n2,beta,200\n3,gamma,300\n" > /tmp/sample.csv
  mc cp /tmp/sample.csv local/cp4d-fixture/sample.csv'

# (b) a FILE data asset — bytes uploaded INTO the project, so they WILL be in the bundle
printf 'id,name,value\n1,alpha,100\n2,beta,200\n3,gamma,300\n' > sample.csv
./bin/cpdctl asset data-asset upload --project-id "$PID" \
  --file sample.csv --name sample.csv --mime text/csv

# (c) a connection to MinIO — this is what carries credentials
./bin/cpdctl connection create --project-id "$PID" \
  --name minio-fixture-conn --datasource-type generics3 --test=false \
  --properties '{"bucket":"cp4d-fixture","url":"http://minio.minio.svc.cluster.local:9000","access_key":"minio","secret_key":"minio123"}'

# (d) a CONNECTED data asset — a reference through that connection, so the bytes stay on MinIO
CONN=<connection id from (c)>
./bin/cpdctl asset data-asset create --project-id "$PID" \
  --metadata-name sample-connected.csv --metadata-asset-type data_asset \
  --metadata-asset-category USER --metadata-origin-country us \
  --entity '{"data_asset":{"mime_type":"text/csv","dataset":false}}' \
  --attachments '[{"asset_type":"data_asset","connection_id":"'"$CONN"'","connection_path":"cp4d-fixture/sample.csv","name":"minio sample.csv","mime":"text/csv"}]'
```

> **Three gotchas, all of which cost time to find:**
> - `--assets` expects a **JSON** value, not the keyword `all`. To export everything use the
>   decomposed boolean **`--assets-all-assets`**.
> - `connection create` **runs a live connect-test by default** and refuses to save if it fails.
>   `--test=false` saves the connection anyway. Needed here: the CP4D S3 connector uses
>   **virtual-hosted** addressing (`bucket.host`), which MinIO's path-style endpoint does not
>   satisfy → a tested connect fails with `UnknownHostException`. Irrelevant for backup-fidelity
>   testing, where what matters is what export captures, not runtime queries.
> - `data-asset create` requires both `--metadata-asset-category` and `--metadata-origin-country`.
>
> The MinIO instance is the throwaway one from
> [kasten-s3troubleshooting](https://github.com/michaelcourcy/kasten-s3troubleshooting?tab=readme-ov-file#test-with-a-minio-instance)
> (`minio`/`minio123` — test fixture credentials, not secrets).

---

## Test it end-to-end

```bash
# 1. run an on-demand backup
kubectl create -f - <<'EOF'
apiVersion: actions.kio.kasten.io/v1alpha1
kind: RunAction
metadata: { generateName: run-cp4d-backup-, namespace: kasten-io }
spec:
  subject: { apiVersion: config.kio.kasten.io/v1alpha1, kind: Policy, name: cp4d-projects-backup-policy, namespace: kasten-io }
EOF

# 2. confirm per-project PVCs + snapshots + restore point
kubectl get pvc -n cp4d-projects-backup -L cp4d.io/project-id
kubectl get volumesnapshot -n cp4d-projects-backup
kubectl get restorepoint -n cp4d-projects-backup

# 3. restore (see Restore above: new namespace, only the project PVCs, hook restoreProjects),
#    then verify the project came back:
./bin/cpdctl project list --name <project-name> --match exact
./bin/cpdctl asset search --project-id <new-pid> --type-name asset --query '*:*'
```

**Validated end-to-end on 2026-11-19: restore through the `restoreProjects` hook** (Kasten 9.0.11 /
OpenShift 4.21.30 on Azure / CP4D 5.4.0 Patch 7 with Watson Pipelines and DataStage 5.4.7, keeper
image `5.4-r3`, storage `managed-csi`). Seven projects `fixture-01` to `fixture-07`, each with a
compiled DataStage flow `customers-etl`, its job and a file data asset `marker.csv`; projects 1 to 5
also had a Watson Pipelines pipeline. The restores were `RestoreAction`s with `spec.hooks.onSuccess`,
the object the Kasten UI creates. The same scenario was then run by hand through the Kasten UI
restore form (new namespace, volumes only, hook `restoreProjects` after a successful restore) and
gave the same result.

| Check | Result |
|---|---|
| Backup through a `RunAction` | `Complete`. 7 PVCs, one restore point |
| Change after the backup | `fixture-02`: flow and job deleted, `marker.csv` replaced by `after-backup.csv`. `fixture-05`: project deleted |
| Restore of those 2 PVCs into the new namespace `cp4d-projects-restore` | `Complete`. Only the 2 PVCs restored |
| `fixture-05` (deleted) | Recreated under its own name, new id: flow, job, `marker.csv`, pipeline `pipe-5` |
| `fixture-02` (still there) | Left as it was (`after-backup.csv`, no flow). `fixture-02-restored-20261119-1254` created beside it with flow, job, `marker.csv`, pipeline `pipe-2` |
| Restore into `cp4d-projects-backup` | `Failed`, nothing imported: `RESTORE HOOK REFUSED: namespace cp4d-projects-backup is backed up by policy cp4d-projects-backup-policy`. With the ServiceAccount check alone: also refused |
| Hook run a second time on the same namespace | `already restored by a previous run, as fixture-02 (<id>); skipped`. No second copy |
| Restore pod | SCC `restricted-v2`, random uid, no PodSecurity warning. The temporary credential Secret is gone at the end |

> **Two bugs found during this run, both fixed before the results above.** A worker node stopped
> responding in the middle of a restore (an Azure problem, the node came back by itself). The hook
> failed and Kasten ran it again by itself. That showed:
>
> 1. **The hook was not safe to run twice.** A second run would have imported a project already
>    restored, as a second `-restored-` copy. Fix: the `cp4d.io/restored-as` annotation and the reuse
>    of a running pod (see [Troubleshooting a restore](#troubleshooting-a-restore)).
> 2. **`cpdctl` panics without a writable home directory.** Under the restricted SCC the pod runs
>    with a random uid and `HOME=/`, and `cpdctl` stops with
>    `panic: configuration error: mkdir /.bluemix: permission denied`. Fix: `HOME=/tmp` in the pod.
>    The first run had worked only because the pod, created by Kasten's ServiceAccount, had received
>    a privileged SCC and run as root.

**Validated end-to-end on 2026-11-18: export stored as a zip, scripts from the ConfigMap, fail-fast
wait** (Kasten 9.0.11 / OpenShift 4.21.30 on Azure / CP4D 5.4.0 Patch 7 with Watson Pipelines and
DataStage, keeper image `5.4-r3`, storage `managed-csi`). Seven projects, each with a compiled
DataStage flow, its job and a file data asset `marker.csv`. Projects 1 to 5 also had a Watson
Pipelines pipeline (project 1 had two), projects 6 and 7 had none.

| Check | Result |
|---|---|
| Scripts from the ConfigMap | First line of the run: `scripts from ConfigMap kasten-io/cp4d-backup-scripts: sha256 8a9346be3213`, the same value as `cat scripts/*.sh \| sha256sum` in git |
| `backupPrehook` through a `RunAction` | `Complete`. 7 projects discovered, 7 export pods (4 at a time), 7 PVCs, one restore point |
| What is on each PVC | `export.zip` (175 to 183 KB), `.cp4d-project-id`, `.cp4d-project-name` |
| Duplicate entries in the zip | **One per pipeline**: 2 in project 1, 1 in projects 2 to 5, 0 in projects 6 and 7. The two copies of `orchestration_flow.<id>.json` have the same MD5 |
| `unzip` on one of these zips | `replace …/orchestration_flow.<id>.json? … (EOF or read error, treating as "[N]one" ...)`, exit code 1. This is the error of the earlier versions |
| Change before restore | Project 4: pipeline deleted, DataStage job deleted, `marker.csv` replaced by a `v2-CHANGED` version |
| Granular restore | A `RestoreAction` filtered to the project 4 PVC restored only that PVC, into a new namespace |
| `restore.sh` (same name, typed confirmation; replaced by the `restoreProjects` hook on 2026-11-19) | Deleted the changed project, created a new one, `importing /backup/current/export.zip (177066 bytes) … State: completed` |
| Data check | Pipeline `pipe-4`, job `customers-etl-job` and flow back; `marker.csv` back to `v1`; the restored job **ran without recompiling** (`5 records exported`, `Completed`) |
| Fail-fast, a failing export | One line added to `export-project.sh` **in the ConfigMap only** made project 2 exit 1. The pod failed in seconds and did not block the batch. The 6 other projects were exported, project 2 was retried once, and the prehook ended with `BACKUP PREHOOK FAILED: 1 project(s) failed to export: <guid>` |

> Kasten runs a failed `backupPrehook` again by itself, so the failing `RunAction` above took
> 403 seconds in total for about 2 minutes per attempt. This is Kasten's retry, not a wait in the
> script.

**Re-validated end-to-end on 2026-10-16 after upgrading CP4D from 5.2.2 to 5.4.0 Patch 7**
(Kasten 9.0.7 / OpenShift 4.21.30, keeper image `1.10.34-1`, storage `ocs-storagecluster-ceph-rbd`).
The blueprint itself needed no change for 5.4. The keeper image was rebuilt for a newer `cpdctl`
and `kubectl`, and now passes the API key on stdin.

| Check | Result |
|---|---|
| `cpadmin`'s *Backup Reader* role and API key after the upgrade | Both survived: `has_manage_project: true`, and the key in the Secret still authenticates |
| `backupPrehook` through a `RunAction` | `Complete` in about 1 minute. 3 projects discovered, `cpadmin` already an editor on all 3, 3 export pods |
| Snapshot scope | 3 PVCs, 3 CSI snapshots, one restore point |
| Loss | The fixture project (notebook, job, file data asset, connection, connected data asset) **deleted** from CP4D |
| Granular restore | A `RestoreAction` filtered to that one PVC restored only that PVC, into a new namespace |
| `restore.sh` | Recreated the project under a new id: `importing 97 files … State: completed` |
| Data check | The same five assets came back, and the file data asset `sample.csv` came back byte for byte |

**Validated end-to-end on 2026-07-10** (Kasten 8.5.12 / CP4D 5.2.x): backup produced one PVC per
project, all CSI-snapshotted into one restore point; a granular cross-namespace restore of a single
PVC + `restore.sh` recreated the project with its notebook, job, file data asset, connected data
asset, and connection.

**Self-enrolment re-validated on 2026-08-07** (keeper image `1.8.244-3`). A project was created by a
*different* user (`cp4dsvc`) with one data asset; the keeper (`cpadmin`, holding `manage_project`)
was **not** a member of it. After one `RunAction`:

| Check | Result |
|---|---|
| Keeper added to the foreign project | `cpadmin role=editor state=ACTIVE` — added automatically |
| Foreign project got its own PVC | `cp4d-e2e-foreign-project-ace60e4e` |
| Bundle written to that PVC | 76 files, `.cp4d-project-name=e2e-foreign-project`, the uploaded data asset present under `assets/.METADATA/` |
| An **empty** project in the same run | PVC created, export skipped, backup still succeeded (`SPACES0046E` is treated as a no-op) |
| Stale PVCs of deleted projects | pruned |

That is the whole point of the design: a project nobody granted the keeper access to was protected
on the next backup with no operator action.

---

## Security — credentials in the bundle (read this)

`cpdctl` export does **not** redact connection credentials — by default they are written in
**cleartext** into the bundle (evidence in [DESIGN.md § 6](../DESIGN.md#6-the-data-and-credential-boundary-validated-2026-07-10)).
Consequences:

- **V1 (this blueprint): plaintext bundle, protected by RBAC.** `ENCRYPTION_KEY=""`. Acceptable
  under a **single backup admin** with the backup namespace locked down. The bundle PVCs and the
  namespace **must** be tightly RBAC-restricted. The CP4D API key is *not* in the bundle (it lives
  in the CP4D instance namespace, read cross-namespace, rendered to tmpfs) — only the *connection* credentials inside
  exported projects are.
- **Hardening / multi-tenant: encrypt.** Set `ENCRYPTION_KEY` (a managed secret) in **both**
  actions of the blueprint, to the same value (`restoreProjects` must decrypt what
  `backupPrehook` encrypted). cpdctl encrypts the
  masked properties on export and decrypts on import. When tenants self-serve restores into their
  own namespaces, encryption is **not** optional — plaintext would fan credentials out into tenant
  namespaces. At that point store the key in a **KMS** (Azure Key Vault + Secrets Store CSI), never
  a k8s Secret — which also keeps it structurally out of any backup.

## The external-datasource boundary

`cpdctl` export captures the **project** (notebooks, jobs, file data assets, connection
*definitions*, the dependency graph) but **not the external data** behind a connection (the S3
objects, DB rows, on-prem files). A **connected** data asset travels as a *reference only*. This is
a deliberate boundary — the same one cpdbr has. Per connection, the team decides whether the
external datasource also needs protection:

- **No action** if it is read-only/versioned or a system-of-record with its own backups — the
  project just needs it to still exist at restore time.
- **A separate, independent process** otherwise — e.g. a separate Kasten policy for the namespace
  hosting that database. The CP4D project backup and the datasource backup are then two
  coordinated-but-independent restore points.

Document this per project so a restore is never silently incomplete. Bundle-level evidence in
[DESIGN.md § 6](../DESIGN.md#6-the-data-and-credential-boundary-validated-2026-07-10).

> **Git-based projects fall under the same rule.** If a project is git-based, the git remote *is* an
> external datasource and needs its own independent protection — and you should still back up the
> CP4D metadata with this blueprint. Git is tamper-*evident*, not tamper-*proof*, and in a real
> disaster the whole CI/CD chain is usually inside the blast radius. See
> [DESIGN.md § 4](../DESIGN.md#git-based-does-not-remove-the-need-to-back-up-the-metadata).

---

## Projects containing DataStage flows

If the CP4D installation has **DataStage** installed, this blueprint already protects the DataStage
design assets, because they are ordinary project assets. Measured on CP4D 5.2.2 / DataStage 5.2.2
(2026-09-09), a project exported with the same `cpdctl asset export start --assets-all-assets` this
blueprint runs contained:

| In the bundle | Notes |
|---|---|
| `data_intg_flow` | the flow itself |
| `px_executables` | the **compiled** artifacts, as an attachment of the flow |
| `job` | the runnable binding of flow + parameters + environment |
| `data_intg_data_set` metadata **and** the Data Set *descriptor* files | the descriptor only — see below |
| `data_intg_project_settings`, `environment` | project-level DataStage settings |
| `data_asset` bytes | as for any project |

Imported into a new, empty project, the flow came back reporting `Compiled: true` /
`Need Compilation: false`, and **the restored job ran without being recompiled**. So no separate
export path is needed for DataStage: `cpdctl dsjob export-project` produces a *smaller* bundle
(DataStage components only) and is not required here.

Three things a restore of such a project must account for:

1. **Recompile if the flow says so.** Check with
   `CPDCTL_ENABLE_DSJOB=true cpdctl dsjob list-flows --project <name> --with-compiled`. If
   `Need Compilation` is `true`, run `cpdctl dsjob compile --project <name>` — with no `--name` it
   compiles every flow in the project. Compiling is a real build step: it writes `OshScript.osh` and
   a `lib/` directory onto the DataStage engine volume.
2. **A Data Set is restored as a pointer, not a copy — which is correct for a recovery, and
   dangerous when you clone.** A Data Set's *descriptor* travels in the bundle; its **data** lives
   on the DataStage engine volume, which is shared by every project on that runtime instance. In a
   real recovery the original is gone, or you are importing onto another cluster, so a descriptor
   addressing engine storage is exactly right — the data files are simply absent until a job
   rewrites them.

   The hazard is **duplication**: a second copy of a project alongside the original on the *same*
   runtime instance. Verified — the descriptor written into a freshly imported project is
   **byte-identical** to the source project's, and reading the Data Set there returned the source's
   rows **before any job had run**. Because a Data Set write deletes the existing data files before
   rewriting them, running the imported job destroyed the source project's Data Set:
   `view-dataset` on the **original** project then failed with `Sample data could not be read from
   dataset …`. So: do not run the imported jobs while the original project still exists on the same
   runtime instance.

3. **Engine state is outside this blueprint.** Custom libraries (`user-lib`), ODBC/JDBC driver
   configuration, Data Set data, files a flow writes to a file-system path, and user volumes live on
   the DataStage engine PVCs and are in no project export. A flow that calls a custom routine will
   restore fine and then fail at run time if the library is gone. See
   [datastage-example](../../datastage-example/5.4/), which protects exactly that and nothing else.

---

## Remove everything

The CP4D platform itself is out of scope here (installed and torn down independently). To create the
test fixture project, see [Create the test fixture](#create-the-test-fixture-test-data) above; to
remove it: `./bin/cpdctl project delete --project-id <PID>`, and
`kubectl -n minio delete pod mc-fixture --ignore-not-found` if the fixture pod is still around.

Teardown of this blueprint's resources:

```bash
# policy + blueprint
kubectl delete policy cp4d-projects-backup-policy -n kasten-io
kubectl delete blueprint cp4d-projects-backup-bp -n kasten-io
kubectl delete configmap cp4d-backup-scripts -n kasten-io

# backup namespace (per-project PVCs + snapshots) and restore namespaces
kubectl delete ns cp4d-projects-backup
kubectl delete ns cp4d-projects-restore

# credential secret + RBAC in the CP4D instance namespace
kubectl delete secret cp4d-backup-cpdctl-creds -n "${CPD_NS}"
kubectl delete role,rolebinding cp4d-backup-read-creds -n "${CPD_NS}"

# optional: take manage_project away from cpadmin again (removes the Backup Reader role;
# same PUT rules as in Prerequisite 2 — the body must repeat "username")
CPD_HOST=$(oc get route cpd -n "${CPD_NS}" -o jsonpath='{.spec.host}')
TOKEN=$(curl -sSk -X POST "https://${CPD_HOST}/icp4d-api/v1/authorize" -H 'Content-Type: application/json' \
  -d "{\"username\":\"cpadmin\",\"password\":\"$(oc get secret ibm-iam-bindinfo-platform-auth-idp-credentials -n "${CPD_NS}" -o jsonpath='{.data.admin_password}' | base64 -d)\"}" | jq -r .token)
ROLE_ID=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/roles" -H "Authorization: Bearer ${TOKEN}" \
  | jq -r '.rows[] | select(.doc.role_name=="Backup Reader") | .id')
ROLES=$(curl -sSk "https://${CPD_HOST}/usermgmt/v1/user/cpadmin" -H "Authorization: Bearer ${TOKEN}" \
  | jq -c --arg r "$ROLE_ID" '.user_roles - [$r]')
curl -sSk -X PUT "https://${CPD_HOST}/usermgmt/v1/user/cpadmin" -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' -d "{\"username\":\"cpadmin\",\"user_roles\":${ROLES}}"

# Kasten restore-point content for this app
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=cp4d-projects-backup
```

---

## Initial prompt

> Can you make a blueprint for IBM Cloud Pak for Data to back up the analytics projects. cpdbr already
> does a platform-level backup but it is all or nothing, and on a multi-tenant CP4D I want to be able
> to restore a single project.
