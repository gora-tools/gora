"""GORA CLI — primary local interface for release operations."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
import typer
from pydantic import ValidationError
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text
from typer.core import TyperGroup

from gora.audit.sink import JournalPathError, attach, resolve_journal
from gora.config.settings import get_settings
from gora.domain.release.manifest import ManifestLoadError, ReleaseManifest, StageState
from gora.services.notifier import Notifier, build_notifier
from gora.services.release import ReleaseService
from gora.text import visible


@contextmanager
def _visible_errors() -> Iterator[None]:
    """A click error raised inside is shown escaped, wherever its message quotes input."""
    try:
        yield
    except click.ClickException as exc:
        exc.message = visible(exc.message)
        raise


class _Group(TyperGroup):
    """Usage errors quote the arguments that caused them; those are shown escaped too."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        with _visible_errors():
            name = None if info_name is None else visible(info_name)
            return super().make_context(name, args, parent, **extra)

    def invoke(self, ctx: click.Context) -> Any:
        with _visible_errors():
            return super().invoke(ctx)


app = typer.Typer(
    name="gora",
    help="GORA - Deterministic GitOps Release Engine",
    no_args_is_help=True,
    cls=_Group,
)
# Plain text: no markup, emoji codes or highlighting is read from what is printed, and
# no line is wrapped. Style is applied only through _print, never parsed from a value.
console = Console(soft_wrap=True, markup=False, emoji=False, highlight=False)
err_console = Console(stderr=True, soft_wrap=True, markup=False, emoji=False, highlight=False)

DEFAULT_MANIFEST = Path("release-manifest.yaml")

# Exit codes (ADR-0017): 0 ok, 1 refused or failed, 2 usage, 3 completed with incomplete audit.
EXIT_AUDIT_INCOMPLETE = 3


def _print(*parts: str | tuple[str, str], style: str = "", err: bool = False) -> None:
    """Print one line built from literal parts, a part optionally ``(text, style)``.

    Every part goes through ``visible``, so nothing in it can move the cursor, break
    the line, or change how the terminal renders; GORA's own text contains no control.
    Results go to stdout; ``err=True`` sends the line to stderr.
    """
    shown = [
        visible(part) if isinstance(part, str) else (visible(part[0]), part[1]) for part in parts
    ]
    (err_console if err else console).print(Text.assemble(*shown, style=style))


def _notify(emit: Callable[[Notifier], object]) -> None:
    """Best-effort Slack notification. Never fails the release (Git is truth).

    Everything — settings load, notifier build, and the emit — is guarded: a
    malformed ``GORA_*`` env var or a Slack failure must never fail a release
    that Git has already persisted.
    """
    try:
        notifier = build_notifier(get_settings())
        if not notifier.active:
            return
        emit(notifier)
    except Exception as exc:  # notifications are best-effort
        _print(f"  (slack notify failed: {exc})", style="yellow", err=True)


@contextmanager
def _audited() -> Iterator[None]:
    """Run a mutating command with the audit journal open (ADR-0017).

    The journal is opened before anything else, and the same descriptor is held until the
    command ends. If it cannot be opened the command stops here: exit 1, nothing written,
    no remote call. A write that fails later never aborts the sequence; the events it
    could not record are printed to stderr, one JSON line each, and a command that
    completed then exits 3.
    """
    try:
        target = resolve_journal(os.environ)
    except JournalPathError as exc:
        _print(str(exc), style="red", err=True)
        raise typer.Exit(code=2) from exc
    try:
        attached = attach(target)
    except (OSError, ValueError) as exc:
        reason = (exc.strerror if isinstance(exc, OSError) else None) or str(exc)
        _print(f"Cannot open the audit journal {target.path}: {reason}", style="red", err=True)
        _print("  Nothing was changed: no manifest write, no remote call.", style="red", err=True)
        raise typer.Exit(code=1) from exc

    journal = attached.journal
    try:
        yield
    except typer.Exit as exc:
        _report_unrecorded(journal.unrecorded, completed=exc.exit_code == 0)
        if exc.exit_code == 0 and journal.unrecorded:
            raise typer.Exit(code=EXIT_AUDIT_INCOMPLETE) from exc
        raise
    except BaseException:
        _report_unrecorded(journal.unrecorded, completed=False)
        raise
    else:
        _report_unrecorded(journal.unrecorded, completed=True)
        if journal.unrecorded:
            raise typer.Exit(code=EXIT_AUDIT_INCOMPLETE)
    finally:
        attached.detach()


def _report_unrecorded(events: list[str], *, completed: bool) -> None:
    """Say how many events missed the journal, then print each in full on stderr.

    An event line is printable ASCII (see ``AuditLogger``), so it is written as it is: it
    must stay valid JSON, which escaping it for display would break.
    """
    if not events:
        return
    n = len(events)
    noun = "event was" if n == 1 else "events were"
    lead = "The operation completed." if completed else "The operation did not complete."
    _print(f"{lead} {n} {noun} not recorded in the audit journal:", style="red", err=True)
    for line in events:
        click.echo(line, err=True)


