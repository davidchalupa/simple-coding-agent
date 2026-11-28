"""AST-based structural checks for line-based Python replacements.

This module deliberately implements a narrow, high-confidence guardrail:
reject a ``replace_lines`` operation when its payload introduces a definition
with the same name/kind as an existing Python symbol, but the requested line
range only covers part of that existing symbol.

The check is fail-open when the existing file or replacement cannot be parsed.
It does not attempt to validate arbitrary edits or infer user intent.
"""

from __future__ import annotations

import ast
import io
import textwrap
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PartialSymbolReplacement:
    """Details of a replacement that would partially redefine a symbol."""

    symbol_name: str
    symbol_kind: str
    symbol_start_line: int
    symbol_end_line: int
    requested_start_line: int
    requested_end_line: int
    indentation: str

    @property
    def indentation_column(self) -> int:
        # This is a character column, which is sufficient for spaces and tabs
        # in the diagnostic. ``indentation`` also preserves the exact prefix.
        return len(self.indentation.expandtabs(8))


def _line_number(value: object) -> int | None:
    """Accept integer line numbers (and digit strings) without accepting bools."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _symbol_kind(node: ast.AST) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    if isinstance(node, ast.ClassDef):
        return "class"
    return None


def _symbol_start_line(node: ast.AST) -> int:
    """Include decorators in the source span of a definition."""
    decorator_lines = [
        decorator.lineno
        for decorator in getattr(node, "decorator_list", ())
        if getattr(decorator, "lineno", None) is not None
    ]
    node_line = getattr(node, "lineno", 1)
    return min([node_line, *decorator_lines])


def _iter_symbols(tree: ast.AST) -> Iterable[ast.AST]:
    for node in ast.walk(tree):
        if _symbol_kind(node) is not None and getattr(node, "end_lineno", None) is not None:
            yield node


def _read_source(filepath: str | Path) -> tuple[str, list[str]] | None:
    try:
        source = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return None
    return source, source.splitlines()


def _tokenized_definition_identities(source: str) -> set[tuple[str, str]]:
    """Recover function/class headers emitted before a payload syntax error."""
    tokens = []
    generator = tokenize.generate_tokens(io.StringIO(source).readline)
    while True:
        try:
            tokens.append(next(generator))
        except StopIteration:
            break
        except (tokenize.TokenError, IndentationError, SyntaxError):
            break

    identities: set[tuple[str, str]] = set()
    for index, token in enumerate(tokens[:-1]):
        if token.type != tokenize.NAME or token.string not in {"def", "class"}:
            continue
        name_token = tokens[index + 1]
        if name_token.type != tokenize.NAME:
            continue
        kind = "class" if token.string == "class" else "function"
        identities.add((kind, name_token.string))
    return identities


def detect_partial_symbol_redefinition(
    filepath: str | Path,
    start_line: object,
    end_line: object,
    replacement_content: str,
) -> PartialSymbolReplacement | None:
    """Detect a same-named definition inserted into only part of itself.

    ``replace_lines`` line ranges are treated as inclusive, matching the tool
    arguments used by the agent. The replacement is parsed after dedenting so
    that replacing an indented method (or the malformed nested-function case)
    can be analyzed independently of its eventual insertion column.

    Returns the most specific offending original symbol, or ``None`` when the
    operation is structurally acceptable or cannot be checked safely.
    """
    requested_start = _line_number(start_line)
    requested_end = _line_number(end_line)
    if requested_start is None or requested_end is None or requested_end < requested_start:
        return None
    if not isinstance(replacement_content, str) or not replacement_content.strip():
        return None

    loaded = _read_source(filepath)
    if loaded is None:
        return None
    source, source_lines = loaded

    try:
        original_tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, ValueError, TypeError):
        # Do not block edits to files that are already syntactically invalid;
        # other validation/recovery mechanisms can handle those cases.
        return None

    normalized_replacement = textwrap.dedent(replacement_content.strip("\n"))
    try:
        replacement_tree = ast.parse(normalized_replacement, filename="<replace_lines payload>")
    except (SyntaxError, ValueError, TypeError):
        # A malformed expression later in the proposed body must not hide an
        # otherwise clear `def same_name` header near the start of the payload.
        # Tokenization lets us recover definition headers emitted before the
        # lexical/syntax error without treating text inside strings as code.
        replacement_symbols = _tokenized_definition_identities(normalized_replacement)
    else:
        replacement_symbols = {
            (_symbol_kind(node), getattr(node, "name", None))
            for node in _iter_symbols(replacement_tree)
        }
    if not replacement_symbols:
        return None

    candidates: list[tuple[int, int, ast.AST, int, int]] = []
    for original_symbol in _iter_symbols(original_tree):
        identity = (_symbol_kind(original_symbol), getattr(original_symbol, "name", None))
        if identity not in replacement_symbols:
            continue

        original_start = _symbol_start_line(original_symbol)
        original_end = int(original_symbol.end_lineno)

        # Ignore same-named symbols elsewhere in the file. We only care about
        # an original definition whose source span intersects the requested edit.
        overlaps = requested_start <= original_end and requested_end >= original_start
        if not overlaps:
            continue

        covers_complete_symbol = (
            requested_start <= original_start and requested_end >= original_end
        )
        if covers_complete_symbol:
            continue

        candidates.append((original_end - original_start, original_start,
                           original_symbol, original_start, original_end))

    if not candidates:
        return None

    # Prefer the smallest matching original symbol, avoiding a broader outer
    # definition when a more specific nested definition is also implicated.
    _, _, symbol, original_start, original_end = min(candidates, key=lambda item: (item[0], item[1]))
    indentation = ""
    if 1 <= original_start <= len(source_lines):
        source_line = source_lines[original_start - 1]
        indentation = source_line[:len(source_line) - len(source_line.lstrip(" \t"))]

    return PartialSymbolReplacement(
        symbol_name=str(getattr(symbol, "name", "<unknown>")),
        symbol_kind=str(_symbol_kind(symbol)),
        symbol_start_line=original_start,
        symbol_end_line=original_end,
        requested_start_line=requested_start,
        requested_end_line=requested_end,
        indentation=indentation,
    )
