"""GORA CLI — primary local interface for release operations."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from gora.domain.release.manifest import ReleaseManifest

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
def cut_rc() -> None:
    """Cut a new release candidate. (Not yet implemented)"""
    console.print("[yellow]Not yet implemented — Milestone 2[/yellow]")


@app.command()
def promote() -> None:
    """Promote current candidate to a target stage. (Not yet implemented)"""
    console.print("[yellow]Not yet implemented — Milestone 2[/yellow]")


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
