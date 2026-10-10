# Contributing

Contributions are welcome under the [Apache License 2.0](LICENSE). Read the
[README](README.md) first, then [the release model](docs/architecture/release-model.md): a
change that contradicts one of its laws is raised with the maintainer before any code is
written, and a decision changes through an ADR in `docs/adr/`.

## Sign off every commit

GORA uses the [Developer Certificate of Origin](https://developercertificate.org/). Sign off
each commit with `git commit -s`, which adds a `Signed-off-by:` line with your name and
email. By signing off you certify that you wrote the change or have the right to submit it
under the project's license.

## Making a change

1. Branch off `main`. `main` is protected: every change is a pull request, squash-merged.
2. Keep one concern in one pull request.
3. Run `make verify` before you push. Its targets and the policy behind them are in
   [TESTING.md](TESTING.md); CI is the gate of record.
4. Write commit messages and the pull request title as
   [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`,
   imperative, lowercase. The squash commit takes the pull request title as its subject.
5. A pull request that adds or changes a check shows the check failing first, in its
   description: the command and its red output, on the code before the change or with the
   defect injected.
6. Leave AI-tool trailers out of commits and pull request text: no `Co-authored-by` for a
   tool and no "Generated with" lines.

Never commit secrets, `.env` files, tokens or private keys.
