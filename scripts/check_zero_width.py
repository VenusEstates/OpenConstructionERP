"""Detect stray zero-width Unicode characters, and be the only place that says which ones.

This file exists because the rule used to live in two places. The CI job in
.github/workflows/ci.yml and the `lint:unicode` script in frontend/package.json
each carried their own character class. On 2026-08-14 the CI copy was narrowed
to stop treating letters as invisibles; the npm copy was not, and for eight days
it kept failing on correctly written Persian while telling the reader to run
scripts/strip_zero_width.py. On 2026-08-17 someone did, and 35211 U+200C left
fa.ts, 54 U+200D left bn.ts and 25 U+200E left he.ts. Every gate we own was
green about it.

So there is one class now, defined below, and three consumers: the CI job, the
npm script and the stripper, none of which restates it. `--self-check` runs on
every invocation and fails if a caller has grown a character class of its own
again.

STRAY, and therefore reported
-----------------------------
  U+200B ZERO WIDTH SPACE          U+2061-U+2064 INVISIBLE OPERATORS
  U+2060 WORD JOINER               U+2066-U+2069 BIDI ISOLATES
  U+FEFF ZERO WIDTH NO-BREAK SPACE

These crashed React reconciliation on /contracts when a browser translation
extension mutated the DOM (task #135 / R6), and no language we ship spells
anything with them, so a hit is always worth acting on. ESLint's
no-irregular-whitespace covers part of this inside .ts/.tsx, but it does not
know the bidi isolates and does not run on .md/.html/.py, which is why the
scan is here as well.

SPELLING, and therefore never reported
--------------------------------------
  U+200C ZWNJ  required Persian orthography, and Arabic and Urdu use it too
  U+200D ZWJ   forms Bengali and Devanagari conjuncts
  U+200E LRM   puts a Latin token, ".ifc" say, on the right side of an RTL line
  U+200F RLM   the mirror case

Scanning those made this check fail on every run against correct translations,
and the response at the time was to except frontend/src/app/locales/ar.ts
wholesale, which then hid six real defects inside it. Guarding only the
genuinely invisible characters means no file needs an exception.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# The single definition. scripts/strip_zero_width.py imports both of these.
STRAY_CHARS = {
    chr(0x200B): "U+200B ZWSP",
    chr(0x2060): "U+2060 WJ",
    chr(0x2061): "U+2061 FA",
    chr(0x2062): "U+2062 IT",
    chr(0x2063): "U+2063 IS",
    chr(0x2064): "U+2064 IP",
    chr(0x2066): "U+2066 LRI",
    chr(0x2067): "U+2067 RLI",
    chr(0x2068): "U+2068 FSI",
    chr(0x2069): "U+2069 PDI",
    chr(0xFEFF): "U+FEFF BOM",
}

SPELLING_CHARS = {
    chr(0x200C): "U+200C ZWNJ (Persian, Arabic, Urdu)",
    chr(0x200D): "U+200D ZWJ (Bengali, Devanagari)",
    chr(0x200E): "U+200E LRM (Hebrew, Arabic)",
    chr(0x200F): "U+200F RLM (Hebrew, Arabic)",
}

DEFAULT_ROOTS = ("frontend/src", "marketing-site")
EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md", ".py", ".snap")
SKIP_DIRS = {"node_modules", "dist", "build", "coverage", ".git"}

REMEDIATION = (
    "Delete the invisible character by hand, in the word you can see it sitting in. "
    "Do not reach for a bulk stripper unless you have read what it refuses to touch: "
    "U+200C, U+200D, U+200E and U+200F are letters and required marks, not stray formatting."
)

# The two callers, and the file each one is allowed to name.
CALLERS = (
    (os.path.join(".github", "workflows", "ci.yml"), "check_zero_width.py"),
    (os.path.join("frontend", "package.json"), "check_zero_width.py"),
)

# The third consumer, and the only one that deletes anything.
REMEDIATOR = os.path.join("scripts", "strip_zero_width.py")

# A caller writing its own class writes an escape: \x{200B}, \u200B or \u{200B}.
# Prose that merely names a codepoint, "U+200C" in a comment, has no backslash
# and is not a second copy of the rule, so it does not trip this.
ESCAPE_RE = re.compile(r"\\(?:x\{([0-9A-Fa-f]{1,6})\}|u\{([0-9A-Fa-f]{1,6})\}|u([0-9A-Fa-f]{4}))")
GOVERNED = {ord(c) for c in STRAY_CHARS} | {ord(c) for c in SPELLING_CHARS}


# What is in scope has to be decided by the repository, not by how much
# unversioned material happens to be sitting in the working copy. marketing-site
# is 770 MB of generated pages, ignored in its entirety, and walking it meant the
# no-argument run - the form ci.yml uses - never finished on a developer machine
# that had it checked out. CI never noticed, because a fresh clone has no
# marketing-site at all: the gate worked everywhere except where a person would
# actually run it, which is the worst way for one to be broken.
#
# So git decides. `ls-files --cached --others --exclude-standard` is everything
# the repository tracks plus everything a commit could still pick up, and nothing
# .gitignore has already excluded. A file written a minute ago and not yet added
# is still scanned; generated output that no commit can reach is not.
MAX_FALLBACK_FILES = 20000


class Unbounded(Exception):
    """Raised when a root cannot be enumerated within a budget.

    A gate that hangs cannot be told apart from a gate that is thinking, and
    this one hung for ten minutes before anyone found out. Refusing loudly is
    the only honest answer when the scope cannot be established.
    """


def git_files(repo_root: str, root: str) -> list[str] | None:
    """Paths under ``root`` that git can account for, or None if it cannot."""
    try:
        result = subprocess.run(  # noqa: S603, S607
            [
                "git",
                "-C",
                repo_root,
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                root,
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    names = [n for n in result.stdout.decode("utf-8", "replace").split("\0") if n]
    return [os.path.join(repo_root, n.replace("/", os.sep)) for n in names]


def iter_files(repo_root: str, root: str):
    """Yield the files under ``root`` this gate is responsible for."""
    paths = git_files(repo_root, root)
    if paths is None:
        # No git here - a wheel, a tarball, a copied directory. Walk, but with a
        # ceiling, so the unbounded case fails instead of hanging.
        paths = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(repo_root, root)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                paths.append(os.path.join(dirpath, name))
            if len(paths) > MAX_FALLBACK_FILES:
                raise Unbounded(root)
    for path in paths:
        parts = set(os.path.relpath(path, repo_root).replace(os.sep, "/").split("/"))
        if parts & SKIP_DIRS:
            continue
        if path.endswith(EXTENSIONS):
            yield path


# The common case, a clean tree, must not pay for decoding every file it reads.
# One compiled pass over the raw bytes answers "is anything in here", and only a
# file that says yes gets decoded and located by line. The pattern is built from
# STRAY_CHARS, never written out.
STRAY_BYTES_RE = re.compile(b"|".join(re.escape(ch.encode("utf-8")) for ch in STRAY_CHARS))


def scan(repo_root: str, roots: list[str]) -> tuple[list[tuple[str, int, str]], dict[str, int]]:
    """Return every stray hit, and how many files each root contributed.

    The per-root count is printed on every run. A gate that reports only pass or
    fail cannot tell a clean tree from a tree it never looked at, and this one
    has now been narrowed once; the number is what makes the next narrowing
    visible.
    """
    hits: list[tuple[str, int, str]] = []
    counted: dict[str, int] = {}
    for root in roots:
        abs_root = os.path.join(repo_root, root)
        if not os.path.isdir(abs_root):
            print(f"skipped, not present in this checkout: {root}")
            continue
        seen = 0
        for path in iter_files(repo_root, root):
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
            except OSError:
                continue
            seen += 1
            if not STRAY_BYTES_RE.search(blob):
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                text = blob.decode("utf-8", "replace")
            rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for ch, name in STRAY_CHARS.items():
                    if ch in line:
                        hits.append((rel, lineno, name))
        counted[root] = seen
    return hits, counted


def self_check(repo_root: str) -> list[str]:
    """Return the reasons the two callers no longer agree with this file, if any."""
    problems: list[str] = []
    for rel, expected in CALLERS:
        path = os.path.join(repo_root, rel)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except OSError:
            problems.append(f"{rel} is missing, so it cannot be shown to delegate here")
            continue

        if rel.endswith("package.json"):
            try:
                scripts = json.loads(text).get("scripts", {})
            except json.JSONDecodeError as exc:
                problems.append(f"{rel} does not parse as JSON: {exc}")
                continue
            text = scripts.get("lint:unicode", "")
            if not text:
                problems.append(f"{rel} has no lint:unicode script")
                continue
            rel = f"{rel} (lint:unicode)"

        if expected not in text:
            problems.append(f"{rel} does not call {expected}, so it is a second copy of the rule")

        for match in ESCAPE_RE.finditer(text):
            code = int(next(g for g in match.groups() if g), 16)
            if code in GOVERNED:
                problems.append(
                    f"{rel} spells out U+{code:04X} in a character class of its own. "
                    f"That is how the two copies drifted apart last time. Call {expected} instead."
                )

    # The remediator gets a different check, because it is the tool that actually
    # deletes and because Python spells a codepoint chr(0x200C), which carries no
    # backslash for ESCAPE_RE to find. Comparing the imported values at runtime
    # would not help either: run as a script this module is __main__, so an import
    # of the remediator loads a second copy of these dicts and every value still
    # compares equal. So the check is structural. It must import the class, and it
    # must not assign to either name, which forbids even a byte-identical copy.
    path = os.path.join(repo_root, REMEDIATOR)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError:
        problems.append(f"{REMEDIATOR} is missing, so it cannot be shown to import this class")
    else:
        if "from check_zero_width import" not in text:
            problems.append(
                f"{REMEDIATOR} does not import this class. The tool that deletes must not own "
                f"its own idea of what is deletable."
            )
        for name in ("STRAY_CHARS", "SPELLING_CHARS"):
            if re.search(rf"^\s*{name}\s*(=|\[)", text, re.M):
                problems.append(
                    f"{REMEDIATOR} assigns to {name}. Import it from check_zero_width instead: "
                    f"a remediator with its own copy of the rule is what removed 35290 letters on 2026-08-17."
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "roots",
        nargs="*",
        default=None,
        help="directories to scan, relative to the repo root",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repository root (default: the parent of scripts/)",
    )
    parser.add_argument(
        "--skip-self-check",
        action="store_true",
        help="scan only, do not verify the callers agree",
    )
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    repo_root = os.path.abspath(args.repo_root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    roots = list(args.roots) if args.roots else list(DEFAULT_ROOTS)

    if not args.skip_self_check:
        problems = self_check(repo_root)
        if problems:
            print("The zero-width rule is defined in scripts/check_zero_width.py and nowhere else.")
            for problem in problems:
                print(f"  {problem}")
            return 2

    try:
        hits, counted = scan(repo_root, roots)
    except Unbounded as exc:
        print(
            f"{exc.args[0]} holds more than {MAX_FALLBACK_FILES} files and git is not "
            "available here to say which of them the repository owns, so the scope of "
            "this scan cannot be established. Run it from a git checkout, or name the "
            "directories to scan."
        )
        return 2

    scanned = ", ".join(f"{root} ({counted[root]} files)" for root in roots if root in counted)
    if hits:
        print("Zero-width Unicode characters found:")
        for rel, lineno, name in hits:
            print(f"  {rel}:{lineno}  {name}")
        print(REMEDIATION)
        print(f"scanned: {scanned}")
        return 1

    print(f"No stray zero-width Unicode characters found in: {scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
