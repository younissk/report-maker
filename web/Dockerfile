# syntax=docker/dockerfile:1.7
#
# report-maker web — the whole deployment.
#
#     docker build -f web/Dockerfile -t report-maker-web .
#
# The build context is the REPOSITORY ROOT, not `web/`: the server is a front
# end over `bin/report-maker`, so the image has to carry the engine as well.
# `docker compose up` at the root does this for you.
#
# Three stages, and the split is deliberate:
#
#   client   node builds the frontend, and node stays here
#   typst    curl fetches a pinned, checksummed typst, and curl stays here
#   runtime  python, the engine, the server, the built client, and that is all
#
# The final image has **no node, no npm and no curl**. That is not tidiness —
# `web/README.md` item 4 explains that with `RM_WEB_DIAGRAMS` unset the server
# already strips node from every engine subprocess `PATH`, and mermaid is the
# largest attack surface in the tool. Leaving the runtime out of the image is
# what makes "diagrams are off by default" a fact about the container rather
# than a setting somebody can flip by accident. Turning diagrams on here means
# building a different image, on purpose, which is the correct amount of
# friction for a headless Chrome per render.


# ── the frontend ─────────────────────────────────────────────────────────────
#
# `npm ci` and not `npm install`: the lockfile is the point. A frontend that
# resolves a different dependency tree on every build has no business inside a
# project whose thesis is that evidence is reproducible.

FROM node:22-bookworm-slim AS client

WORKDIR /build

# The manifests alone first, so the (slow) install layer is cached against the
# lockfile rather than against every edit to a component.
COPY web/client/package.json web/client/package-lock.json ./
RUN npm ci

COPY web/client/ ./
RUN npm run build && test -f dist/index.html


# ── typst ────────────────────────────────────────────────────────────────────
#
# PINNED, and verified against a checksum recorded here.
#
# A document is a rendering, not a value: two typst releases can paginate,
# hyphenate or lay a table out differently, and the colophon this engine writes
# records which one compiled a report precisely because "it looks different now"
# has to be answerable. An image that fetched `latest` would move that answer
# under the operator without telling anybody — which contradicts the one thing
# this project is for.
#
# To move the version you must change BOTH the version and its checksum. That
# is on purpose: overriding `--build-arg TYPST_VERSION=` alone fails the
# `sha256sum -c`, loudly, at build time. Recompute with:
#
#     curl -fsSL -o t.tar.xz \
#       https://github.com/typst/typst/releases/download/v<V>/typst-<arch>-unknown-linux-musl.tar.xz
#     sha256sum t.tar.xz
#
# The musl builds are statically linked, so they run unchanged on the
# glibc-based `python:3.12-slim` below.

FROM debian:bookworm-slim AS typst

ARG TYPST_VERSION=0.15.1
ARG TYPST_SHA256_AMD64=a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c
ARG TYPST_SHA256_ARM64=5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee

# Supplied by BuildKit; `amd64` or `arm64` for the platforms this image targets.
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) arch=x86_64;  sha="${TYPST_SHA256_AMD64}" ;; \
      arm64) arch=aarch64; sha="${TYPST_SHA256_ARM64}" ;; \
      *) echo "no pinned typst build for TARGETARCH=${TARGETARCH};" \
              "add its checksum to web/Dockerfile" >&2; exit 1 ;; \
    esac; \
    name="typst-${arch}-unknown-linux-musl"; \
    curl -fsSL --retry 3 --retry-delay 2 \
      -o /tmp/typst.tar.xz \
      "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${name}.tar.xz"; \
    printf '%s  /tmp/typst.tar.xz\n' "${sha}" | sha256sum -c -; \
    tar -xJf /tmp/typst.tar.xz -C /tmp; \
    install -m 0755 "/tmp/${name}/typst" /usr/local/bin/typst; \
    rm -rf /tmp/typst.tar.xz "/tmp/${name}"; \
    typst --version | grep -q "${TYPST_VERSION}"


# ── the server ───────────────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="report-maker web" \
      org.opencontainers.image.description="Evidence-grade reports in a browser. No account, no install." \
      org.opencontainers.image.source="https://github.com/younissk/report-maker" \
      org.opencontainers.image.licenses="NOASSERTION"

# `tini` is here because the container's PID 1 is a reaper, not a detail.
# MEASURED, in this image, with `web/tests/test_engine_bridge.py`:
#
#     python3 as PID 1   Deadlines/Streaming — 2 failed
#                        "grandchild 33 outlived the abandoned stream"
#     under tini         8 passed
#
# The server enforces its 60-second engine deadline by killing the process
# *group*, and the thing that has to die is a grandchild: server → report-maker
# → typst. With no init to reap and no init semantics on PID 1 the grandchild
# survives, so the deadline — which is the quota's teeth, and the whole answer
# to "somebody submitted a build that never ends" — silently stops being
# enforced the moment the code is containerised. `docker run --init` fixes it
# too, but fly.io and Render do not run `docker run`, so it belongs in the
# image where nothing can forget it.
#
# `git` is here for GitHub mode — clone, and `report-maker sync`.
# `ca-certificates` is here for `cite`, which fetches the pages it archives.
# The fonts are a courtesy: typst carries its own fallbacks, so a report always
# renders, but the shipped brand pack names macOS families (Helvetica Neue,
# Didot, Menlo) that no Linux box has. Liberation and DejaVu give an operator
# real families to name in `brand/brand.json` and widen the glyph coverage
# typst falls back through. Nothing here aliases one name to another — a font
# that silently stands in for another font is a layout that lies about itself.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        tini \
        fonts-liberation \
        fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*

