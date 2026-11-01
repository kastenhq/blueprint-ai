# FileNet on CP4BA in one sitting — enough to design a backup for it

> ## ⚠️ This is not FileNet documentation — use IBM's
>
> **To learn or use FileNet Content Manager, go to IBM's official documentation, not this file.**
> CP4BA 26.0.0 docs: <https://www.ibm.com/docs/en/cloud-paks/cp-biz-automation/26.0.0>,
> FileNet P8 Platform docs: <https://www.ibm.com/docs/en/filenet-p8-platform>
>
> This is a crash course written *while developing this blueprint*, by someone learning the product
> in order to protect it. It is deliberately partial: it covers what a backup designer needs and
> skips most of FileNet.
>
> ### Status of this file
>
> **Written before the install.** §1 and §2 come from IBM's documentation and general knowledge of
> FileNet P8, and are the model to check. §3 and §4 are the hands-on steps to run once
> [install-cp4ba.md](install-cp4ba.md) is done. §5 records what the cluster actually shows, and it
> is filled in by running them. Where §5 contradicts §2, §5 wins.

---

## 1. What FileNet is

**FileNet Content Manager** is an **enterprise content management** system: a managed archive for
the documents of a business — contracts, claim forms, scanned letters, invoices, identity
documents. In CP4BA it is the **Content** capability. Before containers it was sold as
**FileNet P8**, and it has one of the largest installed bases of the CP4BA products, mostly in
banking, insurance and government.

A file share stores files. FileNet stores **documents**, which are more than files:

| Feature | Meaning |
|---|---|
| **Metadata** | A document has a **class** (*Insurance claim*) and **properties** (*claim number*, *customer ID*, *amount*). Users and applications search on them |
| **Versions** | Check out, check in, major and minor versions, full history |
| **Security** | An access control list on every document and folder, with users and groups from LDAP |
| **Retention** | Documents that may not be deleted before a date — required by law for many customers |
| **Events** | *"When a claim document is added, start a review"* — the link to Business Automation Workflow |
| **APIs** | Java, .NET, CMIS and GraphQL, so that other applications store and read their documents in FileNet |

Customers treat it as the **system of record**: the copy in FileNet *is* the contract. A lost
document is not a lost cache entry, it is a lost contract. That sets the bar for the backup.

Two properties explain most of what matters for backup:

1. **The metadata and the content live in different places.** Properties, versions, folders and
   security are rows in a database. The content — the bytes of the PDF — is usually a file in a
   **storage area** on a file system, which in CP4BA means a PVC. One document is therefore split
   across two stores, and a restore is only correct if both come back from the same moment.
2. **The servers hold little state of their own.** Content Platform Engine (CPE) and Navigator keep
   their persistent state in databases and in the storage areas. Their pods also mount PVCs for
   configuration and logs, which §5 must sort into "needed for restore" and "not needed".

---

## 2. The object model

### 2.1 The components

| Component | What it does | Path on CP4BA |
|---|---|---|
| **Content Platform Engine (CPE)** | The server. Owns the object stores, writes the metadata to the database and the content to the storage areas, enforces security | — (APIs only) |
| **ACCE** (*Administration Console for Content Platform Engine*) | The administrator's web console: object stores, classes, storage areas, security. Part of CPE | `/cpe/acce` *(to be measured)* |
| **Business Automation Navigator** | The user's web interface: browse, search, view, upload. Also called ICN (*IBM Content Navigator*), its earlier name, which survives in URLs | `/icn/navigator` *(to be measured)* |
| **Content Services GraphQL** | A GraphQL API in front of CPE, for applications and for scripts | `/content-services-graphql` *(to be measured)* |

