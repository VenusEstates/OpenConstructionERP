# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""A price analysis has to come out in the shape the estimator's market expects.

``Preset`` has carried a ``region`` field since the presets were written. It is
declared on the dataclass, it is serialised into ``to_dict`` for the UI, and
until now nothing anywhere resolved a preset from it. The endpoint took the
preset by name with ``"international"`` as its default, so a Hungarian
estimator opening a price analysis got the international shape unless they
knew to type ``preset=hu_anyag_dij`` into a query string.

That is not a smaller version of the right answer. A Hungarian bill quotes
every line twice, as anyag and dij, and the split is what the reader compares
tenders on; a single unit rate is a different document, not a plainer one. The
same is true of the German EFB sheet, the UK NRM grouping and the US bid
breakdown. Four of the six presets existed and were unreachable without prior
knowledge of their slugs, which is the same defect the markup region had: the
platform knew the market's convention and never asked itself which market it
was in.

The population check at the bottom is the one that would have caught this. A
preset that declares a region and that no country resolves to is a convention
we wrote down and cannot deliver.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.modules.price_breakdown.presets import PRESETS, preset_for_country

#: Markets whose own convention the presets claim to implement, and the preset
#: each must resolve to. Written out rather than derived from the table, so
#: that a preset silently retagged to another region fails here instead of
#: agreeing with itself.
EXPECTED = {
    "HU": "hu_anyag_dij",
    "DE": "efb",
    "GB": "nrm",
    "US": "us_bid",
}

#: The neutral answer, for a market with no preset and for no market at all.
NEUTRAL = "international"


@pytest.mark.parametrize(("country", "preset"), sorted(EXPECTED.items()))
def test_a_market_with_a_convention_gets_it(country: str, preset: str) -> None:
    """Each market that has a preset resolves to that preset, not the neutral one."""
    resolved = preset_for_country(country)
    assert resolved == preset, (
        f"a price analysis for a {country} project is rendered with the {resolved!r} preset "
        f"instead of {preset!r}, so the estimator gets a document laid out for another market."
    )


def test_the_uk_is_reachable_by_its_iso_code_as_well_as_its_tag() -> None:
    """The preset is tagged UK, the column holds GB, and both have to work.

    This product has always tagged the British convention "UK" while the ISO
    code for the country is "GB". The alias exists because of that and not
    because either spelling is wrong, and the check is here because a resolver
    that only understood its own tag would answer "no convention" for every
    real British project, whose column says GB.
    """
    assert preset_for_country("GB") == "nrm"
    assert preset_for_country("UK") == "nrm"


def test_case_and_whitespace_do_not_change_the_answer() -> None:
    """The country column is not validated on the way in, so the reader normalises."""
    assert preset_for_country("hu") == preset_for_country("HU")
    assert preset_for_country("  de  ") == preset_for_country("DE")


@pytest.mark.parametrize("country", [None, "", "   ", "ZZ", "FR", "IT"])
def test_a_market_with_no_preset_gets_the_neutral_one(country: str | None) -> None:
    """No preset for this market and no market at all are the same answer.

    The neutral preset is the one that assumes nothing, so it is the honest
    result for both. What must not happen is a market being handed the nearest
    preset that looks close enough.
    """
    assert preset_for_country(country) == NEUTRAL


@pytest.mark.parametrize("country", ["AT", "CH"])
def test_the_german_sheet_is_not_handed_to_its_neighbours(country: str) -> None:
    """Austria and Switzerland share German with the EFB preset, not a form.

    EFB 221/222/223 are the German federal procurement sheets. An Austrian or
    Swiss bill is not laid out that way, and handing them the German preset
    because the words on it are readable is exactly the substitution the
    neutral preset exists to avoid. Named here as an exception with a test
    rather than left to be inferred from the absence of a mapping, because the
    absence looks identical to an oversight.
    """
    assert preset_for_country(country) == NEUTRAL, (
        f"{country} resolves to the German EFB preset. A shared language is not a shared bill form."
    )


def test_every_preset_that_claims_a_market_can_be_reached_from_it() -> None:
    """No preset may declare a region that no country resolves to.

    This is the assertion that names the original defect. Four presets declared
    a region, the field had no reader at all, and the only way to reach any of
    them was to know its slug. A preset nobody can reach is a convention we
    wrote down and cannot deliver.
    """
    claimed = {name: p.region for name, p in PRESETS.items() if p.region and p.region.lower() != NEUTRAL}
    print(f"{len(claimed)} of {len(PRESETS)} presets claim a market: {claimed}")
    assert claimed, "no preset claims a market at all, so this file is checking nothing"

    unreachable = {name: region for name, region in claimed.items() if preset_for_country(region) != name}
    assert not unreachable, (
        f"presets that declare a market no country resolves to: {unreachable}. Either the region "
        f"tag is not a country code the resolver understands, or two presets claim one market and "
        f"one of them is being shadowed."
    )


def test_two_presets_do_not_quietly_claim_one_market() -> None:
    """A duplicate region would make one of the pair unreachable, silently.

    The index is built by comprehension, so a second preset declaring an
    existing region overwrites the first and nothing says so. Two presets do
    share the ``international`` tag today, which is why that value is excluded
    from the index rather than treated as a market.
    """
    regions = [p.region.upper() for p in PRESETS.values() if p.region and p.region.lower() != NEUTRAL]
    duplicates = sorted({r for r in regions if regions.count(r) > 1})
    assert not duplicates, (
        f"more than one preset claims each of {duplicates}. The region index keeps the last one "
        f"declared and the others become unreachable without any error."
    )


def test_the_endpoint_asks_the_project_when_no_preset_is_named() -> None:
    """The resolver being right is worthless if the endpoint never calls it.

    The whole defect was a correct preset that nothing selected, so a test of
    the resolver alone would have passed on the broken tree. This reads the
    endpoint: its ``preset`` parameter must accept None, and the body must
    reach the shared resolver rather than defaulting to a name.
    """
    from app.modules.boq.router import get_position_price_analysis

    tree = ast.parse(textwrap.dedent(inspect.getsource(get_position_price_analysis)))
    func = tree.body[0]
    assert isinstance(func, ast.AsyncFunctionDef)

    args = {a.arg: a for a in func.args.args + func.args.kwonlyargs}
    preset_arg = args.get("preset")
    assert preset_arg is not None, "the price-analysis endpoint no longer takes a preset"
    assert preset_arg.annotation is not None
    annotation = ast.unparse(preset_arg.annotation)
    assert "None" in annotation, (
        f"preset is annotated {annotation!r}. It has to accept None, which is how a caller says "
        "'decide from the project'. A string default puts one market's shape back on every bill."
    )

    called = {ast.unparse(node.func) for node in ast.walk(func) if isinstance(node, ast.Call)}
    assert "preset_for_country" in called, (
        "the price-analysis endpoint does not call preset_for_country, so the preset is either "
        "fixed again or resolved from a second copy of the market table."
    )
