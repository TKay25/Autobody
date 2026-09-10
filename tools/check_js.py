"""JavaScript syntax sanity checker.

Checks that every front-end script has balanced brackets, strings, template
literals, comments **and regex literals**. It is not a full parser, but it
catches the class of mistake that actually bites: a missing or extra `)` in a
deeply nested `h(...)` call, which is invisible to Python tests and only shows
up when a user opens that screen.

Run directly:

    python tools/check_js.py

`tests/test_frontend.py` calls :func:`check_all`, so CI fails on a broken view.
"""
from __future__ import annotations

import sys
from pathlib import Path

PAIRS = "([{"
CLOSERS = ")]}"
TEMPLATE_OPEN = "${"

# A `/` starts a regex literal when the previous significant character cannot
# end an expression — the same heuristic JavaScript lexers use.
REGEX_ALLOWED_AFTER = set("([{,;:=!&|?+-*%^~<>") | {""}
KEYWORDS_BEFORE_REGEX = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "yield", "await", "case", "do", "else", "throw",
}


class Problem(Exception):
    """A syntax problem found in a script."""

    def __init__(self, path: Path, line: int, message: str) -> None:
        super().__init__(f"{path.name}:{line} {message}")
        self.path = path
        self.line = line
        self.message = message


def _skip_regex(src: str, start: int, line: int, path: Path) -> int:
    """Return the index just past a regex literal that begins at ``start``."""
    i = start + 1
    n = len(src)
    in_class = False
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            raise Problem(path, line, "unterminated regex literal")
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            i += 1
            while i < n and src[i].isalpha():   # flags
                i += 1
            return i - 1
        i += 1
    raise Problem(path, line, "unterminated regex literal")


def scan(path: Path) -> bool:
    """Return True when ``path`` balances. Prints the failure when it does not."""
    try:
        _check(path)
        return True
    except Problem as problem:
        print(f"  FAIL {problem}")
        return False


def _check(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    n = len(src)
    i = 0
    line = 1
    stack: list[tuple[str, int]] = []
    state: str | None = None
    prev_sig = ""        # last significant character seen in code state
    prev_word = ""       # last identifier, so `return /re/` is handled

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "\n":
            line += 1

        if state is None:
            if c in "\"'`":
                state = c
            elif c == "/" and nxt == "/":
                state = "//"
                i += 1
            elif c == "/" and nxt == "*":
                state = "/*"
                i += 1
            elif c == "/":
                is_regex = prev_sig in REGEX_ALLOWED_AFTER or prev_word in KEYWORDS_BEFORE_REGEX
                if is_regex:
                    i = _skip_regex(src, i, line, path)
                    prev_sig, prev_word = "/", ""
                else:
                    prev_sig, prev_word = c, ""
            elif c in PAIRS:
                stack.append((c, line))
                prev_sig, prev_word = c, ""
            elif c == "}" and stack and stack[-1][0] == TEMPLATE_OPEN:
                # Close a `${ ... }` interpolation and resume the template.
                stack.pop()
                state = "`"
                prev_sig, prev_word = c, ""
            elif c in CLOSERS:
                if not stack:
                    raise Problem(path, line, f"extra {c!r} with nothing open")
                opener, opened_at = stack.pop()
                if PAIRS.index(opener) != CLOSERS.index(c):
                    raise Problem(
                        path, line, f"{c!r} closes {opener!r} opened on line {opened_at}"
                    )
                prev_sig, prev_word = c, ""
            elif c.isalnum() or c in "_$":
                prev_word = (prev_word + c) if (prev_sig.isalnum() or prev_sig in "_$") else c
                prev_sig = c
            elif not c.isspace():
                prev_sig, prev_word = c, ""
        else:
            if c == "\\":
                i += 2
                continue
            if state == "`" and c == "$" and nxt == "{":
                stack.append((TEMPLATE_OPEN, line))
                state = None
                prev_sig, prev_word = "{", ""
                i += 2
                continue
            if state in "\"'`" and c == state:
                state = None
                prev_sig, prev_word = "'", ""
            elif state == "//" and c == "\n":
                state = None
            elif state == "/*" and c == "*" and nxt == "/":
                state = None
                i += 1

        i += 1

    if stack:
        unclosed = ", ".join(f"{o!r}@{ln}" for o, ln in stack[-6:])
        raise Problem(path, stack[-1][1], f"unclosed {unclosed}")
    if state in {"'", '"', "`"}:
        raise Problem(path, line, f"file ends inside a {state} string")


def check_all(root: Path | None = None) -> list[Problem]:
    """Return every problem found under ``root`` (defaults to app/static/js)."""
    root = root or (Path(__file__).resolve().parent.parent / "app" / "static" / "js")
    problems: list[Problem] = []
    for path in sorted(root.rglob("*.js")):
        try:
            _check(path)
        except Problem as problem:
            problems.append(problem)
    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]]
    if targets:
        failed = [p for p in targets if not scan(p)]
        print(f"checked {len(targets)} file(s), {len(failed)} with problems")
        return 1 if failed else 0

    root = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    files = sorted(root.rglob("*.js"))
    problems = check_all(root)
    for problem in problems:
        print(f"  FAIL {problem}")
    print(f"checked {len(files)} file(s), {len(problems)} with problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
