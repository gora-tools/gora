"""The GORA command line: today ``gora version`` only.

The commands of release-model section 11 are decided, not built.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from difflib import get_close_matches
from typing import Any

import typer
from rich.console import Console
from rich.text import Text

# TyperGroup's signatures take the vendored click Context; typer.Context only subclasses it.
from typer import _click

# The vendored click's usage errors; Typer exports neither of them.
from typer._click.exceptions import NoSuchOption, UsageError
from typer.core import TyperCommand, TyperGroup

from gora.build_info import build_info
from gora.text import visible


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
def _gora() -> None:
    """GORA - Deterministic GitOps Release Engine"""
    # Its being here keeps the app a group: Typer collapses an app with one command and no
    # callback into that command, and ``gora version`` would be an unexpected argument.


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


if __name__ == "__main__":
    app()
