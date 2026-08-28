#!/usr/bin/env python3
"""Prompt provenance guard: keep the instruction text we send to a model readable.

Why this exists, which is not the same as what it checks.

An EU AI Act Article 5 measurement was run across the platform and came back
clean: nothing here infers emotion, categorises people biometrically, scores
social behaviour or scrapes faces. That result rests on a property of the code
rather than on anybody's judgement. Every instruction we send to a model is a
literal a reader can open and read, so "what do we ask the model to do" is a
question the source answers completely. Read the constants and you have read the
product.

That property is not a law of nature. The day a prompt is assembled from a
database row, a settings record, a request body or an uploaded file, the source
stops answering the question. A tenant could then ask for an affective label
through our own pipeline with no code change of ours, and a sweep like the one
that produced the clean result would see nothing, because there would be nothing
to see. The measurement would not be wrong so much as no longer meaningful.

So this guard holds the property open. If it goes red the honest response is to
redo the measurement, not to relax the guard.

The property is already not universal, and the acknowledgement lists below are
where that is written down rather than hidden. Somebody reading this file should
be able to answer "where can instruction text come from other than the source"
without having to read anything else.

What it checks
--------------

Every call to ``ai_client.call_ai`` passes its ``system`` argument. This walks
the AST to that argument and asks where the text came from, resolving a name one
hop through the enclosing function, one hop through a private helper's callers
in the same file, then to a module-level constant, then to a constant imported
from another module. Four outcomes:

``constant``
    A string literal, an f-string, a concatenation or ``.format()`` over one.
    Interpolating data into a constant template is normal and necessary here: a
    BOQ description has to reach the model somehow. The template is the
    instruction and the template is in the source.

``builder``
    The argument is a call to a helper that composes the text. The helper is not
    followed. Each one is listed in ``CLEARED_BY_READING`` with the constant a
    person arrived at, so a new helper fails until somebody opens it.

``runtime``
    The text comes from a row, a settings object, or a parameter whose origin
    this scan cannot see. Where the text really is runtime it goes in
    ``RUNTIME_BY_DESIGN`` with what writes it and who is allowed to write it.
    Where a person followed it to a constant it goes in ``CLEARED_BY_READING``.
    A new one fails either way.

``unresolved``
    The scan could not decide. This fails like a violation and says so, because
    a guard that reports nothing when it understood nothing is worse than no
    guard: it reads as a pass.

It also checks the transcription path, which does not go through ``call_ai`` at
all. ``phonelog.transcription`` posts audio straight to a provider's REST audio
endpoint. That endpoint accepts an optional ``prompt`` field, so instruction text
could appear on the audio path without any ``call_ai`` site changing. The
multipart form is checked for it.

What it cannot see, stated plainly so nobody mistakes its silence for a proof.
It does not follow a builder into its body, it does not follow a public
function's parameter back to callers in other files, and it says nothing about
the ``prompt`` argument, which carries user data by design. Somebody who smuggles
instructions to the model inside a data field walks straight past this. It guards
the shape of the instruction slot, not the meaning of what arrives in it.

How to find a call site this file's anchors would miss, which is a real risk
because an anchor only ever finds the calls that go through it. The audio path
above was found by accident after a sweep organised by module name and module
docstring had already reported clean, and a module whose self-description
understates what it does defeats any sweep that reads prose. The search that
does not have that weakness is an intersection: take the modules that accept an
``UploadFile`` and intersect them with the modules that import ``ai_client``.
Neither half requires an opinion about what a module is for, and the result is
by construction every place a file a human uploaded meets a model. Run that
before trusting this file's list of anchors.

Exit codes: 0 clean, 1 findings, 2 the scan could not run and concluded nothing.
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import sys
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "backend" / "app"

#: Directory names that never hold source we are responsible for.
SKIP_PARTS = frozenset(
    {
        "__pycache__",
        ".venv",
        ".venv-run",
        "_frontend_dist",
        "_frontend_dist_prev",
        "node_modules",
    }
)

#: The one function every model call in the platform goes through.
MODEL_CALL = "call_ai"
#: ``call_ai(provider, api_key, system, prompt, ...)`` - the instruction slot.
INSTRUCTION_KEYWORD = "system"
INSTRUCTION_POSITION = 2

#: A URL constant holding this marks a speech-to-text endpoint.
TRANSCRIPTION_URL_MARK = "/audio/transcriptions"
#: Fields on that endpoint that carry instruction text rather than audio or format.
TRANSCRIPTION_INSTRUCTION_FIELDS = frozenset({"prompt"})

#: Below this many call sites the anchor has drifted and the run proves nothing.
MIN_EXPECTED_CALL_SITES = 20


# -- Acknowledgement lists ---------------------------------------------------
# Keyed by (path relative to the repo, enclosing function, the argument source
# text). Line numbers are deliberately not part of the key: this tree is edited
# by many hands and a key that moved with an unrelated insertion would turn every
# edit into a red gate and train people to widen the list instead of reading it.

#: Call sites this scan cannot follow, that a person followed instead and found
#: a constant at the end of. The value is the reading, not a note that a reading
#: happened: whoever adds an entry has to say which constant they arrived at, so
#: the next reader can check the claim without repeating the walk. A call site
#: that is not here fails, which is the point.
CLEARED_BY_READING: dict[tuple[str, str, str], str] = {
    (
        "backend/app/modules/boq/router.py",
        "ai_chat_boq",
        "with_locale(BOQ_CHAT_SYSTEM_PROMPT, locale)",
    ): (
        "with_locale appends a reply-in-this-language line to the module constant. The locale is "
        "a language tag, not free text, so BOQ_CHAT_SYSTEM_PROMPT carries the whole instruction."
    ),
    (
        "backend/app/modules/boq/service.py",
        "_call_llm",
        "system",
    ): (
        "Four callers in the same file, all of the form with_locale(<module constant>, locale): "
        "ENHANCE_DESCRIPTION_SYSTEM, SUGGEST_PREREQUISITES_SYSTEM, CHECK_SCOPE_SYSTEM and "
        "ESCALATE_RATE_SYSTEM. The scan sees the helper rather than the constants behind it."
    ),
    (
        "backend/app/modules/voice/service.py",
        "_extract_structured",
        "structuring.structuring_system_prompt(spec, target_language)",
    ): (
        "Picks one of the structuring module's constant templates by note type and appends the "
        "target language. Both inputs are enum-like; neither carries caller text."
    ),
    (
        "backend/app/modules/compliance/router.py",
        "_caller",
        "system",
    ): (
        "A closure returned by _build_ai_caller, so its argument is filled by whoever invokes the "
        "returned callable rather than by anything in this file. It is handed to parse_nl_to_dsl "
        "as ai_caller and invoked in exactly one place, core/validation/dsl/nl_builder.py, as "
        "ai_caller(_AI_SYSTEM_PROMPT, user_prompt). That is a module constant."
    ),
    (
        "backend/app/modules/compliance_ai/service.py",
        "_caller",
        "system",
    ): (
        "The same shape as the compliance router above and the same single invocation site: "
        "core/validation/dsl/nl_builder.py calls it as ai_caller(_AI_SYSTEM_PROMPT, user_prompt)."
    ),
    (
        "backend/app/modules/project_intelligence/advisor.py",
        "_call_ai_logged",
        "system",
    ): (
        "Both callers pass _build_system_prompt(role, language, standard), which selects one of "
        "three module constants in SYSTEM_PROMPTS by role, with a default, and formats {language} "
        "and {standard} into it. The role only chooses between constants; it cannot supply text."
    ),
}

#: Call sites where the instruction text genuinely is not a constant. Each entry
#: says what writes the text and who is allowed to write it, because that is the
#: question a conformance answer has to answer. This list is the honest form of
#: the claim at the top of this file: the property holds everywhere except here.
RUNTIME_BY_DESIGN: dict[tuple[str, str, str], str] = {
    (
        "backend/app/modules/ai_agents/llm.py",
        "next_step",
        "full_system",
    ): (
        "The agent runner's LLM bridge. For a built-in agent the text is a module constant. For a "
        "user-authored custom agent it is CustomAgent.system_prompt, written by that agent's own "
        "creator and visible only to them, so nobody has instruction text imposed on them by "
        "another user. Each tool re-checks the invoking user's permission, so an agent cannot "
        "reach data its creator could not read unaided. Its reach is not narrow, though: "
        "base.py:361 gives an agent with no tools selected the whole registry rather than none, "
        "so authored text can run over project documents via search_documents. A creator's own "
        "reach includes text written by and about other people, which is the point at which a "
        "prompt written here stops being purely self-service."
    ),
}


@dataclass
class Origin:
    """Where a piece of instruction text came from.

    Attributes:
        kind: One of ``constant``, ``builder``, ``runtime``, ``unresolved``.
        detail: How the decision was reached, in words a reader can act on.
    """

    kind: str
    detail: str = ""


@dataclass
class Finding:
    """One instruction slot the scan wants a human to look at."""

    path: str
    lineno: int
    func: str
    source: str
    origin: Origin


@dataclass
class Module:
    """A parsed source file and the names it binds."""

    path: pathlib.Path
    rel: str
    dotted: str
    tree: ast.Module
    consts: dict[str, ast.expr] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    owners: dict[int, ast.AST] | None = None
    calls: dict[str, list[ast.Call]] | None = None


class ParseFailure(Exception):
    """A file this scan had to read would not parse."""


def iter_source_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every .py file under ``root`` that is ours to answer for."""
    return [
        p for p in sorted(root.rglob("*.py")) if not SKIP_PARTS.intersection(p.parts)
    ]


