"""The GORA command line: ``gora version`` and ``gora changes``.

The other commands of release-model section 11 are decided, not built. ``gora changes`` is
built for a project whose code and release files share one repository (``S == R``).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from difflib import get_close_matches
from enum import StrEnum
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.text import Text

# TyperGroup's signatures take the vendored click Context; typer.Context only subclasses it.
from typer import _click

# The vendored click's usage errors; Typer exports neither of them.
from typer._click.exceptions import NoSuchOption, UsageError
from typer.core import TyperCommand, TyperGroup

from gora.adapters.code_hosts import code_host
from gora.adapters.git import GitError
from gora.adapters.github import GitHubError
from gora.build_info import build_info
from gora.config.settings import get_settings
from gora.domain.release.candidates import RefusedError
from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release_repository.tree import InvalidReleaseTreeError
from gora.services import changes as service
from gora.text import visible

NO_PROJECT = "no project: pass -p/--project, or set GORA_PROJECT"
NO_OUTPUT = "--output is text or json"


class _ShownUsageError(UsageError):
    """A usage error whose message is already shown; ``_visible_errors`` leaves it alone."""


@contextmanager
def _visible_errors() -> Iterator[None]:
    """A click error raised inside is shown escaped, wherever its message quotes input.

    Typer vendors click; ``TyperException`` is the public base of its click errors.

    From Typer 0.27.3 two of those messages arrive escaped already, as ``\\xNN`` with no
    escape for a backslash, and escaping them again would show an ESC and the text ``\\x1b``
    alike: an escape would no longer read back to one character (``gora.text.visible``).
    GORA builds both from the raw values, so what is shown does not depend on the Typer
    installed: leftover arguments are refused by ``_Command``, and an unknown option is
    rebuilt here from the exception's ``option_name``, which stays raw. The name of an
    unknown command reaches the message through ``repr`` on every version, and is built
    from the raw value the same way, by ``_Group.resolve_command``.
    """
    try:
        yield
    except _ShownUsageError:
        raise
    except NoSuchOption as exc:
        exc.message = f"No such option: {visible(exc.option_name)}"
        raise
    except typer.TyperException as exc:
        exc.message = visible(exc.message)
        raise


class _Command(TyperCommand):
    """A command that refuses its own leftover arguments, so their message is built here.

    Click builds "Got unexpected extra argument(s)" inside ``parse_args`` and, from Typer
    0.27.3, escapes it there. The check is the same one, run on the raw values after click
    has handled every parameter: its callbacks still report first.
    """

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        allowed = ctx.allow_extra_args
        ctx.allow_extra_args = True
        try:
            rest = super().parse_args(ctx, args)
        finally:
            ctx.allow_extra_args = allowed
        if rest and not allowed and not ctx.resilient_parsing:
            shown = " ".join(visible(str(arg)) for arg in rest)
            raise _ShownUsageError(f"Got unexpected extra argument(s) ({shown})", ctx)
        return rest


class _Group(TyperGroup):
    """Usage errors quote the arguments that caused them; those are shown escaped too."""

    def resolve_command(
        self, ctx: _click.Context, args: list[str]
    ) -> tuple[str | None, _click.Command | None, list[str]]:
        """An unknown command is refused here, so its name is shown once, from the raw value.

        Typer formats the name with ``!r``, which escapes it, and ``_visible_errors`` would
        escape that again: an ESC and the text ``\\x1b`` would not read back as themselves.
        What the parser reads as an option, a dash and something after it, stays with Typer,
        which parses it as one; so does every other case, completion included.
        """
        name = args[0] if args else None
        if (
            name is not None
            and not ctx.resilient_parsing
            and not (name.startswith("-") and len(name) > 1)
            and not self._names_a_command(ctx, name)
        ):
            raise _ShownUsageError(self._no_such_command(name), ctx)
        return super().resolve_command(ctx, args)

    def _names_a_command(self, ctx: _click.Context, name: str) -> bool:
        """Whether ``name`` is a command of the group, looked up as click does."""
        command = self.get_command(ctx, name)
        if command is None and ctx.token_normalize_func is not None:
            command = self.get_command(ctx, ctx.token_normalize_func(name))
        return command is not None

    def _no_such_command(self, name: str) -> str:
        """The message Typer builds, from the raw name: quoted as ``repr`` quotes it, and with
        the close matches among the group's commands when the group suggests them."""
        shown = visible(name)
        quote = '"' if "'" in shown and '"' not in shown else "'"
        message = f"No such command {quote}{shown}{quote}"
        matches = get_close_matches(name, list(self.commands)) if self.suggest_commands else []
        if matches:
            return f"{message}. Did you mean {', '.join(repr(m) for m in matches)}?"
        return f"{message}."

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: _click.Context | None = None,
        **extra: Any,
    ) -> _click.Context:
        with _visible_errors():
            name = None if info_name is None else visible(info_name)
            return super().make_context(name, args, parent, **extra)

    def invoke(self, ctx: _click.Context) -> Any:
        with _visible_errors():
            return super().invoke(ctx)


