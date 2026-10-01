"""GORA CLI — primary local interface for release operations."""

from collections.abc import Callable
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gora.config.settings import get_settings
from gora.domain.release.manifest import ReleaseManifest, UnsupportedManifestError
from gora.services.notifier import Notifier, build_notifier
from gora.services.release import ReleaseService

app = typer.Typer(
    name="gora",
    help="GORA - Deterministic GitOps Release Engine",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True, soft_wrap=True)

DEFAULT_MANIFEST = Path("release-manifest.yaml")


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
        console.print(f"[yellow]  (slack notify failed: {exc})[/yellow]")


def _refuse(exc: UnsupportedManifestError) -> typer.Exit:
    """Report a manifest this engine cannot represent; the file is left untouched."""
    err_console.print(f"[red]{escape(str(exc))}[/red]")
    err_console.print("[red]  Refusing to read or write it; the file is unchanged.[/red]")
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
        console.print(f"[yellow]No manifest found at {manifest}[/yellow]")
        console.print("Run [bold]gora cut-rc[/bold] to create a release candidate.")
        raise typer.Exit(code=1)

    try:
        release = ReleaseManifest.from_yaml(manifest)
    except UnsupportedManifestError as exc:
        raise _refuse(exc) from exc

    # Header
    console.print(f"\n[bold]Manifest:[/bold] {release.version.value}")
    console.print(f"[bold]Release:[/bold]  {release.release}")

    # Candidate
    if release.candidate:
        console.print(f"[bold]Candidate:[/bold] {release.candidate.version}")
        console.print(f"[dim]  digest: {release.candidate.digest}[/dim]")
        console.print(f"[dim]  image:  {release.candidate.image}[/dim]")
    else:
        console.print("[yellow]No active release candidate[/yellow]")

    # Stages
    if release.stages:
        console.print()
        table = Table(title="Stages")
        table.add_column("Stage", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Digest", style="dim")

        for stage_name, state in release.stages.items():
            table.add_row(stage_name, state.version, state.digest)

        console.print(table)
    else:
        console.print("[yellow]No stages defined[/yellow]")

    console.print()


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
    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.cut_rc(
            image=image,
            version=version,
            digest=digest,
            release=release,
        )
    except UnsupportedManifestError as exc:
        raise _refuse(exc) from exc
    except ValidationError as exc:
        console.print("[red]Invalid release candidate:[/red]")
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            console.print(f"  [red]{loc}[/red]: {err['msg']}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"\n[green]Cut release candidate[/green] [bold]{result.candidate.version}[/bold]")
    console.print(f"[dim]  release: {result.release}[/dim]")
    console.print(f"[dim]  image:   {result.candidate.image}[/dim]")
    console.print(f"[dim]  digest:  {result.candidate.digest}[/dim]")
    if result.superseded:
        console.print(f"[yellow]  superseded prior candidate: {result.superseded}[/yellow]")
    if result.created:
        console.print(f"[dim]  created new manifest: {result.manifest_path}[/dim]")
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
    if open_pr:
        _promote_open_pr(stage, manifest, repo, base)
        return

    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.promote(stage)
    except UnsupportedManifestError as exc:
        raise _refuse(exc) from exc
    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[green]Promoted to[/green] [bold]{result.stage}[/bold]")
    console.print(f"[dim]  version: {result.version}[/dim]")
    console.print(f"[dim]  digest:  {result.digest}[/dim]")
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
        console.print("[red]--repo owner/name is required with --open-pr[/red]")
        raise typer.Exit(code=1)
    if not settings.github_token:
        console.print("[red]GORA_GITHUB_TOKEN is required with --open-pr[/red]")
        raise typer.Exit(code=1)

    adapter = GitHubAdapter(repo, settings.github_token)
    service = PromotionPRService(adapter, manifest_path=manifest)
    try:
        result = service.open_promotion_pr(stage, base=base)
    except UnsupportedManifestError as exc:
        raise _refuse(exc) from exc
    except (ValueError, GitHubError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    verb = "Opened" if result.created else "Reusing"
    console.print(
        f"\n[green]{verb} promotion PR[/green] #{result.pull_request.number} "
        f"[dim]{result.pull_request.url}[/dim]"
    )
    console.print(f"[dim]  {result.version} → {result.stage} @ {result.digest}[/dim]")
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
    service = ReleaseService(manifest_path=manifest)
    try:
        result = service.rollback(stage, to_version=to_version, to_digest=to_digest)
    except UnsupportedManifestError as exc:
        raise _refuse(exc) from exc
    except ValidationError as exc:
        console.print("[red]Invalid rollback target:[/red]")
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            console.print(f"  [red]{loc}[/red]: {err['msg']}")
        raise typer.Exit(code=1) from exc

    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[green]Rolled back[/green] [bold]{result.stage}[/bold]")
    console.print(f"[dim]  version: {result.version}[/dim]")
    console.print(f"[dim]  digest:  {result.digest}[/dim]")
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

    console.print(f"gora {__version__}")


if __name__ == "__main__":
    app()