def dotted_name(path: pathlib.Path, root: pathlib.Path) -> str:
    """``backend/app/modules/ai/prompts.py`` -> ``app.modules.ai.prompts``."""
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class Tree:
    """Lazy index of the app package: paths eagerly, parses on demand.

    Parsing all of ``backend/app`` costs more than the rest of the scan put
    together and almost none of it is needed. Only files that mention the model
    call are scanned, and another module is parsed only when a name has to be
    followed into it.
    """

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.paths: dict[str, pathlib.Path] = {}
        self.cache: dict[str, Module | None] = {}
        self.base = root.parent
        for path in iter_source_files(root):
            self.paths[dotted_name(path, root)] = path

    def relative(self, path: pathlib.Path) -> str:
        """The path as a reader would cite it, relative to the repo when it can be."""
        try:
            return path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return path.as_posix()

    def candidates(self) -> list[pathlib.Path]:
        """Files whose text mentions the model call or a transcription endpoint.

        A byte search, so a file is parsed only when it could hold a call site.
        The anchor is a plain identifier, so this cannot miss a call it would
        otherwise have found; the coverage floor in :func:`main` is what catches
        the anchor having been renamed out from under it.
        """
        needles = (MODEL_CALL.encode(), TRANSCRIPTION_URL_MARK.encode())
        out = []
        for path in self.paths.values():
            try:
                blob = path.read_bytes()
            except OSError as exc:
                raise ParseFailure(f"{path}: {exc}") from exc
            if any(n in blob for n in needles):
                out.append(path)
        return sorted(out)

    def get(self, dotted: str) -> Module | None:
        """Parse and index a module by dotted name, or ``None`` when we have no such file."""
        if dotted in self.cache:
            return self.cache[dotted]
        path = self.paths.get(dotted)
        if path is None:
            self.cache[dotted] = None
            return None
        try:
            return self.load(path)
        except ParseFailure:
            self.cache[dotted] = None
            return None

    def load(self, path: pathlib.Path) -> Module:
        """Parse one file and record the names it binds."""
        dotted = dotted_name(path, self.root)
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise ParseFailure(f"{self.relative(path)}: {exc}") from exc
        mod = Module(path=path, rel=self.relative(path), dotted=dotted, tree=tree)
        collect_module_names(mod)
        self.cache[dotted] = mod
        return mod


