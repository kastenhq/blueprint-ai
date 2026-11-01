# ODM on CP4BA in one sitting — enough to design a backup for it

> ## ⚠️ This is not ODM documentation — use IBM's
>
> **To learn or use Operational Decision Manager, go to IBM's official documentation, not this
> file.** CP4BA 26.0.0 docs: <https://www.ibm.com/docs/en/cloud-paks/cp-biz-automation/26.0.0>,
> ODM 9.6 docs: <https://www.ibm.com/docs/en/odm/9.6.0>
>
> This is a crash course written *while developing this blueprint*, by someone learning the product
> in order to protect it. It is deliberately partial: it covers what a backup designer needs and
> skips most of ODM.
>
> ### Status of this file
>
> **Written before the install.** §1 and §2 come from IBM's documentation and are the model to
> check. §3 and §4 are the hands-on steps to run once [install-cp4ba.md](install-cp4ba.md) is done.
> §5 records what the cluster actually shows, and it is filled in by running them. Where §5
> contradicts §2, §5 wins.

---

## 1. What ODM is

**Operational Decision Manager (ODM)** is a **business rules management system**. It takes
business decisions out of application code — *"is this loan eligible?"*, *"what discount does this
order get?"*, *"does this claim need a manual review?"* — and keeps them as **rules** that business
users can read and change without redeploying the application.

An application does not embed the rules. It sends a request (*"here is a borrower and a loan"*) to
a **decision service** over REST, and receives the decision (*"approved, yearly repayment 9 800"*).

Two properties explain most of its design, and both matter for backup:

1. **Authoring and execution are separate products with separate stores.** Rules are written and
   versioned in **Decision Center**. To run them, a snapshot of the rules is packaged and
   **deployed** to **Decision Server**, which keeps its own copy. After a deployment the two
   copies are independent: deleting a rule in Decision Center does not remove it from production,
   and losing Decision Server's store does not lose the rules' history.
2. **The execution pods hold no state.** Decision Server Runtime loads rulesets from its database
   into memory and parses them on first use. Restart it and it reloads them. All persistent state
   is in databases, not in the pods' file systems.

So the expectation going in is **a database-centric backup**. Whether that holds on CP4BA is the
first thing §5 has to confirm.

---

## 2. The object model

### 2.1 The components

| Component | What it does | Path on CP4BA |
|---|---|---|
| **Decision Center** | Authoring: the *Business console* where rules are written, tested, versioned, branched and released | `/odm/decisioncenter` |
| **Decision Server Console** | Administration of the execution side: lists deployed RuleApps, deploys new ones, manages the Java libraries the rules call | `/odm/res` |
| **Decision Server Runtime** | Execution: answers the REST calls (*Hosted Transparent Decision Service*, HTDS) | `/odm/DecisionService` |
| **Decision Runner** | Runs test suites and simulations launched from Decision Center | `/odm/DecisionRunner` |

The console, the runtime and Decision Runner together are the **Rule Execution Server (RES)** —
the older name, which survives in URLs (`/res`) and in the API.

### 2.2 Authoring objects — stored by Decision Center

| Object | What it is |
|---|---|
| **Decision service** | The top-level project: rules, data model, and how they are deployed. The unit you import and export |
| **Rule project** | A folder of rules inside a decision service |
| **Action rule** | An *if / then* rule written in BAL (*Business Action Language*), close to plain English: `if the amount of the loan is more than 1 000 000 then add "too big" to the messages` |
| **Decision table** | Many rules of the same shape written as rows of a table |
| **Ruleflow** | The order in which groups of rules run |
| **BOM** (*Business Object Model*) | The vocabulary the rules are written in (*"the amount of the loan"*) |
| **XOM** (*Execution Object Model*) | The Java classes or XML schema the BOM maps to. What actually executes |
| **Branch** / **release** | Versioning: work happens on branches, a *release* freezes a set of changes for deployment |
| **Deployment configuration** | Which rulesets go into which RuleApp, and to which Decision Server |
| **Test suite** / **simulation** | Scenarios with expected results, run by Decision Runner |

### 2.3 Execution objects — stored by Decision Server

| Object | What it is |
|---|---|
| **RuleApp** | The deployment unit: a versioned archive (`.jar`) containing one or more rulesets |
| **Ruleset** | The compiled rules that answer one kind of request. Addressed by a path: `/<ruleapp>/<version>/<ruleset>/<version>` |
| **Managed XOM** | The Java libraries (`.jar`) a ruleset needs, uploaded to Decision Server and stored in its database rather than on a file system |
| **Execution trace** / **Decision Warehouse** | Optional record of each execution. Off by default. If switched on, it can become the largest table of the deployment |

### 2.4 The persistence map — where the bytes are expected to be

This is the table the blueprint design will depend on. **It is the model to verify in §5, not a
measurement:**

| # | Store | Expected content | Reached through |
|---|---|---|---|
| 1 | **Decision Center database** (PostgreSQL) | Every authoring object in §2.2, with full history | SQL, or the Decision Center REST API (export a decision service as a `.zip`) |
| 2 | **Decision Server database** (PostgreSQL) | Deployed RuleApps, rulesets, managed XOMs, optional execution traces | SQL, or the RES REST API (`/res/api/v1/ruleapps`) |
| 3 | **Users** — OpenLDAP in Starter | `cp4admin`, `user1`…`user10`, groups | LDAP. In production this is the customer's own directory, outside the cluster |
| 4 | **Platform metadata** — Foundational Services / Zen | Role assignments made in the Platform UI, API keys, IAM configuration | Its own database, deployed by Foundational Services |
| 5 | **Kubernetes objects** | The `ICP4ACluster` CR, generated secrets (database passwords, TLS certificates), ConfigMaps | The Kubernetes API |