def _refuse(exc: ManifestLoadError) -> typer.Exit:
    """Report a manifest this engine cannot load; the file is left untouched."""
    _print(str(exc), style="red", err=True)
    _print("  Refusing to read or write it; the file is unchanged.", style="red", err=True)
    return typer.Exit(code=1)


@app.command()
def status(
    manifest: Path = typer.Option(
        DEFAULT_MANIFEST,
        "--manifest",
        "-m",
        help="Path to release manifest file",
    ),
) -> None:
    """Show current release state from manifest."""
    if not manifest.exists():
        _print(f"No manifest found at {manifest}", style="yellow", err=True)
        _print("Run ", ("gora cut-rc", "bold"), " to create a release candidate.", err=True)
        raise typer.Exit(code=1)

    try:
        release = ReleaseManifest.from_yaml(manifest)
    except ManifestLoadError as exc:
        raise _refuse(exc) from exc

    # Header
    console.print()
    _print(("Manifest:", "bold"), f" {release.version.value}")
    _print(("Release:", "bold"), f"  {release.release}")

    # Candidate
    if release.candidate:
        _print(("Candidate:", "bold"), f" {release.candidate.version}")
        _print(f"  digest: {release.candidate.digest}", style="dim")
        _print(f"  image:  {release.candidate.image}", style="dim")
    else:
        _print("No active release candidate", style="yellow")

    # Stages
    if release.stages:
        console.print()
        _print_stages(release.stages)
    else:
        _print("No stages defined", style="yellow")

    console.print()


def _print_stages(stages: dict[str, StageState]) -> None:
    """The stages as aligned columns, one line per stage, however long a value is."""
    header = ("Stage", "Version", "Digest")
    rows = [(name, state.version, state.digest) for name, state in stages.items()]
    # Widths are measured on what _print shows, so an escaped character keeps the columns
    # aligned. Cells stay raw: _print escapes them, and escaping twice would double a `\`.
    widths = [
        max(cell_len(visible(cell)) for cell in column)
        for column in zip(header, *rows, strict=True)
    ]

    def line(cells: tuple[str, ...], styles: tuple[str, ...]) -> None:
        parts: list[str | tuple[str, str]] = []
        for i, (cell, width, cell_style) in enumerate(zip(cells, widths, styles, strict=True)):
            last = i == len(cells) - 1
            pad = "" if last else " " * (width - cell_len(visible(cell)) + 2)
            parts += [(cell, cell_style), pad]
        _print(*parts)

    _print("Stages", style="italic")
    line(header, ("bold", "bold", "bold"))
    for row in rows:
        line(row, ("cyan", "green", "dim"))


@app.command()
def cut_rc(
    image: str = typer.Option(
        ...,
        "--image",
        "-i",
        help="Image reference without tag/digest, e.g. ghcr.io/org/service",
    ),
    version: str = typer.Option(
        ...,
        "--version",
        "-v",
        help="Release candidate identity, e.g. 1.2.0-rc.3",
    ),
    digest: str = typer.Option(
        ...,
        "--digest",
        "-d",
        help="Immutable artifact digest (sha256:...)",
    ),
    release: str | None = typer.Option(
        None,
        "--release",
        "-r",
        help="Release line (derived from version if omitted)",
    ),
    manifest: Path = typer.Option(
        DEFAULT_MANIFEST,
        "--manifest",
        "-m",
        help="Path to release manifest file",
    ),
) -> None:
    """Cut a new release candidate from an immutable artifact digest."""
    with _audited():
        _cut_rc(image, version, digest, release, manifest)


