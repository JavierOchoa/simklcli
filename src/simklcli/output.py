"""Keep argument-validation failures machine readable when --json is requested."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

from typer import Abort
from typer import _click as click
from typer.core import TyperGroup


class JsonGroup(TyperGroup):
    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        arguments = list(sys.argv[1:] if args is None else args)
        try:
            result = super().main(
                args=arguments,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except click.ClickException as exc:
            if "--json" in arguments:
                click.echo(
                    json.dumps({"error": "invalid_arguments", "message": exc.format_message()})
                )
            exc.show()
            if standalone_mode:
                raise SystemExit(exc.exit_code) from None
            raise
        except Abort as exc:
            if "--json" in arguments:
                click.echo(json.dumps({"error": "cancelled", "message": "Aborted."}))
            click.echo("Aborted!", err=True)
            if standalone_mode:
                raise SystemExit(
                    130 if isinstance(exc.__cause__, KeyboardInterrupt) else 1
                ) from None
            raise
        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result
