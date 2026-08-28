# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The description of a module, as data.

A module the builder produces is rendered from this specification and nothing
else. That is the whole safety argument: an assistant drafting a module writes
a :class:`ModuleSpec`, never Python, so the worst a bad draft can do is
describe a module that fails validation here. Generated code is a pure
function of a spec that passed.

It also means the builder works with no assistant connected at all. The wizard
collects the same structure by hand, and the same generator renders it.

Every identifier is checked against three things rather than one: the shape of
a Python identifier, a keyword list, and the names the generated code uses for
itself. A field called ``id`` or ``metadata`` parses fine and collides with the
row's own primary key or SQLAlchemy's registry, and the failure would arrive as
a stack trace at import time on the user's server.
"""

from __future__ import annotations

import keyword
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# snake_case, starting with a letter. Trailing and doubled underscores are
# allowed by Python but not by us: they read as typos in a column name.
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

MAX_FIELDS = 40

FieldType = Literal[
    "text",
    "long_text",
    "integer",
    "number",
    "money",
    "date",
    "datetime",
    "boolean",
    "select",
]

# Names the generated module already uses on its own row or that SQLAlchemy and
# Pydantic claim. A user field of any of these names would shadow something.
RESERVED_FIELD_NAMES = frozenset(
    {
        "id",
        "project_id",
        "created_at",
        "updated_at",
        "created_by",
        "metadata",
        "metadata_",
        "registry",
        "query",
        "self",
        "class",
        "schema",
        "model_config",
        "model_fields",
    }
)

# Module directory names the platform owns. A generated module taking one of
# these is invisible - the shipped module wins the import - so it is refused at
# the spec rather than discovered later by its author wondering why nothing
# changed. Checked against the live shipped tree in :func:`reserved_module_keys`.
STATIC_RESERVED_KEYS = frozenset({"core", "modules", "app", "tests", "migrations", "admin"})


def reserved_module_keys() -> frozenset[str]:
    """Directory names already taken by a module that ships with the platform."""
    from pathlib import Path

    names = set(STATIC_RESERVED_KEYS)
    try:
        from app.core.module_loader import MODULES_DIR

        shipped = Path(MODULES_DIR)
        if shipped.is_dir():
            names.update(d.name for d in shipped.iterdir() if d.is_dir())
    except Exception:  # pragma: no cover - shipped tree unreadable
        pass
    return frozenset(names)


def url_prefix_for(key: str) -> str:
    """The path a module with this key is served from.

    The loader mounts a router at the hyphenated form of the module's directory
    name, so a key with an underscore is reachable at a URL without one. The
    frontend renders every generated module from this prefix, and the wizard
    shows it before installing, so the rule lives here rather than being
    reassembled from the key at each caller.
    """
    return f"/api/v1/{key.replace('_', '-')}"


def _check_identifier(value: str, what: str) -> str:
    value = (value or "").strip()
    if not IDENTIFIER_RE.match(value):
        raise ValueError(
            f"{what} must be snake_case: a letter, then letters, digits and single underscores. Got {value!r}."
        )
    if keyword.iskeyword(value) or keyword.issoftkeyword(value):
        raise ValueError(f"{what} {value!r} is a Python keyword.")
    return value


class FieldSpec(BaseModel):
    """One column, one form input, one table cell."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: FieldType = "text"
    required: bool = False
    help_text: str = ""
    unit: str = ""
    # select only. Stored as text; the options are what the form offers and
    # what the generated validator accepts.
    options: list[str] = Field(default_factory=list)
    # Shown in the list view. A table of forty columns is not a table, so the
    # generator caps what it renders and this is how a spec says which matter.
    in_list: bool = True

    @field_validator("name")
    @classmethod
    def _name_is_a_safe_identifier(cls, value: str) -> str:
        value = _check_identifier(value, "field name")
        if value in RESERVED_FIELD_NAMES:
            raise ValueError(
                f"field name {value!r} is reserved - the generated row already has one, and a second would shadow it."
            )
        return value

    @field_validator("label")
    @classmethod
    def _label_is_present(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("every field needs a label - it is what the user reads.")
        return value

    @model_validator(mode="after")
    def _select_has_options(self) -> FieldSpec:
        if self.type == "select":
            cleaned = [o.strip() for o in self.options if o and o.strip()]
            if len(cleaned) < 2:
                raise ValueError(
                    f"field {self.name!r} is a select with {len(cleaned)} option(s). "
                    "A choice of one is not a choice; use text, or add options."
                )
            if len(set(cleaned)) != len(cleaned):
                raise ValueError(f"field {self.name!r} repeats an option.")
            object.__setattr__(self, "options", cleaned)
        elif self.options:
            raise ValueError(f"field {self.name!r} is {self.type} but carries select options.")
        return self


RuleKind = Literal["required", "positive", "not_future", "range", "one_of", "order"]


class RuleSpec(BaseModel):
    """A validation rule, in the platform's own sense: part of the workflow.

    Every module ships rules. A module with none is refused, which is the
    platform's rule 4 expressed where it can actually be enforced.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    kind: RuleKind
    field: str
    # range only
    min_value: float | None = None
    max_value: float | None = None
    # order only: this field must not be later than `other_field`
    other_field: str = ""
    severity: Literal["error", "warning"] = "error"

    @field_validator("code")
    @classmethod
    def _code_shape(cls, value: str) -> str:
        value = (value or "").strip().upper()
        if not re.match(r"^[A-Z][A-Z0-9_]{2,48}$", value):
            raise ValueError(f"rule code {value!r} must be UPPER_SNAKE, 3 to 49 characters.")
        return value

    @field_validator("message")
    @classmethod
    def _message_present(cls, value: str) -> str:
        value = (value or "").strip()
        if len(value) < 4:
            raise ValueError("a rule needs a message a person can act on.")
        return value

    @model_validator(mode="after")
    def _kind_has_what_it_needs(self) -> RuleSpec:
        if self.kind == "range":
            if self.min_value is None and self.max_value is None:
                raise ValueError(f"rule {self.code} is a range with no bound.")
            if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
                raise ValueError(f"rule {self.code} has min above max.")
        if self.kind == "order" and not self.other_field:
            raise ValueError(f"rule {self.code} compares an order but names only one field.")
        return self


class EntitySpec(BaseModel):
    """The one record type a generated module manages."""

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    plural_name: str = ""
    fields: Annotated[list[FieldSpec], Field(min_length=1, max_length=MAX_FIELDS)]
    # Whether rows belong to a project. Almost everything in this product does,
    # and the generated router scopes reads and writes by it when true.
    project_scoped: bool = True

    @field_validator("name")
    @classmethod
    def _entity_name(cls, value: str) -> str:
        return _check_identifier(value, "entity name")

    @model_validator(mode="after")
    def _fields_are_distinct(self) -> EntitySpec:
        names = [f.name for f in self.fields]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate field name(s): {', '.join(duplicates)}")
        if not self.plural_name:
            object.__setattr__(self, "plural_name", f"{self.display_name}s")
        return self


class ModuleSpec(BaseModel):
    """Everything the generator needs, and nothing it does not."""

    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str
    description: str = ""
    category: Literal["community", "integration", "regional"] = "community"
    icon: str = "Boxes"
    version: str = "0.1.0"
    author: str = ""
    entity: EntitySpec
    rules: Annotated[list[RuleSpec], Field(min_length=1, max_length=60)]

    #: Who wrote this specification, as opposed to what the generated module
    #: later does. ``assistant`` means a model drafted it from a sentence;
    #: ``wizard`` means a person filled the form in, which is the default
    #: because a hand-built spec never passes through ``draft_spec``.
    #:
    #: It stays ``assistant`` after a person edits the draft, because that is
    #: what happened: a model wrote the first version and a person changed it.
    #: Clearing it on the first keystroke would report a hand-written spec as
    #: soon as somebody fixed a typo.
    #:
    #: This is a statement about the artefact, not about the module's runtime.
    #: The generated manifest separately declares ``InferenceRole.NONE``, which
    #: remains true: the module the generator emits calls no model.
    drafted_by: Literal["assistant", "wizard"] = "wizard"

    @field_validator("key")
    @classmethod
    def _key_shape(cls, value: str) -> str:
        value = _check_identifier(value, "module key")
        if len(value) < 3:
            raise ValueError("module key is too short to be recognisable.")
        return value

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        value = (value or "").strip()
        if not re.match(r"^\d+\.\d+\.\d+$", value):
            raise ValueError(f"version {value!r} must be MAJOR.MINOR.PATCH.")
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name_present(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("a module needs a name a person can read.")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> ModuleSpec:
        taken = reserved_module_keys()
        if self.key in taken:
            raise ValueError(
                f"module key {self.key!r} is already used by a module that ships with the "
                "platform. The shipped one would win and this one would never load."
            )

        known = {f.name: f for f in self.entity.fields}
        for rule in self.rules:
            if rule.field not in known:
                raise ValueError(f"rule {rule.code} names field {rule.field!r}, which does not exist.")
            if rule.kind == "order":
                if rule.other_field not in known:
                    raise ValueError(f"rule {rule.code} compares against {rule.other_field!r}, which does not exist.")
                if rule.other_field == rule.field:
                    raise ValueError(f"rule {rule.code} compares a field with itself.")
                kinds = {known[rule.field].type, known[rule.other_field].type}
                if not kinds <= {"date", "datetime"}:
                    raise ValueError(f"rule {rule.code} orders fields that are not dates.")
            if rule.kind in {"positive", "range"} and known[rule.field].type not in {
                "integer",
                "number",
                "money",
            }:
                raise ValueError(f"rule {rule.code} is numeric but {rule.field!r} is {known[rule.field].type}.")
            if rule.kind == "one_of" and known[rule.field].type != "select":
                raise ValueError(f"rule {rule.code} restricts choices on a field that has none.")
            if rule.kind == "not_future" and known[rule.field].type not in {"date", "datetime"}:
                raise ValueError(f"rule {rule.code} is about time but {rule.field!r} is not a date.")

        codes = [r.code for r in self.rules]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule code(s): {', '.join(duplicates)}")
        return self

    @property
    def table_name(self) -> str:
        """The physical table. Prefixed like every other table in the platform."""
        return f"oe_{self.key}_{self.entity.name}"

    @property
    def module_name(self) -> str:
        """The manifest name, in the ``oe_*`` namespace the loader expects."""
        return f"oe_{self.key}"

    @property
    def class_name(self) -> str:
        return "".join(part.title() for part in self.entity.name.split("_"))

    @property
    def url_prefix(self) -> str:
        """Where the loader will mount this module's router."""
        return url_prefix_for(self.key)
