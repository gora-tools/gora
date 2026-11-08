# Data sources of the vulnerability database in this image

GORA's scanner image (`deployments/docker/scanner.Dockerfile`) carries the vulnerability
database that Anchore's Grype downloads, built by Anchore from the sources below. The image
carries this file so that the attribution travels with the data.

## What was found

The list of sources is Anchore's data-sources page for Grype, read on 2026-11-07:
<https://oss.anchore.com/docs/reference/grype/data-sources/>. It names each source and the
provider of Anchore's `vunnel` that fetches it. **It states no licence or terms of use for any
source, and none for the combined database.** Anchore's guide to the database says only that
Anchore "collates vulnerability data from common feeds, and publishes that data online, at no
cost to users"
(<https://oss.anchore.com/docs/guides/vulnerability/database/>), which is not a licence.
`vunnel` and `grype-db`, the code that builds the database, are Apache-2.0; that is the licence
of the code and not of the data.

The terms of each source are the source's own, published by it, and **were not found on the
pages above.** For three sources (GitHub Security Advisories, the Debian Security Tracker and
NVD) the source's own pages were read as well, on 2026-11-07; the column below quotes what they
say and links them. Wherever it says "not found", no page read states terms for that source,
and none is stated here from memory.

## The sources

| Source | Provider | Upstream named by Anchore | Terms |
| ------ | -------- | ------------------------- | ----- |
| AlmaLinux OSV Database | `alma` | <https://github.com/AlmaLinux/osv-database> | not found |
| Alpine SecDB | `alpine` | <https://secdb.alpinelinux.org> | not found |
| Amazon Linux Security Center | `amazon` | <https://alas.aws.amazon.com/> | not found |
| Microsoft AzureLinux OVAL | `mariner` | <https://github.com/microsoft/AzureLinuxVulnerabilityData> | not found |
| Microsoft CBL-Mariner OVAL | `mariner` | <https://github.com/microsoft/AzureLinuxVulnerabilityData> | not found |
| Bitnami Vulnerability Database | `bitnami` | <https://github.com/bitnami/vulndb> | not found |
| Chainguard Security | `chainguard` | <https://packages.cgr.dev/chainguard/security.json> | not found |
| Chainguard Libraries (OpenVEX) | `chainguard_libraries` | <https://libraries.cgr.dev/openvex/v1/all.json> | not found |
| Debian Security Tracker | `debian` | <https://security-tracker.debian.org/tracker/> | not found: the tracker's own page says only that "Everything in the Debian Security Tracker is publicly available" ([about](https://security-team.debian.org/security_tracker.html)), which names no licence or terms |
| ECHO Security | `echo` | <https://advisory.echohq.com/data.json> | not found |
| GitHub Security Advisories | `github` | <https://github.com/advisories> | CC-BY 4.0: "This project is licensed under the terms of the CC-BY 4.0 open source license", [README of the advisory database](https://github.com/github/advisory-database), which links the [full terms](https://docs.github.com/en/github/site-policy/github-terms-for-additional-products-and-features#12-advisory-database) |
| MINIMOS Security | `minimos` | <https://packages.mini.dev/advisories/secdb/security.json> | not found |
| National Vulnerability Database (NVD) | `nvd` | <https://nvd.nist.gov/developers/vulnerabilities> | not found: NVD's [legal disclaimer](https://nvd.nist.gov/general/legal-disclaimer), [FAQ](https://nvd.nist.gov/general/FAQ-Sections/General-FAQs) and [terms of use](https://nvd.nist.gov/developers/terms-of-use) pages returned no text when read on 2026-11-07, so their terms are unread |
| Oracle Linux Security | `oracle` | <https://linux.oracle.com/security/> | not found |
| Red Hat Security Data API | `rhel` | <https://access.redhat.com/documentation/en-us/red_hat_security_data_api/> | not found |
| SUSE Security OVAL | `sles` | <https://ftp.suse.com/pub/projects/security/oval/> | not found |
| Ubuntu CVE Tracker | `ubuntu` | <https://git.launchpad.net/ubuntu-cve-tracker> | not found |
| Wolfi Security | `wolfi` | <https://packages.wolfi.dev/os/security.json> | not found |
| EPSS (auxiliary) | `epss` | <https://www.first.org/epss/> | not found |
| CISA Known Exploited Vulnerabilities (auxiliary) | `kev` | <https://www.cisa.gov/known-exploited-vulnerabilities-catalog> | not found |

The list is that of the page on the date above; the database in an image is the one Anchore
published when the image was built, and `grype db providers` in the image names the providers it
holds.