Content Search Services (full-text search), CMIS, Task Manager and the other optional components
are **not** installed here (see [install-cp4ba.md §4.2](install-cp4ba.md#42-deployment--the-custom-resource)).

### 2.2 The objects

From the largest to the smallest:

| Object | What it is |
|---|---|
| **P8 domain** | The whole FileNet installation. One per CP4BA deployment |
| **GCD** (*Global Configuration Database*) | The domain's directory: which object stores exist and which database each one uses, which storage areas exist and where, how users authenticate (LDAP). It holds **no documents** |
| **Object store** | A repository of documents, with its own database. A domain can have several, often one per business area |
| **Document class** / **property** | The schema: a class (*Insurance claim*) defines which properties its documents have. Classes are stored in the object store, so a restore of the object store database brings back its schema |
| **Folder** | A place in a tree, like a directory. A document can be filed in several folders, or in none |
| **Document** | One version of one document: its properties, its security, and one or more **content elements** (the files) |
| **Version series** | All the versions of one document |
| **Storage area** | Where the content elements are written. See §2.3 |
| **Storage policy** | Which storage area a document class writes to |
| **Navigator desktop** / **repository** | Navigator's own configuration: which object stores users see, and how. Stored in Navigator's database, not in FileNet's |

### 2.3 Storage areas — the part that decides the backup design

When a document is added, CPE writes its content to the storage area chosen by the class's storage
policy. There are three kinds:

| Kind | Content is written to | Backup consequence |
|---|---|---|
| **Database storage area** | A table of the object store database | Metadata and content in **one** store. A database backup is enough |
| **File storage area** | A directory tree on a file system (`FN0/FN12/…` subdirectories, one file per content element) | Metadata in the database, content on a PVC. Two stores to keep consistent |
| **Advanced storage area** | One or more *storage devices*: a file system, or an S3 bucket | Same as above when the device is a file system. If the device is S3, the content is outside the cluster and Kasten does not capture it |

Which kind the Starter install creates is the first thing §5 answers.

**What can go wrong, and how it shows.** Two expected mechanisms (from FileNet's design, to be
verified in §5):

- **Adding a document:** CPE writes the content file first, then commits the database row.
- **Deleting a document:** CPE deletes the database row at once and **queues** the content file for
  deletion by a background task, which removes it later.

So, between a database backup and a file backup taken at different moments:

| Situation after restore | Symptom | Severity |
|---|---|---|
| A content file with **no** database row (an *orphan*) | Nothing visible to users. Wasted space | An inconsistency in IBM's terms, reported by the Content Consistency Checker |
| A database row whose content file is **missing** | The document is listed, its properties show, but opening it fails with a *content not found* error | **Data loss** — the document is broken |

IBM treats **both** as inconsistencies to prevent (see [README.md §3.1](README.md#31-what-these-documents-say-in-short)),
which is why the backup is taken offline. The second case is the dangerous one, and it is only
detected by **opening** the document. A test that only lists documents, or only counts rows, will report success on a broken
restore. That is why §4 records a checksum of every test file.

### 2.4 The persistence map — where the bytes are expected to be

This is the table the blueprint design will depend on. **It is the model to verify in §5, not a
measurement:**

| # | Store | Expected content | Reached through |
|---|---|---|---|
| 1 | **GCD database** (PostgreSQL) | The domain: object stores, storage areas, LDAP configuration | ACCE, or SQL |
| 2 | **Object store database(s)** (PostgreSQL) | Classes, properties, folders, documents, versions, security; the content too, if the storage area is a database storage area | ACCE, Navigator, GraphQL, or SQL |
| 3 | **Storage area** (expected: a PVC) | The content files | The file system in the CPE pod |
| 4 | **Navigator database** (PostgreSQL) | Desktops, repositories, plug-in configuration, user preferences | Navigator administration |
| 5 | **CPE and Navigator configuration PVCs** | JDBC drivers, bootstrap and configuration files, logs, plug-ins | The file system in the pods |
| 6 | **Users** — OpenLDAP in Starter | `cp4admin`, `user1`…`user10`, groups. Document security refers to these users | LDAP. In production this is the customer's own directory, outside the cluster |
| 7 | **Platform metadata** — Foundational Services / Zen | Role assignments, API keys, IAM configuration | Its own database |
| 8 | **Kubernetes objects** | The `ICP4ACluster` CR, generated secrets (database passwords, TLS certificates), ConfigMaps | The Kubernetes API |

Stores 6–8 are shared with ODM (see [mini-tutorial-odm.md §2.4](mini-tutorial-odm.md#24-the-persistence-map--where-the-bytes-are-expected-to-be)).

What §5 must answer:

- What kind of storage area does the Starter object store use? If a file storage area, on which PVC?
- Are stores 1, 2 and 4 databases in **one** PostgreSQL cluster, or in several? On which PVCs? Are
  they in the same cluster as the ODM databases?
- Which of the PVCs in store 5 are needed for a restore, and which can be recreated by the operator
  (logs, caches)?
- Is the content queue (deletion of content files) visible, and how long does it take?

---

## 3. First login

Get the URLs and the passwords (details in [install-cp4ba.md §6](install-cp4ba.md#6-access)):

```bash
export CP4BA_NS=cp4ba-starter
kubectl get cm icp4adeploy-cp4ba-access-info -n ${CP4BA_NS} -o jsonpath='{.data}' | jq
kubectl get secret icp4adeploy-openldap-customldif -n ${CP4BA_NS} -o jsonpath='{.data.ldap_user\.ldif}' | base64 -d
```

1. Open **ACCE** and log in as `cp4admin` (Enterprise LDAP). In the tree on the left, the domain
   has one or more **object stores**. Write down the name of the one created by the Starter install
   — it is used everywhere below as `<OS>`.
2. Still in ACCE, open `<OS>` → **Administrative → Storage → Storage Areas**. Note the kind of each
   storage area (§2.3) and, for a file storage area, its **root directory**. This is the first
   answer of §5.
3. Open **Navigator**. Check that the desktop shows `<OS>` as a repository and that you can browse
   it. If it is empty, that is expected: nothing has been added yet.

---

## 4. Hands-on: a dataset that proves the restore

The goal is a small, verifiable dataset (well under 5 KB): a few documents whose **content** can be
compared byte for byte after a restore, and one change of each kind a restore must undo.

### 4.1 Prepare the test files

```bash
mkdir -p fixture && cd fixture
for i in 1 2 3; do
  printf 'kasten restore test document %s\ncreated %s\n' "$i" "$(date -u +%FT%TZ)" > "kasten-doc-${i}.txt"
done
printf 'kasten restore test document 1\nVERSION 2\n' > kasten-doc-1-v2.txt
shasum -a 256 kasten-doc-*.txt | tee checksums.txt
```

The timestamp makes each file unique to this run, so a restore cannot be confused with an older
test.

### 4.2 Add them

In Navigator, in `<OS>`:

1. Create a folder `kasten-test`.
2. Add `kasten-doc-1.txt`, `kasten-doc-2.txt` and `kasten-doc-3.txt` to it, with the default
   document class. Set the **Document Title** to the file name.
3. On `kasten-doc-1.txt`: **Check out**, then **Check in** `kasten-doc-1-v2.txt` as a new major
   version. The document now has two versions with different content.

You now have stores 2 and 3 populated: rows in the object store database, and — if the storage area
is on a file system — four content files on a PVC (three documents, plus the second version).

### 4.3 Find the bytes on disk

This proves where the content is, instead of assuming it. Find the CPE pod and the storage area
root directory noted in §3:

```bash
CPE_POD=$(kubectl get pods -n ${CP4BA_NS} -o name | grep -- '-cpe-deploy-' | head -1)   # to be measured: the real name
kubectl exec -n ${CP4BA_NS} ${CPE_POD} -- sh -c \
  'grep -rl "kasten restore test document" <storage-area-root> 2>/dev/null'
```

Expected: four files under `FN*/FN*/` subdirectories. If nothing is found, the content is in the
database (a database storage area) — then look for it in the object store database instead (§5.2).

Then find which PVC that directory belongs to:

```bash
kubectl get ${CPE_POD} -n ${CP4BA_NS} -o jsonpath='{range .spec.containers[0].volumeMounts[*]}{.mountPath}{" <- "}{.name}{"\n"}{end}'
kubectl get ${CPE_POD} -n ${CP4BA_NS} -o jsonpath='{range .spec.volumes[*]}{.name}{" <- "}{.persistentVolumeClaim.claimName}{"\n"}{end}'
```

### 4.4 The restore check

This is what Step 3 and Step 5 of the blueprint workflow run after every restore. **Opening the
content is the test** — listing documents is not, for the reason given in §2.3.

For each document in `kasten-test`, in Navigator or ACCE:

1. It is listed, with its Document Title.
2. It **opens**, and the content matches: download it and compare with `checksums.txt`
   (`shasum -a 256 -c checksums.txt`).
3. `kasten-doc-1.txt` has **two versions**, and each version opens with its own content.
4. The **Content Consistency Checker** reports no inconsistency between the object store database
   and its storage areas. IBM requires it after a partial restore, and it is the only check that
   also finds orphan files. *(To be measured: how to run it on CP4BA 26.0.0.)*

A script is better than clicking, because it is repeated many times. Content Services GraphQL can
list the documents and give a download URL for each content element. *(To be measured: the exact
query on 26.0.0 — check the schema in the GraphiQL page at `/content-services-graphql` first.)*
A starting point:

```graphql
{
  documents(repositoryIdentifier: "<OS>",
            where: "DocumentTitle LIKE 'kasten-doc-%'") {
    documents {
      id
      name
      majorVersionNumber
      contentElements {
        ... on ContentTransfer { retrievalName contentSize downloadUrl }
      }
    }
  }
}
```

### 4.5 Changes the restore must undo

Take the backup (or, in Step 3, the manual snapshots) **after** §4.2. Then:

1. **Delete** `kasten-doc-2.txt`.
2. **Add** a new document `kasten-doc-4.txt`.
3. Wait a few minutes, then check on disk (§4.3) whether the content file of `kasten-doc-2.txt` has
   gone. That shows how long the deletion queue takes (§2.3).

After the restore, the check in §4.4 must pass, `kasten-doc-2.txt` must be back and open, and
`kasten-doc-4.txt` must be gone.

### 4.6 Provoke the broken case once (Step 3 only)

To be sure the check in §4.4 really detects a bad restore, produce one on purpose while validating
the workflow by hand:

1. Snapshot the **database PVC(s)** only.
2. Add `kasten-doc-5.txt`.
3. Snapshot the **storage area PVC**.
4. Delete `kasten-doc-5.txt`, wait for its content file to disappear, then restore the database
   from the snapshot of step 1 and the storage area from step 3.

Here the database is older than the files, so expect an orphan file and no broken document. Then
repeat with the order reversed (storage area first, then add, then database): the database now has
a row for `kasten-doc-5.txt` with no file behind it. Opening it must fail. If §4.4 does not report
a failure in that case, the check is not good enough to trust.

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

In the object store database, documents are expected in the `docversion` table and folders in
`container`. Count the rows before and after §4.2 and §4.5 to see which tables each action writes.

### 5.3 Results

| Question | Answer |
|---|---|
| Object store name(s) | |
| Storage area kind and root directory | |
| Storage area PVC (name, size, class, access mode) | |
| GCD database: cluster, database name, PVC | |
| Object store database: cluster, database name, PVC | |
| Navigator database: cluster, database name, PVC | |
| Same PostgreSQL cluster as the ODM databases? | |
| Other CPE / Navigator PVCs, and whether each is needed for restore | |
| Delay before a deleted document's content file disappears | |
| Does §4.6 produce the expected broken document? | |

These answers are the input to Step 1 of the blueprint workflow: choosing the backup pattern.