def collect_module_names(mod: Module) -> None:
    """Record module-level assignments and every ``from ... import`` binding.

    Assignments are read at module level only, because that is what a constant
    is. Imports are read from anywhere in the file: this codebase defers imports
    into function bodies on purpose, to keep import-time work at zero, and a
    deferred import binds the same name to the same module attribute. Reading
    only the top of the file leaves most of the real prompt constants looking
    unresolvable, which is the failure mode where a guard reports confusion
    about code that is perfectly clear.
    """
    for node in mod.tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    mod.consts[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            mod.consts[node.target.id] = node.value
    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        source_mod = resolve_relative_import(mod.dotted, node)
        if source_mod is None:
            continue
        for alias in node.names:
            mod.imports.setdefault(alias.asname or alias.name, (source_mod, alias.name))


def resolve_relative_import(current: str, node: ast.ImportFrom) -> str | None:
    """Turn a possibly-relative ``from X import Y`` into an absolute module name."""
    if not node.level:
        return node.module
    base = current.split(".")
    if node.level > len(base):
        return None
    prefix = base[: len(base) - node.level]
    if node.module:
        prefix = [*prefix, *node.module.split(".")]
    return ".".join(prefix) if prefix else None


def is_literal_text(node: ast.expr) -> bool:
    """True when the node is text a reader can read in the source."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        # An f-string's literal parts are the template; the holes are payload.
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return is_literal_text(node.left) and is_literal_text(node.right)
    if isinstance(node, ast.IfExp):
        return is_literal_text(node.body) and is_literal_text(node.orelse)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"format", "join", "strip", "lstrip", "rstrip"}:
            return is_literal_text(node.func.value)
    return False


def enclosing_functions(mod: Module) -> dict[int, ast.AST]:
    """Map every node in the module to its innermost enclosing function.

    One descent, cached. The obvious version walks the subtree of every function
    definition, which is quadratic in nesting and takes minutes on this tree's
    larger service modules.
    """
    if mod.owners is not None:
        return mod.owners
    owner: dict[int, ast.AST] = {}

    def descend(node: ast.AST, current: ast.AST | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else current
            )
            if inner is not None:
                owner[id(child)] = inner
            descend(child, inner)

    descend(mod.tree, None)
    mod.owners = owner
    return owner


def called_name(node: ast.Call) -> str | None:
    """The bare name of the function being called."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def calls_by_name(mod: Module) -> dict[str, list[ast.Call]]:
    """Every call in the module, indexed by the bare name being called."""
    if mod.calls is not None:
        return mod.calls
    index: dict[str, list[ast.Call]] = {}
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Call):
            name = called_name(node)
            if name:
                index.setdefault(name, []).append(node)
    mod.calls = index
    return index


