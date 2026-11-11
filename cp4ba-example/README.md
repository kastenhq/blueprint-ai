# CP4BA (FileNet and ODM) — Kasten blueprint

Back up an IBM Cloud Pak for Business Automation (CP4BA) deployment with the **Content** capability
(FileNet, renamed IBM Content Cortex in 26.0.0) and the **Decisions** capability (ODM), with
**Kasten as the data mover**. The backup follows IBM's **offline** procedure: a policy-level action
hook stops everything in the CP4BA namespace, Kasten snapshots the PVCs, and the hook starts
everything again.

> ⚠️ Designed for FileNet and ODM only, with their content and databases on PVCs. It does not
> protect external S3 storage areas or external databases. Validated on the **Starter** deployment
> on a test cluster, not on a production installation. Read the warning at the top of the version
> README before you use it.

## Pick the folder for your CP4BA version

What a backup must capture, and how a restore works, change between CP4BA releases: the
components and their PVCs, the PostgreSQL operator, the operators to scale down, and the CR fields
a restore must set. Each version has its **own folder**. Use the folder that matches your
installation, and follow the `README.md` inside it.

| CP4BA | Deployment type | Folder | OpenShift | Kasten | Status |
|---|---|---|---|---|---|
| 26.0.0-IF003 | Starter | [26.0.0/](26.0.0/) | 4.21.30 | 9.0.10 | Backup validated through Kasten; restore and disaster recovery (namespace deleted, same cluster) validated by hand |

A folder is a **record of what was validated** on that version. It is not updated when another
version moves on. If your version is not listed, start from the nearest folder and re-run the
validation (Steps 2–5 of [AGENTS.md](../AGENTS.md)) before you rely on it.

To present the design, use the concept deck [cp4ba.pptx](cp4ba.pptx) (built from
[ppt-tools/deck.py](ppt-tools/deck.py) with `python3 ppt-tools/build.py cp4ba-example`).

See the ownership disclaimer in the [repository README](../README.md).
