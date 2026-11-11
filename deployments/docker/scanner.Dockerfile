# Gate-defining file (ADR-0016 rule 7). GORA's own scanner image (ADR-011, amendment
# 2026-11-07): Grype, pinned by the digest of its index, and the vulnerability database it
# matches against, downloaded when this image is built. The digest of the image pins the tool and
# the data that were tested together: the database schema is tied to the Grype version.
#
# Built by a person, from main, by .github/workflows/scanner.yml, and pushed to
# ghcr.io/gora-tools/gora-scanner by digest. A gate names it by that digest. A bump of the Grype
# digest below, which Dependabot (docker) proposes, is applied by running scanner.yml on main and
# moving the digest the gate names by hand, as for the base image (base.Dockerfile). It builds
# each platform natively, with no layer cache (ADR-021).
#
# The scan runs with no network: Grype's database update, its database age check and its check
# for a newer Grype are off in the image (the ENV below), and Grype reads the database in the
# image. Freshness is a property of the digest, which a pull request moves, and not of a pull
# request.
#
# Anchore's image is built from scratch: Grype and its CA certificates, no shell. The commands
# below are therefore in exec form, and the image has no shell either.

FROM ghcr.io/anchore/grype:v0.121.3@sha256:13113eeceab017260370cf540e1d25442c59665fd07d1b09b66d63bb0c282887

# Where the database lives in the image (GRYPE_DB_CACHE_DIR). Set before the download so that
# the download goes there, and kept so that the scan reads it from there.
ENV GRYPE_DB_CACHE_DIR=/grype-db

# The only step that reaches the network: the download of the database, whose checksum Grype
# checks against the one its listing publishes. The build fails if the download or the check
# fails, and the status after it fails if what is installed is not a valid database.
RUN ["/grype", "db", "update"]
RUN ["/grype", "db", "status"]

# Offline from here on, documented in Grype's own configuration (`grype config`, v0.121.2):
#   GRYPE_DB_AUTO_UPDATE         "check for database updates on execution"
#   GRYPE_DB_VALIDATE_AGE        "ensure db build is no older than the max-allowed-built-age"
#   GRYPE_CHECK_FOR_APP_UPDATE   "enable/disable checking for application updates on startup"
# GRYPE_DB_VALIDATE_BY_HASH_ON_START stays on (its default): each run checks the database
# against the hash it was installed with.
ENV GRYPE_DB_AUTO_UPDATE=false \
    GRYPE_DB_VALIDATE_AGE=false \
    GRYPE_CHECK_FOR_APP_UPDATE=false

# The data sources of the database and the terms each is published under, as far as they are
# known: the attribution travels with the data.
COPY deployments/docker/scanner-data-sources.md /usr/share/doc/gora-scanner/DATA-SOURCES.md

LABEL org.opencontainers.image.source="https://github.com/gora-tools/gora" \
      org.opencontainers.image.description="GORA's scanner image: Grype and its vulnerability database, offline at run time"