def parameter_names(func: ast.AST) -> set[str]:
    """Every parameter name of a function definition."""
    if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
        return set()
    a = func.args
    names = {p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def last_local_assignment(func: ast.AST, name: str) -> ast.expr | None:
    """The last value assigned to ``name`` inside ``func``, if any.

    The last rather than the nearest. A name rebound in a branch is exactly where
    one hop stops being trustworthy, and taking the last keeps the answer
    conservative instead of confidently wrong.
    """
    found: ast.expr | None = None
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found = node.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                found = node.value
    return found


class Resolver:
    """Answers where a piece of instruction text came from, one hop at a time."""

    def __init__(self, tree: Tree) -> None:
        self.tree = tree

    def module_constant(self, dotted: str, name: str, depth: int = 0) -> Origin | None:
        """Classify a module-level name, following one import hop at a time."""
        if depth > 4:
            return Origin(
                "unresolved", f"import chain deeper than four hops at {dotted}.{name}"
            )
        mod = self.tree.get(dotted)
        if mod is None:
            return None
        if name in mod.consts:
            value = mod.consts[name]
            if is_literal_text(value):
                return Origin("constant", f"module constant {dotted}.{name}")
            if isinstance(value, ast.Name):
                return self.module_constant(dotted, value.id, depth + 1)
            if isinstance(value, ast.Call):
                return Origin("builder", f"{dotted}.{name} is built by a call")
            return Origin("unresolved", f"{dotted}.{name} is not literal text")
        if name in mod.imports:
            src_mod, src_name = mod.imports[name]
            return self.module_constant(src_mod, src_name, depth + 1)
        return None

    def classify(
        self,
        node: ast.expr,
        mod: Module,
        func: ast.AST | None,
        seen: frozenset[tuple[str, str, str]] = frozenset(),
    ) -> Origin:
        """Classify the expression filling an instruction slot."""
        if is_literal_text(node):
            return Origin("constant", "literal in the call")
        if isinstance(node, ast.Name):
            return self.classify_name(node.id, mod, func, seen)
        if isinstance(node, ast.Attribute):
            return Origin("runtime", f"attribute {ast.unparse(node)}")
        if isinstance(node, ast.Subscript):
            return Origin("runtime", f"subscript {ast.unparse(node)}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "format",
                "join",
                "strip",
                "lstrip",
                "rstrip",
            }:
                # Interpolating into a template is payload, not instruction, so
                # the question is only what the template is.
                return self.classify(node.func.value, mod, func, seen)
            return Origin("builder", f"call to {ast.unparse(node.func)}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                origin = self.classify(side, mod, func, seen)
                if origin.kind != "constant":
                    return origin
            return Origin("constant", "concatenation of constants")
        if isinstance(node, ast.IfExp):
            for side in (node.body, node.orelse):
                origin = self.classify(side, mod, func, seen)
                if origin.kind != "constant":
                    return origin
            return Origin("constant", "constant on either branch")
        return Origin("unresolved", f"unhandled expression {type(node).__name__}")

    def classify_name(
        self,
        name: str,
        mod: Module,
        func: ast.AST | None,
        seen: frozenset[tuple[str, str, str]] = frozenset(),
    ) -> Origin:
        """Resolve a bare name: enclosing function, then module, then import."""
        if func is not None:
            if name in parameter_names(func):
                from_callers = self.from_local_callers(mod, func, name, seen)
                if from_callers is not None:
                    return from_callers
                return Origin(
                    "runtime", f"parameter {name} of {getattr(func, 'name', '?')}"
                )
            local = last_local_assignment(func, name)
            if local is not None:
                if is_literal_text(local):
                    return Origin("constant", f"local {name} built from literal text")
                if isinstance(local, ast.Name):
                    return self.classify_name(local.id, mod, None, seen)
                if isinstance(local, ast.Attribute):
                    return Origin("runtime", f"local {name} = {ast.unparse(local)}")
                if isinstance(local, ast.Call):
                    return Origin(
                        "builder", f"local {name} = call to {ast.unparse(local.func)}"
                    )
                if isinstance(local, ast.BinOp | ast.IfExp):
                    return self.classify(local, mod, func, seen)
                return Origin("unresolved", f"local {name} = {type(local).__name__}")
        found = self.module_constant(mod.dotted, name)
        if found is not None:
            return found
        return Origin(
            "unresolved", f"name {name} is bound somewhere this scan does not read"
        )

    def from_local_callers(
        self,
        mod: Module,
        func: ast.AST,
        param: str,
        seen: frozenset[tuple[str, str, str]],
    ) -> Origin | None:
        """Classify a parameter by what this file's own call sites pass to it.

        Several modules wrap ``call_ai`` in a small private helper so retry,
        logging and budget handling live in one place. The instruction text is
        still a constant; it is handed over one frame earlier. Following that one
        frame, and only inside the same file, keeps those helpers out of the
        acknowledgement list without turning this into a whole-program dataflow
        analysis it has no way to get right.

        Returns ``None`` for a public function, whose callers are not all here,
        and for a helper nothing in this file calls. A confident verdict built
        from some of the callers would be worse than no verdict.
        """
        name = getattr(func, "name", None)
        if name is None or not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            return None
        if not name.startswith("_"):
            return None
        key = (mod.dotted, name, param)
        if key in seen:
            return Origin("unresolved", f"{name}() feeds its own {param}")
        seen = seen | {key}
        positional = [a.arg for a in [*func.args.posonlyargs, *func.args.args]]
        # ``self.method(a, b)`` binds ``self`` outside the argument list, so the
        # positional index in the signature is one ahead of the index at the
        # call. Getting this wrong does not fail loudly: it reads a different
        # argument and reports a confident verdict about the wrong expression,
        # which is how this scan first cleared a prompt by inspecting a payload.
        bound = 1 if positional and positional[0] in {"self", "cls"} else 0
        index = positional.index(param) - bound if param in positional else None
        if index is not None and index < 0:
            return None
        owners = enclosing_functions(mod)
        origins: list[Origin] = []
        for node in calls_by_name(mod).get(name, []):
            arg: ast.expr | None = None
            for kw in node.keywords:
                if kw.arg == param:
                    arg = kw.value
            at = (
                index
                if isinstance(node.func, ast.Attribute)
                else index + bound
                if index is not None
                else None
            )
            if arg is None and at is not None and len(node.args) > at:
                arg = node.args[at]
            if arg is None:
                continue
            origins.append(self.classify(arg, mod, owners.get(id(node)), seen))
        if not origins:
            return None
        for origin in origins:
            if origin.kind != "constant":
                return Origin(origin.kind, f"via {name}({param}=...): {origin.detail}")
        return Origin(
            "constant", f"every {name}({param}=...) in this file passes a constant"
        )


def instruction_argument(node: ast.Call) -> ast.expr | None:
    """The expression filling ``call_ai``'s instruction slot."""
    for kw in node.keywords:
        if kw.arg == INSTRUCTION_KEYWORD:
            return kw.value
    if len(node.args) > INSTRUCTION_POSITION:
        return node.args[INSTRUCTION_POSITION]
    return None


def model_call_sites(mod: Module) -> list[tuple[ast.Call, ast.expr, ast.AST | None]]:
    """Every ``call_ai`` use in the module, with its instruction argument."""
    owners = enclosing_functions(mod)
    out = []
    for node in calls_by_name(mod).get(MODEL_CALL, []):
        arg = instruction_argument(node)
        if arg is None:
            # No instruction slot means this is not a use of the model call.
            continue
        out.append((node, arg, owners.get(id(node))))
    return out


def site_key(mod: Module, arg: ast.expr, func: ast.AST | None) -> tuple[str, str, str]:
    """The acknowledgement key for one call site."""
    return (mod.rel, str(getattr(func, "name", "?")), ast.unparse(arg))


def scan_model_calls(
    modules: list[Module], resolver: Resolver
) -> tuple[list[Finding], int]:
    """Classify the instruction argument at every model call site."""
    findings: list[Finding] = []
    total = 0
    for mod in modules:
        for node, arg, func in model_call_sites(mod):
            total += 1
            origin = resolver.classify(arg, mod, func)
            if origin.kind == "constant":
                continue
            key = site_key(mod, arg, func)
            # Matched regardless of which kind the scan settled on. The lists
            # record what a person found, and a person can resolve a slot the
            # scan called unresolved just as well as one it called a builder.
            if key in CLEARED_BY_READING or key in RUNTIME_BY_DESIGN:
                continue
            findings.append(Finding(mod.rel, node.lineno, key[1], key[2], origin))
    return findings, total


def form_keys(node: ast.expr, func: ast.AST | None) -> set[str]:
    """The literal keys of a dict expression, following one local assignment."""
    if isinstance(node, ast.Name) and func is not None:
        local = last_local_assignment(func, node.id)
        if local is not None:
            node = local
    if not isinstance(node, ast.Dict):
        return set()
    return {
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def scan_transcription_posts(modules: list[Module]) -> list[Finding]:
    """Refuse an instruction field on a speech-to-text multipart form.

    The audio path does not go through ``call_ai``, so the slot it would grow is
    not the one above. A transcription endpoint takes an optional ``prompt`` that
    biases the transcript, which is instruction text arriving somewhere this
    platform currently sends none.
    """
    findings: list[Finding] = []
    for mod in modules:
        urls = {
            name
            for name, value in mod.consts.items()
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and TRANSCRIPTION_URL_MARK in value.value
        }
        if not urls:
            continue
        owners = enclosing_functions(mod)
        for node in calls_by_name(mod).get("post", []):
            target = node.args[0] if node.args else None
            if not (isinstance(target, ast.Name) and target.id in urls):
                continue
            func = owners.get(id(node))
            for kw in node.keywords:
                if kw.arg not in {"data", "json"}:
                    continue
                for key in sorted(
                    form_keys(kw.value, func) & TRANSCRIPTION_INSTRUCTION_FIELDS
                ):
                    findings.append(
                        Finding(
                            mod.rel,
                            node.lineno,
                            str(getattr(func, "name", "?")),
                            f"{kw.arg}[{key!r}]",
                            Origin(
                                "runtime", "instruction field on the audio endpoint"
                            ),
                        )
                    )
    return findings


def stale_acknowledgements(modules: list[Module]) -> list[str]:
    """Acknowledgement entries that no longer match a real call site.

    An entry left behind after the code moved is not harmless. It reads as a
    reviewed exception and is really a note about a call site that no longer
    exists, so the list quietly stops describing the platform.
    """
    live = {
        site_key(mod, arg, func)
        for mod in modules
        for _, arg, func in model_call_sites(mod)
    }
    return [
        f"{k[0]} :: {k[1]} :: {k[2]}"
        for k in [*CLEARED_BY_READING, *RUNTIME_BY_DESIGN]
        if k not in live
    ]


def interpreter_is_current() -> bool:
    """True when this interpreter can parse the syntax the backend uses.

    The backend targets 3.12 and uses PEP 695 type aliases and generics. On an
    older interpreter those files raise SyntaxError, and the files using the
    newest syntax are the newest files, so a scan that skipped them would be
    quietest about the code most likely to have just changed.
    """
    try:
        ast.parse("type Alias = int\ndef f[T](x: T) -> T: return x\n")
    except SyntaxError:
        return False
    return True


# -- Self-test ---------------------------------------------------------------
# A guard that only ever runs against a tree it passes on proves nothing about
# what it would catch. Each case below is a small fixture tree the scanner is
# pointed at, and the ones that matter most are the red ones: a slot it must
# refuse, and beside it a slot it must not. Run with --self-test.

_FIXTURES: dict[str, dict[str, str]] = {
    # Green: the ordinary shape, a constant imported from a prompts module.
    "imported constant": {
        "modules/x/prompts.py": 'SYSTEM_PROMPT = "You are a cost estimator."\n',
        "modules/x/service.py": (
            "async def run(text):\n"
            "    from app.modules.ai.ai_client import call_ai\n"
            "    from app.modules.x.prompts import SYSTEM_PROMPT\n"
            "    return await call_ai('openai', 'k', system=SYSTEM_PROMPT, prompt=text)\n"
        ),
    },
    # Green: user data interpolated into a constant template. A guard strict
    # enough to refuse this would be useless, because every real prompt here
    # carries data.
    "data formatted into a constant template": {
        "modules/x/service.py": (
            'TEMPLATE = "Classify this item: {desc}"\n'
            "async def run(row):\n"
            "    from app.modules.ai.ai_client import call_ai\n"
            "    return await call_ai('openai', 'k', system=TEMPLATE.format(desc=row.description), prompt='')\n"
        ),
    },
    # Green: a private wrapper called from the same file with a constant. The
    # single hop through local callers exists for this shape.
    "private wrapper passing a constant": {
        "modules/x/service.py": (
            'SYSTEM = "You are a scheduler."\n'
            "async def _ask(system, prompt):\n"
            "    from app.modules.ai.ai_client import call_ai\n"
            "    return await call_ai('openai', 'k', system=system, prompt=prompt)\n"
            "async def run(text):\n"
            "    return await _ask(SYSTEM, text)\n"
        ),
    },
    # Green, and a regression: a bound method's self is not among the call's
    # positional arguments. Reading the wrong argument here does not fail
    # loudly, it clears the slot by inspecting the payload sitting next to it,
    # which is what this scan did on boq/service.py before it was fixed.
    "bound method, constant one slot over": {
        "modules/x/service.py": (
            'SYSTEM = "You are a planner."\n'
            "class S:\n"
            "    async def _ask(self, user_id, system, prompt):\n"
            "        from app.modules.ai.ai_client import call_ai\n"
            "        return await call_ai('openai', 'k', system=system, prompt=prompt)\n"
            "    async def run(self, text):\n"
            "        return await self._ask('u', SYSTEM, text)\n"
        ),
    },
    # Red: the thing this guard exists for. Instruction text off a database row.
    "instruction text from a row": {
        "modules/x/service.py": (
            "async def run(row, text):\n"
            "    from app.modules.ai.ai_client import call_ai\n"
            "    return await call_ai('openai', 'k', system=row.system_prompt, prompt=text)\n"
        ),
    },
    # Red: the same thing one frame further away, which is how it would really
    # arrive. The wrapper is private and its caller passes the row attribute.
    "row attribute through a private wrapper": {
        "modules/x/service.py": (
            "async def _ask(system, prompt):\n"
            "    from app.modules.ai.ai_client import call_ai\n"
            "    return await call_ai('openai', 'k', system=system, prompt=prompt)\n"
            "async def run(settings, text):\n"
            "    return await _ask(settings.custom_prompt, text)\n"
        ),
    },
}

#: Fixtures the scan is required to refuse. Everything else must come back clean.
_RED_FIXTURES = frozenset(
    {"instruction text from a row", "row attribute through a private wrapper"}
)

_TRANSCRIPTION_CLEAN = (
    '_URL = "https://api.openai.com/v1/audio/transcriptions"\n'
    "async def go(client, files):\n"
    "    data = {'model': 'whisper-1', 'response_format': 'verbose_json'}\n"
    "    return await client.post(_URL, data=data, files=files)\n"
)
_TRANSCRIPTION_PROMPTED = _TRANSCRIPTION_CLEAN.replace(
    "'response_format': 'verbose_json'",
    "'response_format': 'verbose_json', 'prompt': 'the caller sounds'",
)


def _fixture_tree(stem: pathlib.Path, files: dict[str, str]) -> Tree:
    """Write a fixture app package to disk and index it."""
    root = stem / "app"
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return Tree(root)


def self_test() -> int:
    """Check the scanner against fixtures it must pass and fixtures it must fail."""
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for name, files in _FIXTURES.items():
            tree = _fixture_tree(base / name.replace(" ", "_"), files)
            modules = [tree.load(path) for path in tree.candidates()]
            found, total = scan_model_calls(modules, Resolver(tree))
            if total != 1:
                failures.append(f"{name}: expected 1 call site, scanned {total}")
                continue
            if name in _RED_FIXTURES and not found:
                failures.append(f"{name}: expected a finding and the scan cleared it")
            if name not in _RED_FIXTURES and found:
                failures.append(
                    f"{name}: expected clean, got {found[0].origin.kind}: {found[0].origin.detail}"
                )

        for name, source, expected in (
            ("transcription without a prompt field", _TRANSCRIPTION_CLEAN, 0),
            ("transcription with a prompt field", _TRANSCRIPTION_PROMPTED, 1),
        ):
            tree = _fixture_tree(
                base / name.replace(" ", "_"), {"modules/p/transcription.py": source}
            )
            modules = [tree.load(path) for path in tree.candidates()]
            got = len(scan_transcription_posts(modules))
            if got != expected:
                failures.append(f"{name}: expected {expected} finding(s), got {got}")

        empty = _fixture_tree(base / "empty", {"modules/x/service.py": "VALUE = 1\n"})
        if empty.candidates():
            failures.append(
                "empty tree: a file with no model call was still selected for scanning"
            )

    if not interpreter_is_current():
        failures.append(
            "interpreter floor: this interpreter cannot parse the syntax the backend uses"
        )

    if failures:
        for line in failures:
            print(f"SELF-TEST FAILED: {line}")
        return 1
    print(
        f"self-test OK: {len(_FIXTURES)} classification fixtures, 2 transcription fixtures, 2 floors"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the guard. See the module docstring for the exit codes."""
    parser = argparse.ArgumentParser(description="Prompt provenance guard")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print what was scanned as well as what failed",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the scanner against its own fixtures",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not APP_ROOT.is_dir():
        print(f"CONCLUDED NOTHING: {APP_ROOT} is not a directory", file=sys.stderr)
        return 2
    if not interpreter_is_current():
        print(
            f"CONCLUDED NOTHING: this interpreter is {sys.version.split()[0]} and cannot parse the "
            "syntax the backend uses. Run it on the version the backend targets (3.12+).",
            file=sys.stderr,
        )
        return 2

    tree = Tree(APP_ROOT)
    modules: list[Module] = []
    broken: list[str] = []
    try:
        candidates = tree.candidates()
    except ParseFailure as exc:
        print(f"CONCLUDED NOTHING: {exc}", file=sys.stderr)
        return 2
    for path in candidates:
        try:
            modules.append(tree.load(path))
        except ParseFailure as exc:
            broken.append(str(exc))
    if broken:
        print(
            "CONCLUDED NOTHING: a file that mentions the model call did not parse.",
            file=sys.stderr,
        )
        for line in broken:
            print(f"  {line}", file=sys.stderr)
        return 2

    resolver = Resolver(tree)
    findings, total = scan_model_calls(modules, resolver)
    findings.extend(scan_transcription_posts(modules))

    if total < MIN_EXPECTED_CALL_SITES:
        print(
            f"CONCLUDED NOTHING: found {total} model call sites, expected at least "
            f"{MIN_EXPECTED_CALL_SITES}. The anchor '{MODEL_CALL}' has probably been renamed.",
            file=sys.stderr,
        )
        return 2

    stale = stale_acknowledgements(modules)

    if args.verbose:
        print(
            f"read {len(modules)} of {len(tree.paths)} app modules, {total} model call sites"
        )
        print(
            f"acknowledged: {len(CLEARED_BY_READING)} cleared by reading, {len(RUNTIME_BY_DESIGN)} runtime by design"
        )

    if not findings and not stale:
        print(
            f"prompt provenance OK: {total} model call sites, every instruction slot accounted for"
        )
        return 0

    for f in findings:
        print(f"{f.path}:{f.lineno} in {f.func}()")
        print(f"    {INSTRUCTION_KEYWORD} = {f.source}")
        print(f"    {f.origin.kind.upper()}: {f.origin.detail}")
        if f.origin.kind == "unresolved":
            print(
                "    The scan could not decide. Read it, then fix it or record it in a list."
            )
        else:
            print(
                "    Record it in CLEARED_BY_READING or RUNTIME_BY_DESIGN with the reading that clears it."
            )
    for entry in stale:
        print(f"stale acknowledgement, no call site matches: {entry}")

    print(
        f"\n{len(findings)} unaccounted instruction slot(s), {len(stale)} stale acknowledgement(s)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
