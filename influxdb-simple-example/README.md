# InfluxDB with Kasten — stop, snapshot, start

Back up a **single-replica InfluxDB OSS** `Deployment` (1.x, or 3 Core with the `file` object
store) with a clean on-disk state. Neither version has a command that pauses writes, so the
blueprint stops InfluxDB (scale to 0) just before Kasten snapshots its PVC and starts it again
afterwards: 20–35 seconds of downtime, and a restore that needs no script. A watchdog Job starts InfluxDB again if a cancelled backup does not.

The concept deck: [influxdb-simple.pptx](influxdb-simple.pptx).

## Pick the folder for your InfluxDB version

| InfluxDB | Folder | Kubernetes | Kasten | Tool image | Status |
|---|---|---|---|---|---|
| 1.11.8 OSS | [1.11/](1.11/) | 1.34.1 | 9.0.8 | `kasten-tools:9.0.8` | Validated end to end (Steps 1–6), restore from export |
| 3.12.3 Core | [3.12/](3.12/) | 1.34.1 | 9.0.8 | `kasten-tools:9.0.8` | Validated end to end (Steps 1–6), restore from export. `sync`-only variant tested and rejected |

A folder is a **record of what was validated** on that version. It is not changed when work moves
to another version. To add a version, copy the nearest folder, adapt it, and re-run Steps 2–5 of
[AGENTS.md](../AGENTS.md).

The blueprint, the scripts and the binding are **identical** in both folders: they only scale a
Deployment. One installation serves InfluxDB 1.x and 3 Deployments side by side.

| | 1.11 | 3.12 Core |
|---|---|---|
| Storage | TSM files + WAL | WAL + Parquet + snapshot and catalog files |
| Downtime measured | ~20 s | 24–35 s (WAL replay at start) |
| Backup without stopping (`sync` only) | Not tested | **Rejected**: 5 of 12 restored copies did not start |
| Native backup tool | `influxd backup -portable` | None |

InfluxDB 2.x was not tested.

## When not to use this blueprint

| If you need | Use instead |
|---|---|
| No write downtime at all | On 1.x: a blueprint based on `influxd backup -portable` (pattern 3), which backs up online. On 3 Core: no tested option |
| Restore of a single database | On 1.x: `influxd restore -portable -db` restores one database |
| More than one replica | InfluxDB OSS cannot run more than one replica on one data volume; this blueprint assumes one |