app = typer.Typer(name="gora", no_args_is_help=True, cls=_Group)


@app.callback()
def _gora(
    ctx: typer.Context,
    project: Annotated[
        str | None,
        typer.Option(
            "-p",
            "--project",
            metavar="PROJECT",
            help=(
                "The project a command acts on: its release repository, by its full name with "
                "its host (github.com/OWNER/NAME). Else GORA_PROJECT; none is guessed from a clone."
            ),
        ),
    ] = None,
) -> None:
    """GORA - Deterministic GitOps Release Engine"""
    # Its being here keeps the app a group: Typer collapses an app with one command and no
    # callback into that command, and ``gora version`` would be an unexpected argument. It only
    # carries the project to the commands that act on one, which resolve it themselves, so that
    # a command with no project (``version``) never asks for one.
    ctx.obj = project


# Plain text: no markup, emoji codes or highlighting is read from what is printed, and
# no line is wrapped. Style is applied only through _print, never parsed from a value.
console = Console(soft_wrap=True, markup=False, emoji=False, highlight=False)
err_console = Console(stderr=True, soft_wrap=True, markup=False, emoji=False, highlight=False)


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


@app.command(cls=_Command)
def version() -> None:
    """Show the source commit GORA was built from, and the version it runs as."""
    info = build_info()
    _print(f"revision: {info.revision}")
    _print(f"version:  {info.version}")


class Output(StrEnum):
    TEXT = "text"
    JSON = "json"


@app.command("changes", cls=_Command)
def changes_command(
    ctx: typer.Context,
    commit: Annotated[
        str | None,
        typer.Argument(
            metavar="COMMIT",
            help="A full object name of a commit of main. Default: the head of main.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            metavar="REPOSITORY",
            help=(
                "The repository the artifacts are built from. Default: the project. Another "
                "repository is decided and not built."
            ),
        ),
    ] = None,
    output: Annotated[
        str,
        typer.Option(
            metavar="text|json",
            help="text: one line per artifact; json: the body of the API, on one line.",
        ),
    ] = Output.TEXT,
) -> None:
    """List the artifacts whose inputs changed at a commit of main, in the clone in the current
    directory: those the commit is a candidate of, with the version it takes. Nothing is
    fetched and nothing is written."""
    # Not a Typer choice: click would quote the value it refuses, and a value shown twice escaped
    # does not read back as itself. The refusal is value-free.
    if output not in set(Output):
        raise _ShownUsageError(NO_OUTPUT, ctx)
    settings = get_settings()
    given: str | None = ctx.obj
    if given is None and not settings.project:
        raise _ShownUsageError(NO_PROJECT, ctx)
    project = given if given is not None else settings.project
    if commit is not None and SOURCE_COMMIT.problem(commit) is not None:
        raise _ShownUsageError(f"COMMIT {SOURCE_COMMIT.rule}", ctx)
    try:
        host = code_host(project, settings)
    except ValueError as error:
        # code_host's message is value-free but for a host it has already made visible.
        raise _ShownUsageError(str(error), ctx) from None
    try:
        found = service.changes(
            project=project,
            source=project if source is None else source,
            commit=commit,
            host=host,
            root=os.getcwd(),
            git=settings.git,
        )
    except InvalidReleaseTreeError as error:
        for problem in error.problems:
            _print(str(problem), err=True)
        raise typer.Exit(1) from None
    except (RefusedError, service.ChangesError, GitError, GitHubError) as error:
        _print(str(error), err=True)
        raise typer.Exit(1) from None
    if output == Output.JSON:
        _print(found.model_dump_json())
    else:
        for artifact in found.artifacts:
            _print(f"{artifact.name} {artifact.version}")


if __name__ == "__main__":
    app()