What §5 must answer:

- Are stores 1 and 2 two databases in **one** PostgreSQL cluster, or two clusters? On which PVCs?
- Do any ODM pods mount a PVC at all? (Expected: no.)
- Does store 4 matter for a restore? If the role assignments are lost, users log in but cannot open
  Decision Center.
- What does `cp4ba.ibm.com/backup-type: mandatory` mark? That label is IBM's own list of what a
  backup must include.

---

## 3. First login and roles

Get the URLs and the administrator password (details in
[install-cp4ba.md §6](install-cp4ba.md#6-access)):

```bash
export CP4BA_NS=cp4ba-starter
kubectl get cm icp4adeploy-cp4ba-access-info -n ${CP4BA_NS} -o jsonpath='{.data.odm-access-info}'
kubectl get secret platform-auth-idp-credentials -n ${CP4BA_NS} -o jsonpath='{.data.admin_password}' | base64 -d; echo
```

1. Open the Platform UI (`https://cpd-cp4ba-starter.<apps-domain>`). Log in with **Enterprise
   LDAP** as `cp4admin`, or with **IBM provided credentials** as `cpadmin`.
2. **Administration → Access control**: check that your user has the ODM roles. In Starter the
   install grants them to `cp4admin`. If Decision Center returns *403*, a missing role is the
   cause, not a broken install.
3. Open Decision Center (`/odm/decisioncenter`). You land in the **Business console**, with no
   decision service yet.

---

## 4. Hands-on: deploy and call a decision service

The goal is a minimal, verifiable dataset: one decision service in Decision Center, one RuleApp in
Decision Server, and one REST call whose answer you can compare after a restore.

### 4.1 Import the sample

IBM publishes a ready-made sample, **Loan Validation Service**: a borrower and a loan go in, an
*approved / rejected* decision and a list of messages come out.

```bash
mkdir -p fixture && cd fixture
curl -LO "https://github.com/DecisionsDev/odm-for-container-getting-started/raw/master/Loan%20Validation%20Service.zip"
```

In Decision Center: **Library → Import Decision Service** → choose the `.zip`.

You now have store 1 populated: one decision service, its rule projects, its BOM, a deployment
configuration and a test suite.

### 4.2 Deploy it to Decision Server

In Decision Center: open the decision service → **Deployments** → `production deployment` →
**Deploy**. This builds a RuleApp from the current state of the `main` branch and sends it to
Decision Server.

Then open the Decision Server Console (`/odm/res`) → **Explorer**. The RuleApp
`production_deployment/1.0` is there, with the ruleset `loan_validation_production/1.0`.

You now have store 2 populated, independently of store 1.

### 4.3 Call it

In the Decision Server Console, open the ruleset → **Retrieve HTDS Description File** → **OpenAPI
– JSON**. It shows the exact endpoint and a sample request. Use that sample rather than writing
one by hand — the field names come from the XOM and change between sample versions.

```bash
CPD=https://cpd-${CP4BA_NS}.<apps-domain>
# A Zen API key: Platform UI → your profile → API key → Generate
AUTH="Authorization: ZenApiKey $(printf 'cp4admin:<api-key>' | base64)"

curl -sk -H "${AUTH}" -H 'Content-Type: application/json' \
  --data @request.json \
  "${CPD}/odm/DecisionService/rest/production_deployment/1.0/loan_validation_production/1.0" | jq
```

Save `request.json` and the answer in `fixture/`. **That answer is the restore test:** after a
restore, the same request must give the same answer.

> *(To verify: that a Zen API key is accepted by `/odm/DecisionService` on 26.0. Older ODM versions
> on CP4BA required an IAM token instead.)*

### 4.4 Make a change that only one store sees

This step separates the two stores, which is what a backup test needs:

1. In Decision Center, change one rule — for example raise the maximum loan amount — and **do not
   deploy**.
2. Call the service again: the answer is **unchanged**, because Decision Server still runs the
   RuleApp deployed in §4.2.

A backup taken now must restore **both** states: the edited rule in Decision Center, and the
older ruleset in Decision Server. If a restore brings back only one of them, the two stores were
not captured at the same point.

---

## 5. What the cluster actually shows *(to be measured)*

Run after §4. Each result goes into the table at the end of this section.

### 5.1 Which pods hold state

```bash
kubectl get pods -n ${CP4BA_NS} -o custom-columns=POD:.metadata.name,PVC:.spec.volumes[*].persistentVolumeClaim.claimName \
  | grep -v '<none>'
```

### 5.2 The databases

```bash
kubectl get clusters.pg.ibm.com -n ${CP4BA_NS}   # IBM's own PostgreSQL operator (group pg.ibm.com)

# then, in the primary of each cluster:
kubectl exec -n ${CP4BA_NS} <primary-pod> -- psql -U postgres -c '\l+'
```

For each ODM database, list the largest tables and their row counts before and after §4.1, §4.2
and §4.4. That shows which table each action writes to.

### 5.3 What IBM marks for backup

```bash
kubectl get all,secret,configmap,pvc -n ${CP4BA_NS} -l cp4ba.ibm.com/backup-type -o name
```

### 5.4 Results

| Question | Answer |
|---|---|
| Decision Center database: cluster, database name, PVC | |
| Decision Server database: cluster, database name, PVC | |
| ODM pods mounting a PVC | |
| Foundational Services / Zen database: cluster, PVC | |
| OpenLDAP PVC | |
| Objects labelled `cp4ba.ibm.com/backup-type` | |
| Tables written by import (§4.1), deploy (§4.2), edit (§4.4) | |

These answers are the input to Step 1 of the blueprint workflow: choosing the backup pattern.
