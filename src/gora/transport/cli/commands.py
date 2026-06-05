"""GORA CLI — primary local interface for release operations."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from gora.domain.release.manifest import ReleaseManifest
from gora.services.release import ReleaseService

app = typer.Typer(
    name="gora",
    help="GORA - Deterministic GitOps Release Engine",
    no_args_is_help=True,
)
console = Console()

DEFAULT_MANIFEST = Path("release-manifest.yaml")


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

    release = ReleaseManifest.from_yaml(manifest)

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
) -> None:
    """Promote the active candidate to a target stage (same digest)."""
    service = ReleaseService(manifest_path=manifest)
    result = service.promote(stage)
    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[green]Promoted to[/green] [bold]{result.stage}[/bold]")
    console.print(f"[dim]  version: {result.version}[/dim]")
    console.print(f"[dim]  digest:  {result.digest}[/dim]")
    console.print()


@app.command()
def rollback() -> None:
    """Rollback a stage to a known prior state. (Not yet implemented)"""
    console.print("[yellow]Not yet implemented — Milestone 2[/yellow]")


@app.command()
def version() -> None:
    """Show GORA version."""
    from gora import __version__

    console.print(f"gora {__version__}")


if __name__ == "__main__":
    app()
