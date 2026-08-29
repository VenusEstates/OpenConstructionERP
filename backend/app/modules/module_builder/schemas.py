# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Request and response shapes for the module builder."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.module_builder.spec import ModuleSpec


class DraftRequest(BaseModel):
    """What the user typed in the first step of the wizard."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=10, max_length=4000)


class DraftResponse(BaseModel):
    """A spec the wizard can now show, edit and install. Nothing was written."""

    spec: ModuleSpec
    source: str = Field(description="assistant when drafted from a sentence, wizard when filled in by hand")


class PreviewFile(BaseModel):
    path: str
    lines: int
    content: str


class PreviewRequest(BaseModel):
    """Ask what a spec would generate. Changes nothing on the server."""

    model_config = ConfigDict(extra="forbid")

    spec: ModuleSpec


class PreviewResponse(BaseModel):
    """Everything that would land on disk, before any of it does."""

    spec: ModuleSpec
    files: list[PreviewFile]
    total_lines: int
    # Shown in the review step, so the URL is known before install rather than
    # discovered afterwards.
    base_path: str
    # Proof, for the install call that follows, that these files were rendered
    # for this person to read. Install will not write a spec that arrives
    # without one, which is what makes the review step a rule rather than a
    # screen. See :mod:`app.modules.module_builder.review_token`.
    review_token: str


class InstallRequest(BaseModel):
    """Install a spec that has been previewed.

    Deliberately not the same body as :class:`PreviewRequest`. The two endpoints
    used to take an identical payload, so an install carried no evidence that
    anyone had looked at the generated code; the extra field is that evidence,
    and keeping the types apart is what stops them drifting back together.
    """

    model_config = ConfigDict(extra="forbid")

    spec: ModuleSpec
    review_token: str


class InstalledModuleRead(BaseModel):
    key: str
    module_name: str
    display_name: str
    version: str
    generated_at: str
    entity: str
    field_count: int
    rule_count: int
    # The screen fetches ``{base_path}/ui-spec`` and everything else under it.
    base_path: str


class InstalledList(BaseModel):
    items: list[InstalledModuleRead]
    total: int
    runtime_root: str


class UninstallResponse(BaseModel):
    key: str
    module_name: str
    removed: bool
    data_dropped: bool


class FieldTypeInfo(BaseModel):
    """One choice in the wizard's field-type list."""

    type: str
    label: str
    hint: str


class RuleKindInfo(BaseModel):
    kind: str
    label: str
    hint: str
    applies_to: list[str]
    needs_other_field: bool = False
    needs_bounds: bool = False


class VocabularyResponse(BaseModel):
    """What the wizard is allowed to offer, read from the spec rather than copied.

    The frontend would otherwise carry its own list of field types and rule
    kinds, and the two would drift the first time one is added here.
    """

    field_types: list[FieldTypeInfo]
    rule_kinds: list[RuleKindInfo]
    reserved_field_names: list[str]
    reserved_keys: list[str]
    max_fields: int
    assistant_available: bool
