# GitHub plans and the host half of GORA's contract

Background, not a decision (CLAUDE.md, "Documents"): what the code host offers on each plan
and visibility, measured against the laws and decisions that need it. Read on 2026-10-24 from
GitHub's documentation for github.com; the sources are at the end. A plan's offer changes;
this file carries the date it was read and is re-read before a decision leans on it.

## What GORA needs from the host, and where it is offered

| Host feature | GORA needs it for | Public, any plan | Private, organization on Team | Private, Enterprise Cloud | Private, personal account on Free |
| --- | --- | --- | --- | --- | --- |
| Rulesets: branch and tag rules, bypass by a GitHub App | L2, L3, L10, L17, L22 (ADR-022) | yes, Free included | yes | yes | **no** |
| Environments, deployment branch policies, environment secrets | L22: the secret only where GORA's writes run | yes | yes (Pro, Team, Enterprise) | yes | **no** |
| Required reviewers and wait timers on an environment | not needed | yes | **no** (Enterprise only) | yes | no |
| Merge queue | L3, "where the host offers one" (ADR-023 section 12) | yes, organization-owned | **no** | yes | no |
| Artifact attestations (provenance signed by the host's Sigstore) | ADR-018: provenance in `workflow/v1`; Build L3 | yes | **no** | yes | no |
| Secret scanning and push protection | the security toggles of GORA's own repository, not a law | free | paid: GitHub Secret Protection | paid | no |
| Code scanning, default setup | not needed | free | paid: GitHub Code Security | paid | no |
| Organization rulesets; "require workflows to pass" | a check required across repositories (gora-infra) | — | yes (Team and Enterprise) | yes | — |
| Dependabot alerts and security updates | the image's dependencies | yes | yes | yes | yes |
| GitHub Apps; OpenID Connect for Actions | L22; CI identities without secrets | yes | yes | yes | yes (Apps) |

## What follows

- **The least a repository needs to connect:** rulesets, an environment and a GitHub App — the
  line between a public or organization-owned repository (any plan for public; Team and up
  for private) and a personal private repository on Free, which cannot hold the laws and
  does not connect. Secret scanning and code scanning are not on that line: no law speaks of
  them, and GORA's own repository turns them on because it is public and they are free.
- **An organization's private repository on Team** holds every law, with two consequences
  the laws already allow for: no merge queue, so L3 is held by required checks in strict
  mode alone; and no artifact attestations, so provenance comes from a key held by the
  builder (Build L2, ADR-018) or from keyless signing through the Sigstore Public Good
  instance, which writes the repository's name into a public log. A Sigstore instance of
  the customer's own is the Enterprise answer (backlog, ADR-027 section 7).
- **Enterprise Cloud** offers everything above for private repositories.
- The module that writes these settings (gora-infra, `modules/github-repository`) runs on all
  three connectable kinds with one code; what differs is an input: the checks, the App, the
  environment's policy, and whether secret scanning is on.

## Sources, read 2026-10-24

- [About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) — "Rulesets are available in public repositories with GitHub Free and GitHub Free for organizations, and in public and private repositories with GitHub Pro, GitHub Team, and GitHub Enterprise Cloud"; organization rulesets "for customers on GitHub Team and GitHub Enterprise plans".
- [Manage environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) — environments, environment secrets and deployment branches in private repositories need Pro, Team or Enterprise; required reviewers and wait timers on Free, Pro or Team "only available for public repositories".
- [Merging a pull request with a merge queue](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-a-pull-request-with-a-merge-queue) — "available in any public repository owned by an organization, [and] in private repositories owned by organizations using GitHub Enterprise Cloud".
- [Use artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations) — on Free, Pro or Team "only available for public repositories"; private or internal repositories need GitHub Enterprise Cloud.
- [Secret scanning scope](https://docs.github.com/en/code-security/reference/secret-security/secret-scanning-scope) — public: "runs automatically for free"; organization-owned private: "with GitHub Secret Protection enabled on GitHub Team or GitHub Enterprise Cloud".
- [Configure code scanning](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configure-code-scanning) — public repositories on GitHub.com; organization-owned repositories on Team or Enterprise with GitHub Code Security.
- [About GitHub's plans](https://docs.github.com/en/enterprise-server@latest/get-started/learning-about-github/githubs-plans) (the Enterprise Server copy; the github.com page refers to github.com/pricing) — Free: unlimited private repositories "with a limited feature set"; Pro and Team: protected branches, required reviewers and code owners in private repositories; Team: "option to purchase GitHub Code Security and GitHub Secret Protection".
