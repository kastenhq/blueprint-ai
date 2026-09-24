# kasten-tools — kanister-tools + kubectl + jq (shared tool image)
#
# This is the SINGLE canonical Dockerfile for the kasten-tools image reused by many
# blueprints in this repo. Do not copy it into individual blueprint folders — that
# reintroduces version drift. Reference this file from each blueprint README instead.
#
# Base: gcr.io/kasten-images/kanister-tools (Kasten's standard tool image, RHEL-based)
# Added: kubectl (cross-namespace cluster operations)
#        jq     (JSON parsing of database / HTTP API responses)
#        tar, gzip (already present in base — re-stated for clarity)
# The base already ships curl.
#
# KubeTask pods using this image MUST run in the kasten-io namespace so they inherit
# the Kasten service account, which has cross-namespace RBAC.
#
# Published as: ghcr.io/kastenhq/blueprint-ai/kasten-tools:<kasten-version>
# Built and pushed automatically by .github/workflows/kasten-tools-image.yaml, which
# derives the published tag from ARG KASTEN_VERSION below.
#
# The image tag MUST match the Kasten version the blueprint was tested against
# (detect with `helm ls -n kasten-io`). To build one tag yourself — for an air-gapped
# cluster, or for a Kasten version this repo has not published:
#
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     --build-arg KASTEN_VERSION=<kasten-version> \
#     --build-arg KUBECTL_VERSION=<k8s-version> \
#     -t <your-registry>/kasten-tools:<kasten-version> --push .
#
# Always pass --build-arg explicitly so the tag matches the target cluster; the ARG
# defaults below only track the most recently tested version. See README.md.

ARG KASTEN_VERSION=9.0.6
FROM gcr.io/kasten-images/kanister-tools:${KASTEN_VERSION}

ARG KUBECTL_VERSION=1.32.0
ARG TARGETARCH

USER root
RUN microdnf install -y tar gzip jq && microdnf clean all \
    && curl -LO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl
USER 1001

ENTRYPOINT ["/bin/sh"]