COPY --from=typst /usr/local/bin/typst /usr/local/bin/typst

# ── where things live, and why the paths are long ────────────────────────────
#
# `/opt/report-maker` and `/var/lib/report-maker`, not `/app` and `/data`.
#
# That is not a style preference; a short path here corrupts the files it is
# supposed to protect. `web/server/app.py::_scrub` takes this machine's layout
# out of every JSON body by substring-replacing the engine checkout and
# `RM_WEB_ROOT` — correctly, because a typst error names the file it was
# compiling and a vault path carries the session id. But it walks *every*
# string in the payload, and `GET /api/reports/:id/file` returns the report's
# own source as one of them. Measured, in this image, with the obvious paths:
#
#     on disk   #import "/.build/design/base/data.typ": *
#     over HTTP #import "/.build/design/base….typ": *
#
# `/data` matched inside `/data.typ`. The editor shows that, and saving it
# writes the corruption back over the report. `/app` does the same to any
# report that mentions a URL path or a directory called `app`.
#
# Long, project-specific prefixes make the collision implausible rather than
# merely unlikely, which is the most a path can do about it. The real fix is
# server-side — a file's text is the user's own bytes and belongs on the
# passthrough side of that function, beside the PDF and the page images — and
# is recorded in the handover. Until it lands, do not shorten these.
ARG RM_HOME=/opt/report-maker
ARG RM_DATA=/var/lib/report-maker

# Non-root, and a real home: `git` writes into `$HOME`, and a process whose
# HOME is unset behaves differently from the one you tested.
ARG UID=10001
ARG GID=10001
RUN set -eux; \
    groupadd --system --gid "${GID}" report; \
    useradd  --system --uid "${UID}" --gid "${GID}" \
             --create-home --home-dir /home/report --shell /usr/sbin/nologin report

WORKDIR ${RM_HOME}

# Only what the server needs to answer a request. `app/` (Electron),
# `examples/` (the demo vault) and `tests/` are deliberately absent: a session
# vault is seeded by `report-maker init` and `report-maker new`, which read the
# starters out of `engine/templates/`, so nothing in the image depends on the
# sample vault. See `.dockerignore` at the root, which is the file docker
# actually reads for this build.
COPY bin/            ${RM_HOME}/bin/
COPY engine/         ${RM_HOME}/engine/
COPY web/__main__.py ${RM_HOME}/web/__main__.py
COPY web/server/     ${RM_HOME}/web/server/
COPY --from=client /build/dist ${RM_HOME}/web/client/dist

# `RM_WEB_ROOT` is the store: `sessions/<id>/vault/` and `shares/<token>.html`.
# Mount a volume here, or a restart takes every open session and — worse —
# every share link that was ever handed out. 0700 because a session vault is a
# stranger's files.
RUN set -eux; \
    mkdir -p "${RM_DATA}"; \
    chown report:report "${RM_DATA}"; \
    chmod 0700 "${RM_DATA}"

# ARGs do not survive into the running container, so the two paths are restated
# as ENV. Keep them in step with the ARGs above.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/report \
    RM_WEB_ROOT=/var/lib/report-maker \
    RM_WEB_CLIENT=/opt/report-maker/web/client/dist \
    RM_WEB_PORT=8787

USER report

EXPOSE 8787

# No curl in this image, so the probe is the interpreter that is already here.
# `/api/health` answers `{ok, version, diagrams, github}` and never a path, so
# a 200 from it says the engine was found without publishing where it lives.
#
# It reads RM_WEB_PORT rather than hard-coding 8787, because Render injects a
# port and a healthcheck that quietly probes the wrong one reports a dead
# container as healthy — the one failure mode a healthcheck must not have.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python3", "-c", "import os,sys,urllib.request; p=os.environ.get('RM_WEB_PORT') or '8787'; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=4).status == 200 else 1)"]

# `--host 0.0.0.0` is correct HERE and essentially nowhere else. The server
# binds loopback by default and prints a warning naming what an exposed port
# can do; inside a container loopback would mean nobody can reach it, and the
# publish decision belongs to whatever runs the container. `docker-compose.yml`
# binds the host side to 127.0.0.1 for exactly that reason.
#
# The warning still prints on every start. Read it once — it is true of your
# deployment, not of the container.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "web", "--host", "0.0.0.0"]
