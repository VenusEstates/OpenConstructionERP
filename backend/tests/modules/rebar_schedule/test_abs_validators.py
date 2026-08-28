# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The ``bvbs_abs`` validation rules.

Every rule is exercised twice: once against data the guideline itself prints,
where it must pass, and once against data built here to break exactly that
rule, where it must fail. A rule tested only on conforming data cannot tell
whether it looks at anything at all.
"""

from decimal import Decimal

import pytest

from app.core.validation.engine import RuleResult, Severity, ValidationContext
from app.modules.rebar_schedule.abs_format import compute_checksum, parse_record
from app.modules.rebar_schedule.validators import (
    RULE_SET,
    RULES,
    AbsAsciiOnly,
    AbsBendRadiusOverRoller,
    AbsChecksumValid,
    AbsDevelopedLengthMatchesHeader,
    AbsGeometryAngleTerminated,
    AbsGeometryExcludesSpacer,
    AbsHeaderFieldOrder,
    AbsMeshCoordinatesNonNegative,
    AbsRecordLengthBudget,
)
from tests.modules.rebar_schedule import spec_examples


def _context(*records: str) -> ValidationContext:
    """A context over records given as text, parsed in file order."""
    return ValidationContext(
        data={"records": [parse_record(text, line_no=n) for n, text in enumerate(records, start=1)]},
        metadata={"locale": "en"},
    )


def _sealed(body: str) -> str:
    """Append the checksum block a body needs, so a fixture is well-formed.

    Built here rather than copied, so a fixture aimed at one rule does not
    trip the checksum rule as a side effect.
    """
    return f"{body}C{compute_checksum(body + 'C')}@"


def _failures(results: list[RuleResult]) -> list[RuleResult]:
    return [item for item in results if not item.passed]


# ── Checksum ───────────────────────────────────────────────────────────────


async def test_checksum_rule_passes_every_record_the_guideline_prints() -> None:
    results = await AbsChecksumValid().validate(_context(*spec_examples.VERIFIED.values()))
    assert len(results) == len(spec_examples.VERIFIED)
    assert _failures(results) == []


async def test_checksum_rule_catches_an_altered_length() -> None:
    damaged = spec_examples.VERIFIED["bf2d-1"].replace("@l1000@", "@l1001@")
    failures = _failures(await AbsChecksumValid().validate(_context(damaged)))
    assert len(failures) == 1
    assert failures[0].severity is Severity.ERROR
    assert "72" in failures[0].message


async def test_checksum_rule_catches_a_record_with_no_checksum_block() -> None:
    stripped = spec_examples.VERIFIED["bf2d-1"].rsplit("@C", 1)[0] + "@"
    assert _failures(await AbsChecksumValid().validate(_context(stripped)))


# ── Header ─────────────────────────────────────────────────────────────────


async def test_header_rule_passes_one_conforming_example_per_super_group() -> None:
    conforming = [
        spec_examples.VERIFIED[label]
        for label in ("bf2d-1", "bf3d-1", "bfwe-1-square-column", "bfma-1-stock-mesh", "bfau-1-spacer-strip")
    ]
    assert _failures(await AbsHeaderFieldOrder().validate(_context(*conforming))) == []


async def test_header_rule_reports_a_missing_field() -> None:
    """Dropping the steel grade from a planar shape leaves the header short."""
    body = "BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@s48@v@Gl400@w90@l600@w0@"
    failures = _failures(await AbsHeaderFieldOrder().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert "g" in failures[0].details["missing"]


async def test_header_rule_reports_fields_out_of_order() -> None:
    """Every field present, two of them swapped.

    This is the failure the rule exists for: a reader that walks the header
    positionally mis-assigns the diameter and the weight and reports nothing.
    """
    body = "BF2D@HjTestPDF@r417@ia@p1@l1000@n10@d12@e0.888@gB500A@s48@v@Gl400@w90@l600@w0@"
    failures = _failures(await AbsHeaderFieldOrder().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].details["out_of_order"] is True
    assert failures[0].details["missing"] == []


async def test_header_rule_reports_a_record_with_no_header_block() -> None:
    failures = _failures(await AbsHeaderFieldOrder().validate(_context(_sealed("BF2D@Gl400@w90@l600@w0@"))))
    assert len(failures) == 1
    assert "header block" in failures[0].message


# ── Block combinations ─────────────────────────────────────────────────────


async def test_spacer_rule_passes_a_record_that_carries_only_spacers() -> None:
    results = await AbsGeometryExcludesSpacer().validate(_context(spec_examples.VERIFIED["bf2d-11-spacers"]))
    assert _failures(results) == []


async def test_spacer_rule_catches_geometry_and_spacers_on_one_line() -> None:
    body = "BF2D@HjTestPDF@r417@ia@p1@l1334@n1@e1.1@d10@gIV@s40@v@Gl1334@w0@At6@p140@p667@"
    failures = _failures(await AbsGeometryExcludesSpacer().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].severity is Severity.ERROR


# ── ASCII and length ───────────────────────────────────────────────────────


async def test_ascii_rule_passes_the_guidelines_records() -> None:
    results = await AbsAsciiOnly().validate(_context(*spec_examples.VERIFIED.values()))
    assert _failures(results) == []


async def test_ascii_rule_catches_an_umlaut_in_a_free_text_field() -> None:
    body = "BF2D@HjHalle S\xfcd@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@w0@"
    failures = _failures(await AbsAsciiOnly().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].details["characters"] == ["\xfc"]


async def test_length_rule_passes_records_inside_the_budget() -> None:
    results = await AbsRecordLengthBudget().validate(_context(*spec_examples.VERIFIED.values()))
    assert _failures(results) == []


async def test_length_rule_reports_an_oversized_record_as_information_only() -> None:
    """Over budget is a quality note, not an error: the file still works."""
    padding = "@".join(f"l{n}@w0" for n in range(1, 200))
    body = f"BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@G{padding}@"
    failures = _failures(await AbsRecordLengthBudget().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].severity is Severity.INFO
    assert failures[0].details["length"] > 1000


# ── Geometry ───────────────────────────────────────────────────────────────


async def test_angle_rule_passes_the_guidelines_planar_shapes() -> None:
    planar = [text for text in spec_examples.VERIFIED.values() if text.startswith(("BF2D", "BFWE", "BFMA"))]
    assert _failures(await AbsGeometryAngleTerminated().validate(_context(*planar))) == []


async def test_angle_rule_catches_a_bar_that_ends_without_an_explicit_zero() -> None:
    """The standard asks for w0 on a bar that ends straight."""
    body = "BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@"
    failures = _failures(await AbsGeometryAngleTerminated().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].details["legs"] == [2]


async def test_radius_rule_passes_the_guidelines_arcs() -> None:
    arcs = [spec_examples.VERIFIED[label] for label in ("bf2d-4-arc", "bf2d-5-arc-opening-angle")]
    assert _failures(await AbsBendRadiusOverRoller().validate(_context(*arcs))) == []


async def test_radius_rule_catches_a_radius_the_stated_roller_cannot_produce() -> None:
    """A 48 mm roller cannot draw a 20 mm inner radius."""
    body = "BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@Gl400@w0@r20@w90@w0@l400@w0@"
    failures = _failures(await AbsBendRadiusOverRoller().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].details["legs"] == [2]
    assert failures[0].details["limit_mm"] == "24"


async def test_developed_length_rule_passes_the_guidelines_planar_shapes() -> None:
    planar = [text for text in spec_examples.VERIFIED.values() if text.startswith("BF2D")]
    assert _failures(await AbsDevelopedLengthMatchesHeader().validate(_context(*planar))) == []


async def test_developed_length_rule_catches_a_leg_that_does_not_add_up() -> None:
    """A header saying 1000 mm over legs of 400 and 300 is 300 mm of steel short."""
    body = "BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l300@w0@"
    failures = _failures(await AbsDevelopedLengthMatchesHeader().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].severity is Severity.WARNING
    assert Decimal(failures[0].details["developed_mm"]) == Decimal(700)


async def test_developed_length_rule_tolerates_the_rounding_an_arc_introduces() -> None:
    """400 mm through 90 degrees develops to 628.319, and the header says 1428."""
    parsed = parse_record(spec_examples.VERIFIED["bf2d-4-arc"])
    assert parsed.header_number("l") == Decimal(1428)
    assert _failures(await AbsDevelopedLengthMatchesHeader().validate(_context(parsed.raw))) == []


async def test_mesh_rule_passes_the_guidelines_meshes() -> None:
    meshes = [text for text in spec_examples.VERIFIED.values() if text.startswith("BFMA")]
    assert _failures(await AbsMeshCoordinatesNonNegative().validate(_context(*meshes))) == []


async def test_mesh_rule_catches_a_negative_bar_coordinate() -> None:
    body = "BFMA@HjTestPDF@r417@ia@p1@l5000@n10@e36.695@gB500A@s48@mZMPDF@b3000@v@Xd5@x-500@y250@l2500@e250,1@"
    failures = _failures(await AbsMeshCoordinatesNonNegative().validate(_context(_sealed(body))))
    assert len(failures) == 1
    assert failures[0].details["coordinates"] == ["Xx=-500"]


async def test_mesh_rule_reads_both_halves_of_a_double_bar() -> None:
    """A double bar carries two values separated by a semicolon; both count."""
    body = "BFMA@HjTestPDF@r417@ia@p1@l5000@n10@e36.695@gB500A@s48@mZMPDF@b3000@v@Yd6d@x175;-175@y600;600@l4400@"
    failures = _failures(await AbsMeshCoordinatesNonNegative().validate(_context(_sealed(body))))
    assert failures[0].details["coordinates"] == ["Yx=-175"]


async def test_mesh_rule_ignores_a_planar_shape() -> None:
    """The mesh coordinate rule says nothing about a bent bar."""
    results = await AbsMeshCoordinatesNonNegative().validate(_context(spec_examples.VERIFIED["bf2d-1"]))
    assert results == []


# ── The rule set as a whole ────────────────────────────────────────────────


def test_every_rule_belongs_to_the_rule_set_and_has_a_unique_id() -> None:
    ids = [rule.rule_id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.standard == RULE_SET for rule in RULES)
    assert all(rule.rule_id.startswith(f"{RULE_SET}.") for rule in RULES)


@pytest.mark.parametrize("rule_class", RULES)
async def test_no_rule_reports_a_finding_against_the_guidelines_own_test_data(rule_class: type) -> None:
    """The published test data is the closest thing to a conformance suite.

    A rule that fires on it is wrong about the format, not about the file -
    with one documented exception, the spacer example, whose header the
    guideline itself leaves incomplete.
    """
    clean = [
        text for label, text in spec_examples.VERIFIED.items() if label not in spec_examples.HEADER_INCOMPLETE_EXAMPLES
    ]
    results = await rule_class().validate(_context(*clean))
    assert _failures(results) == [], rule_class.rule_id


async def test_the_spacer_example_is_the_one_published_record_with_an_incomplete_header() -> None:
    """Pinned, not corrected.

    The guideline's text is unambiguous that every identifier applicable to a
    super-group must be present, and its spacer example omits two of them. The
    rule follows the text; this test records that the example does not, so a
    later reader does not "fix" the rule to match the example.
    """
    findings = {}
    for label, text in spec_examples.VERIFIED.items():
        for result in _failures(await AbsHeaderFieldOrder().validate(_context(text))):
            findings[label] = result.details["missing"]
    expected = dict.fromkeys(
        spec_examples.HEADER_INCOMPLETE_EXAMPLES,
        spec_examples.HEADER_INCOMPLETE_MISSING_FIELDS,
    )
    assert findings == expected


@pytest.mark.parametrize("rule_class", RULES)
async def test_every_rule_survives_an_empty_context(rule_class: type) -> None:
    assert await rule_class().validate(ValidationContext(data={"records": []})) == []


@pytest.mark.parametrize("rule_class", RULES)
async def test_every_rule_carries_a_translated_message_for_each_finding(rule_class: type) -> None:
    """A finding with a raw key in it has no message bundle behind it."""
    damaged = _sealed("BF2D@HjHalle S\xfcd@r417@ia@p1@l1000@n10@d12@gB500A@s48@v@Gl400@w90@l600@")
    broken = damaged.replace("C" + damaged.rsplit("@C", 1)[1].rstrip("@"), "C65")
    for result in await rule_class().validate(_context(broken)):
        assert not result.message.startswith(RULE_SET), result.message
        if not result.passed:
            assert result.suggestion
            assert not result.suggestion.startswith(RULE_SET)