def _cut_rc(image: str, version: str, digest: str, release: str | None, manifest: Path) -> None:
    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.cut_rc(
            image=image,
            version=version,
            digest=digest,
            release=release,
        )
    except ManifestLoadError as exc:
        raise _refuse(exc) from exc
    except ValidationError as exc:
        _print("Invalid release candidate:", style="red", err=True)
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            _print("  ", (loc, "red"), f": {err['msg']}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        _print(str(exc), style="red", err=True)
        raise typer.Exit(code=1) from exc

    console.print()
    _print(("Cut release candidate", "green"), " ", (result.candidate.version, "bold"))
    _print(f"  release: {result.release}", style="dim")
    _print(f"  image:   {result.candidate.image}", style="dim")
    _print(f"  digest:  {result.candidate.digest}", style="dim")
    if result.superseded:
        _print(f"  superseded prior candidate: {result.superseded}", style="yellow")
    if result.created:
        _print(f"  created new manifest: {result.manifest_path}", style="dim")
    _notify(lambda n: n.cut_rc(version=result.candidate.version, digest=result.candidate.digest))
    console.print()


@app.command()
def promote(
    stage: str = typer.Argument(..., help="Target stage, e.g. dev, staging, prod"),
    manifest: Path = typer.Option(
        DEFAULT_MANIFEST,
        "--manifest",
        "-m",
        help="Path to release manifest file",
    ),
    open_pr: bool = typer.Option(
        False,
        "--open-pr",
        help="Generate a promotion PR via the GitHub adapter instead of mutating locally",
    ),
    repo: str = typer.Option("", "--repo", help="owner/name (required with --open-pr)"),
    base: str = typer.Option("main", "--base", help="Base branch for the promotion PR"),
) -> None:
    """Promote the active candidate to a target stage (same digest)."""
    with _audited():
        if open_pr:
            _promote_open_pr(stage, manifest, repo, base)
        else:
            _promote_local(stage, manifest)


def _promote_local(stage: str, manifest: Path) -> None:
    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.promote(stage)
    except ManifestLoadError as exc:
        raise _refuse(exc) from exc
    if not result.success:
        _print(str(result.error), style="red", err=True)
        raise typer.Exit(code=1)

    console.print()
    _print(("Promoted to", "green"), " ", (str(result.stage), "bold"))
    _print(f"  version: {result.version}", style="dim")
    _print(f"  digest:  {result.digest}", style="dim")
    _notify(
        lambda n: n.promoted(
            stage=result.stage or "",
            version=result.version or "",
            digest=result.digest or "",
        )
    )
    console.print()


def _promote_open_pr(stage: str, manifest: Path, repo: str, base: str) -> None:
    from gora.adapters.github import GitHubAdapter, GitHubError
    from gora.services.promotion_pr import PromotionPRService

    settings = get_settings()
    if not repo:
        _print("--repo owner/name is required with --open-pr", style="red", err=True)
        raise typer.Exit(code=1)
    if not settings.github_token:
        _print("GORA_GITHUB_TOKEN is required with --open-pr", style="red", err=True)
        raise typer.Exit(code=1)

    adapter = GitHubAdapter(repo, settings.github_token)
    service = PromotionPRService(adapter, manifest_path=manifest)
    try:
        result = service.open_promotion_pr(stage, base=base)
    except ManifestLoadError as exc:
        raise _refuse(exc) from exc
    except (ValueError, GitHubError) as exc:
        _print(str(exc), style="red", err=True)
        raise typer.Exit(code=1) from exc

    verb = "Opened" if result.created else "Reusing"
    console.print()
    _print(
        (f"{verb} promotion PR", "green"),
        f" #{result.pull_request.number} ",
        (result.pull_request.url, "dim"),
    )
    _print(f"  {result.version} to {result.stage}, digest {result.digest}", style="dim")
    _notify(
        lambda n: n.promotion_pr(
            stage=result.stage,
            version=result.version,
            url=result.pull_request.url,
        )
    )
    console.print()


@app.command()
def rollback(
    stage: str = typer.Argument(..., help="Stage to roll back, e.g. prod"),
    to_version: str = typer.Option(
        ...,
        "--to-version",
        help="Known-good version to restore, e.g. 1.1.9",
    ),
    to_digest: str = typer.Option(
        ...,
        "--to-digest",
        help="Known-good artifact digest to restore (sha256:...)",
    ),
    manifest: Path = typer.Option(
        DEFAULT_MANIFEST,
        "--manifest",
        "-m",
        help="Path to release manifest file",
    ),
) -> None:
    """Roll a stage back to a known prior version/digest (no rebuild)."""
    with _audited():
        _rollback(stage, to_version, to_digest, manifest)


def _rollback(stage: str, to_version: str, to_digest: str, manifest: Path) -> None:
    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.rollback(stage, to_version=to_version, to_digest=to_digest)
    except ManifestLoadError as exc:
        raise _refuse(exc) from exc
    except ValidationError as exc:
        _print("Invalid rollback target:", style="red", err=True)
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            _print("  ", (loc, "red"), f": {err['msg']}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.success:
        _print(str(result.error), style="red", err=True)
        raise typer.Exit(code=1)

    console.print()
    _print(("Rolled back", "green"), " ", (str(result.stage), "bold"))
    _print(f"  version: {result.version}", style="dim")
    _print(f"  digest:  {result.digest}", style="dim")
    _notify(
        lambda n: n.rolled_back(
            stage=result.stage or "",
            version=result.version or "",
            digest=result.digest or "",
        )
    )
    console.print()


@app.command()
def version() -> None:
    """Show GORA version."""
    from gora import __version__

    _print(f"gora {__version__}")


if __name__ == "__main__":
    app()
