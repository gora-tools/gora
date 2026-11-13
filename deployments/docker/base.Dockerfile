# Gate-defining file (ADR-0016 rule 7). GORA's own base image (ADR-011, amendment
# 2026-11-05): Debian, Python and git, and nothing of GORA. The release image starts from it
# by digest, so that a build of the release image reaches neither Docker Hub nor
# snapshot.debian.org: those are reached here, when the base is rebuilt, by a person running
# .github/workflows/base.yml on main.
#
# Built like the release image (ADR-021): one Dockerfile for linux/amd64 and linux/arm64, each
# built on its own native runner, with no layer cache, from a base pinned by the digest of its
# multi-platform index. Dependabot (docker) proposes that digest.
#
# No uv, no pip, no virtual environment, no user, no GORA code. The release image adds those.

FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# The exact Python of the base: the interpreter of the image above. A base with another one
# fails the build here.
#
# The interpreter carries no installed distribution: pip, which the python image ships and
# nothing of GORA runs, is removed, and the build fails, naming what it finds, if any other
# distribution is there. GORA's packages come only from uv.lock, into /app/.venv, in the release
# image. The stdlib stays whole: ensurepip and its bundled wheel are the interpreter's.
ARG PYTHON_VERSION=3.12.16
RUN got=$(python -c 'import platform; print(platform.python_version())') \
    && if [ "$got" != "$PYTHON_VERSION" ]; then \
         echo "the base image's Python is $got, PYTHON_VERSION declares $PYTHON_VERSION" >&2; \
         exit 1; \
       fi \
    && python -m pip uninstall --yes --root-user-action=ignore pip \
    && python -c 'import importlib.metadata, sys; \
        found = ["%s %s" % (d.metadata["Name"], d.version) for d in importlib.metadata.distributions()]; \
        sys.exit(None if not found else "the base interpreter carries installed distributions: " + ", ".join(sorted(found)))'

# git, which the Git adapter runs at /usr/bin/git (GORA_GIT, ADR-006), and every other Debian
# package of the base, from the Debian snapshot deployments/docker/debian.sources names, which
# replaces the base's sources: the live archive is never read, and the build refuses any other
# apt source. apt-get update fails on any error. snapshot.debian.org serves every file by a
# redirect to its content address, so apt cannot tell an exact import from a time; debian.lock
# can: the Release files apt fetched must be exactly the ones it pins, and they hold the hashes
# of the indices, which hold the hashes of the packages apt checks. Then apt-get upgrade brings
# every package the python image carries to the snapshot's version (it installs no new package),
# git is installed, and a last check fails the build, naming the packages, if apt would still
# upgrade or keep back a package, or if any installed package is at a version no source serves
# (apt list --installed marks it local): every Debian package of the base is the snapshot's.
# So the snapshot must be at least as new as the python image the base starts from: an older
# one fails the build here, which is how the two are held together. GIT_VERSION asserts the
# version the snapshot holds. A security update is taken when the snapshot is moved and, for
# Python, the python digest and PYTHON_VERSION with it (ADR-011, the entry "the base takes
# Debian and Python security updates"; TESTING rule 3).
#
# The snapshot answered 503 and 504 for two days (ADR-011, amendment 2026-11-05), so apt is
# patient here and only here, since this image is built rarely: Acquire::Retries=10 retries a
# failed download ten times, and a connection that says nothing for 60 seconds
# (Acquire::http::Timeout, Acquire::https::Timeout) counts as failed. Retries do not weaken
# the lock: every retry is checked against it.
ARG GIT_VERSION=1:2.47.3-0+deb13u1
COPY deployments/docker/debian.sources /etc/apt/sources.list.d/debian.sources
COPY deployments/docker/debian.lock /etc/apt/debian.lock
RUN test "$(ls -A /etc/apt/sources.list.d)" = debian.sources && test ! -e /etc/apt/sources.list \
    && apt-get update --error-on=any -o Acquire::Retries=10 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 \
    && python -c 'import glob, hashlib, sys; \
        want = sorted(line.split()[0] for line in open(sys.argv[1], encoding="utf-8") if line.strip() and not line.startswith("#")); \
        got = sorted(hashlib.sha256(open(path, "rb").read()).hexdigest() for path in glob.glob("/var/lib/apt/lists/*_InRelease")); \
        sys.exit(None if got == want else "apt fetched other Release files than debian.lock pins: " + " ".join(got))' \
        /etc/apt/debian.lock \
    && apt-get upgrade --yes --no-install-recommends -o Acquire::Retries=10 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 \
    && apt-get install --yes --no-install-recommends -o Acquire::Retries=10 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 "git=$GIT_VERSION" \
    && python -c 'import itertools, subprocess, sys; \
        run = lambda *argv: subprocess.run(argv, check=True, capture_output=True, text=True).stdout.splitlines(); \
        plan = run("apt-get", "--simulate", "upgrade"); \
        left = [line.split()[1] for line in plan if line.startswith("Inst ")]; \
        heading = [n for n, line in enumerate(plan) if line.startswith("The following packages have been kept back")]; \
        kept = " ".join(itertools.takewhile(lambda line: line.startswith(" "), plan[heading[0] + 1:])).split() if heading else []; \
        local = [line.split("/")[0] for line in run("apt", "list", "--installed") if "local" in line.rpartition("[")[2].rstrip("]").split(",")]; \
        print("left to upgrade:", " ".join(left) or "none"); \
        print("kept back:", " ".join(kept) or "none"); \
        print("not served by the snapshot:", " ".join(local) or "none"); \
        sys.exit(None if not (left or kept or local) else "the base holds packages that apt would still change or that the snapshot does not serve: " + " ".join(left + kept + local))' \
    && rm -rf /var/lib/apt/lists/*

# Where the image comes from, and the two imports of the Debian archive it was built from:
# the same ids debian.sources names (tests/unit/test_build_pins.py compares them).
ARG DEBIAN_SNAPSHOT=20261109T145047Z
ARG DEBIAN_SECURITY_SNAPSHOT=20261109T173309Z
LABEL org.opencontainers.image.source="https://github.com/gora-tools/gora" \
      org.opencontainers.image.description="GORA's base image: Debian, Python and git" \
      tools.gora.debian-snapshot=$DEBIAN_SNAPSHOT \
      tools.gora.debian-security-snapshot=$DEBIAN_SECURITY_SNAPSHOT
