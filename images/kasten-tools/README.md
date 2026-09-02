# kasten-tools — shared tool image

`ghcr.io/kastenhq/blueprint-ai/kasten-tools` is Kasten's standard
`gcr.io/kasten-images/kanister-tools` image plus `kubectl` and `jq`. It is used by the
`KubeTask` / `KubeExec` phases of many blueprints in this repo (CNPG, CockroachDB, Couchbase,
Elasticsearch, Grafana Mimir, MariaDB, PostgreSQL, PSMDB, pvc-info, and so on).

This directory holds the **single canonical Dockerfile** for that image. Individual blueprint
folders reference [`Dockerfile`](Dockerfile) with a relative link rather than keeping their own
copy, so there is one source of truth and no version drift. See
[AGENTS.md](../../AGENTS.md) → *Step 4* for the rule.

## What it adds

| Tool | Why |
|---|---|
| `kubectl` | cross-namespace cluster operations (the base image has no `kubectl`) |
| `jq` | parse JSON from database / HTTP API responses |
| `tar`, `gzip` | present in the base already; installed explicitly for clarity |
| `curl` | already in the base image |

`KubeTask` pods using this image **must run in the `kasten-io` namespace** so they inherit the
Kasten service account and its cross-namespace RBAC.

## Published tags

**Each blueprint pins the Kasten version it was tested against**, so this image is published as
one tag per referenced version rather than a single moving tag.
[`.github/workflows/kasten-tools-image.yaml`](../../.github/workflows/kasten-tools-image.yaml)
scans the blueprint manifests for every `kasten-tools:<version>` reference and builds one
multi-arch image (`linux/amd64`, `linux/arm64`) per version.

That makes the tag list **self-maintaining**: reference `kasten-tools:8.6.2` in a new blueprint and
the workflow builds and publishes `8.6.2` on merge, with no change to the workflow or this file.
Previously published tags stay available, so blueprints pinned to an older Kasten version keep
working.

| Tag | Used by |
|---|---|
| `8.5.2` | elasticsearch-eck, mariadb-community-operator-standalone, postgres-windows, pvc-info |
| `8.5.4` | cnpg, cnpg-barman, couchbase-operator, psmdb-percona-operator, psmdb-percona-operator-pbm |
| `8.5.7` | elasticsearch-eck-minio |
| `8.5.9` | cnpg-barman-cloud, cockroachdb |
| `8.5.13` | grafana-mimir |
| `9.0.4` | aerospike-abs, tanzu-postgres |

`ARG KASTEN_VERSION` in the `Dockerfile` is the **most recently tested** version. It is always
built even if no blueprint references it, and it is the only version also tagged `latest`. There is
no `sha-<commit>` tag — with one build per version, a commit-scoped tag would be ambiguous.

> **The tag must equal the Kasten version in the blueprint's own README versions table.** A
> blueprint documenting Kasten `8.5.7` while pulling `kasten-tools:8.5.2` is a bug: the tool image
> would not match the Kasten release it runs beside. The same applies to any direct
> `gcr.io/kasten-images/kanister-tools:<version>` reference.

## Building it yourself

You do **not** need to build this image to use the blueprints — the tags above are public and
pullable anonymously. Build it yourself when you are **air-gapped**, when your cluster runs a
Kasten version this repo has not published, or when policy requires images from your own registry.

The image tag must match the Kasten version the blueprint runs against. Detect it first:

```bash
helm ls -n kasten-io          # Helm install
# or, OpenShift OLM:
kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'
```

Then build and push to **your own** registry:

```bash
cd images/kasten-tools
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg KASTEN_VERSION=9.0.4 \
  --build-arg KUBECTL_VERSION=1.32.0 \
  -t <your-registry>/kasten-tools:9.0.4 --push .
```

Always pass `--build-arg` explicitly rather than relying on the `Dockerfile` defaults — the
defaults only track the most recently tested version. `KUBECTL_VERSION` should match the
Kubernetes version in the consuming blueprint's versions table.

> Pushing to `ghcr.io/kastenhq/blueprint-ai/...` will fail with `unauthorized` — that registry is
> owned by this repo's CI. Substitute your own registry, as above.

## Retargeting the blueprints at your registry

The registry prefix appears **only** in image fields, so one substitution covers the whole repo:

```bash
grep -rl 'ghcr.io/kastenhq/blueprint-ai' *-example | \
  xargs sed -i '' 's|ghcr.io/kastenhq/blueprint-ai|<your-registry>|g'
```

Run the `grep -rl` alone first to see exactly which files will change. (On Linux, use
`sed -i` without the `''`.)

For keeper manifests, prefer kustomize's `images:` transformer over editing YAML — one place to
change, and it survives a `git pull`:

```yaml
# kustomization.yaml
resources:
  - keeper.yaml
images:
  - name: ghcr.io/kastenhq/blueprint-ai/kasten-tools
    newName: <your-registry>/kasten-tools
    newTag: "9.0.4"
```

If you run an air-gapped cluster, retargeting at the **cluster** level is usually better than
editing files at all — containerd `registry.mirrors`, a mutating admission policy, or on OpenShift
an `ImageDigestMirrorSet` will rewrite the registry for every blueprint with no local edits.

## Runtime configurability (escape hatch)

If you need the registry and version to be settable without touching any file, a blueprint can
read them from a ConfigMap through Kanister's `objects`:

```yaml
- func: KubeTask
  name: dumpPhase
  objects:
    imgcfg:
      kind: ConfigMap
      name: blueprint-image-config
      namespace: kasten-io
  args:
    image: "{{ .Phases.dumpPhase.ConfigMaps.imgcfg.Data.registry }}/kasten-tools:{{ .Phases.dumpPhase.ConfigMaps.imgcfg.Data.kastenVersion }}"
```

One ConfigMap in `kasten-io` then governs every blueprint. The blueprints in this repo do **not**
use this pattern — it adds four lines to every phase, and the template expression embeds the
phase's own name, so a copy-pasted phase breaks silently. It is documented here for the cases
where runtime configurability is worth that cost.
