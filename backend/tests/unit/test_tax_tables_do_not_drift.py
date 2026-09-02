"""Four tax tables, hand-maintained, that can silently disagree.

The platform carries VAT rates in four unrelated places:

* ``app/core/tax.py`` - a Python dict, 22 countries, read by the country packs.
* ``app/modules/property_dev/data/tax_rates.yaml`` - 12 jurisdictions, and the
  one that actually feeds the property development tax quote panel. Its rate
  classes live under ``vat`` and, for SG, AU and IN, under ``gst``; both are
  read here, because a rate is a rate whatever the block it sits in is called.
* ``app/modules/i18n_foundation/seed_data/tax_configurations.json`` - 40
  countries, effective-dated, and the only one with any history.
* ``app/modules/methodology/templates.py`` - the methodology catalogue's
  ``vat_rate``, 53 countries, and the figure a project's cascade actually
  charges VAT at once a country template is installed.

Why the fourth table was added, which is the whole argument for it
------------------------------------------------------------------
It was measured, on 2026-09-02. Israel raised standard VAT from 17 % to 18 % on
2025-01-01. The methodology catalogue was updated and said 18. The seed file
was not: it went on carrying 17 with no ``effective_to``, so it read as the
rate in force, and the platform stated two different rates for one country on
one date. Nothing was red, because the three tables above happen not to carry
Israel at all - neither ``core/tax.py`` nor the yaml has an IL row - so the
seed's stale figure had no counterpart to disagree with, and the one table that
held the right answer was not in the comparison.

That is the failure this file was written against, arriving through a table
nobody had added rather than through a rate nobody had updated. The catalogue
is the platform's most-maintained rate list, because it is the one somebody
edits when they add a country, and leaving it out meant the check was blind
wherever the other three were silent.

``core/tax.py`` says so in its own docstring: "Nothing currently checks that
the two agree ... so the two can drift apart silently. Treat that as a known
gap, not as a guarantee." This is that check. It does not unify them - which
is the source of truth is a design decision nobody has taken - it only makes a
disagreement impossible to ship without somebody seeing it.

Two things this deliberately does NOT do.

It does not convict on absence. Most countries appear in one table and not
another, by design: the yaml carries stamp duty for jurisdictions with no VAT
block at all, and ``core/tax.py`` leaves out Brazil and the United States on
purpose. A gate that fired on absence would be red from birth, and a gate that
is always red teaches everyone to ignore it.

It does not compare sub-national rates. The other tables are keyed by country
and cannot express a province, so only rows marked ``national`` are comparable
at all. Canada's federal GST is not "Canada's VAT rate" in the sense the other
tables mean, and comparing them would be a category error.

And it does not assert blanket equality across the catalogue, because that
would be false. See :data:`_CATALOGUE_DIVERGES`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.core.tax import VATNotApplicable, get_vat_rate, list_covered_countries
from app.modules.methodology.templates import TEMPLATES

_BACKEND = Path(__file__).resolve().parents[2]
_SEED = _BACKEND / "app" / "modules" / "i18n_foundation" / "seed_data" / "tax_configurations.json"
_YAML = _BACKEND / "app" / "modules" / "property_dev" / "data" / "tax_rates.yaml"
_CORE = _BACKEND / "app" / "core" / "tax.py"
_CATALOGUE = _BACKEND / "app" / "modules" / "methodology" / "templates.py"

# Seed ``tax_code`` values that name one of the three rate classes the other
# two tables also carry. This mapping is the soft spot in the whole check: get
# it wrong and the comparison silently shrinks rather than failing, which is
# the vacuous pass this file exists to avoid. ``test_every_seed_tax_code_is_
# classified`` is what keeps it honest.
_CLASS_OF: dict[str, str] = {
    # Headline rate, under each country's own name for it.
    "AFA": "standard",
    "ALV": "standard",
    "BTW": "standard",
    "CT": "standard",
    "DDS": "standard",
    "DPH": "standard",
    "GST": "standard",
    "IVA": "standard",
    "KDV": "standard",
    "MOMS": "standard",
    "MVA": "standard",
    "NDS": "standard",
    "PDV": "standard",
    "TVA": "standard",
    "VAT": "standard",
    # India has four GST bands and no single headline rate. ``core/tax.py``
    # declares the standard one to be 18 %, so that is the band compared;
    # the other three have nothing to compare against.
    "GST_18": "standard",
    # Reduced rate.
    "ALV_RED": "reduced",
    "BTW_RED": "reduced",
    "CT_RED": "reduced",
    "DPH_RED": "reduced",
    "IVA_RED": "reduced",
    "KDV_RED": "reduced",
    "MOMS_RED": "reduced",
    "MVA_RED": "reduced",
    "NDS_RED": "reduced",
    "PDV_RED": "reduced",
    "TVA_RED": "reduced",
    "VAT_RED": "reduced",
    "VAT_REDUCED": "reduced",
    "VAT_ZERO": "zero",
}

# Codes with no counterpart in a country-keyed table, and why. Listed rather
# than skipped by a wildcard so that a new code has to be thought about.
_NOT_COMPARABLE: dict[str, str] = {
    "GST_5": "one of India's four GST bands; no single-rate counterpart",
    "GST_12": "one of India's four GST bands; no single-rate counterpart",
    "GST_28": "one of India's four GST bands; no single-rate counterpart",
    "TVA_INT": "France's intermediate 10 % tier; neither standard nor reduced",
    "VAT_SPECIAL": "Swiss accommodation rate; no counterpart class",
    "ICMS_SP": "Brazilian state ICMS; sub-national, and no federal row exists",
    "ISS": "Brazilian municipal service tax; sub-national",
    "NONE": "sentinel meaning the country levies no such tax",
    "HST_ON": "Canadian provincial",
    "HST_NS": "Canadian provincial",
    "HST_NB": "Canadian provincial",
    "HST_NL": "Canadian provincial",
    "HST_PE": "Canadian provincial",
    "QST_QC": "Canadian provincial",
    "PST_BC": "Canadian provincial",
    "PST_SK": "Canadian provincial",
    "RST_MB": "Canadian provincial",
    "CA_SALES": "United States state sales tax; sub-national",
}


def _seed_rows() -> list[dict]:
    return json.loads(_SEED.read_text(encoding="utf-8"))


def _seed_rates() -> dict[tuple[str, str], Decimal]:
    """Currently active, country-wide seed rates, as fractions."""
    out: dict[tuple[str, str], Decimal] = {}
    for row in _seed_rows():
        if row["effective_to"] is not None:
            continue
        if row["combination"] != "national":
            continue
        cls = _CLASS_OF.get(row["tax_code"] or "")
        if cls is None:
            continue
        out[(row["country_code"], cls)] = Decimal(row["rate_pct"]) / Decimal("100")
    return out


def _seed_history() -> dict[tuple[str, str], list[tuple[Decimal, str]]]:
    """Closed seed periods, so a stale value can be named as stale."""
    out: dict[tuple[str, str], list[tuple[Decimal, str]]] = {}
    for row in _seed_rows():
        if row["effective_to"] is None or row["combination"] != "national":
            continue
        cls = _CLASS_OF.get(row["tax_code"] or "")
        if cls is None:
            continue
        rate = Decimal(row["rate_pct"]) / Decimal("100")
        out.setdefault((row["country_code"], cls), []).append((rate, row["effective_to"]))
    return out


#: Countries where the catalogue and the seed carry different figures on
#: purpose, each with the reason it is not drift.
#:
#: This is an exception list rather than an allowlist, and the difference is
#: enforced: ``test_every_named_catalogue_exception_still_diverges`` fails when
#: an entry here stops disagreeing, so a country cannot be left excused after
#: the reason for excusing it has gone. That is the failure mode of every
#: "known exceptions" list - it silently grows into a list of things nobody
#: checks - and it is the one thing that would make this file worse than not
#: having the fourth table at all.
#:
#: The two sources are answering different questions, which is why a blanket
#: equality assertion across the catalogue would be false rather than merely
#: strict. The seed's ``is_default`` row is documented as a country's STANDARD
#: rate: the headline figure, the one a general supply is charged at. The
#: catalogue's ``vat_rate`` is the rate a bill of quantities is priced at,
#: which is the standard rate in almost every country and is not in a country
#: that puts construction on a tier of its own. Where the two coincide - which
#: is everywhere but here - a disagreement is drift and this file says so.
_CATALOGUE_DIVERGES: dict[str, str] = {
    "CN": (
        "China's headline VAT rate is 13 %, which is what the seed row flagged is_default carries "
        "and what the seed's VAT_RED row at 9 % is reduced from. Construction and building "
        "services are charged at that 9 % tier, so 9 is the rate a bill of quantities is priced "
        "at and the rate the methodology template quotes. Both figures are right about different "
        "questions, and the platform needs both."
    ),
}


def _catalogue_rates_raw() -> dict[tuple[str, str], Decimal]:
    """Every standard rate the methodology catalogue carries, exceptions included.

    Only ``(country, "standard")`` keys exist here: a methodology carries one
    VAT percentage and no reduced tier, so there is nothing else to compare.

    ``vat_rate`` is tested against ``None`` rather than for truthiness. Qatar,
    Bahrain's neighbours and the other zero-VAT templates carry the string
    "0", which is a rate this platform quotes and not an absence; ``if not
    rate`` would drop precisely those and the comparison would go quiet on the
    countries where a wrong rate is most obvious.
    """
    out: dict[tuple[str, str], Decimal] = {}
    for template in TEMPLATES:
        country = template.get("country_code")
        rate = template.get("vat_rate")
        if not country or rate is None:
            continue
        key = (str(country).upper(), "standard")
        value = Decimal(str(rate)) / Decimal("100")
        if key in out and out[key] != value:
            raise AssertionError(
                f"{key[0]} has two methodology templates quoting different VAT rates "
                f"({out[key] * 100} and {value * 100}). A country can hold two templates - Chile "
                f"and Colombia each ship a flat one and an APU one - but not two opinions about "
                f"its VAT rate, and which one this comparison saw would depend on catalogue order."
            )
        out[key] = value
    return out


def _catalogue_rates() -> dict[tuple[str, str], Decimal]:
    """The catalogue's rates, less the countries excused in :data:`_CATALOGUE_DIVERGES`."""
    return {key: value for key, value in _catalogue_rates_raw().items() if key[0] not in _CATALOGUE_DIVERGES}


def _core_rates() -> dict[tuple[str, str], Decimal]:
    out: dict[tuple[str, str], Decimal] = {}
    for country in list_covered_countries():
        for cls in ("standard", "reduced", "zero"):
            try:
                out[(country, cls)] = Decimal(get_vat_rate(country, cls))
            except VATNotApplicable:
                continue
    return out


def _declares_a_rate(entry: object) -> bool:
    """Whether a yaml rate class states a rate at all, in any shape it may be written in."""
    if isinstance(entry, list):
        return any(isinstance(period, dict) and "rate" in period for period in entry)
    if isinstance(entry, dict):
        return "rate" in entry
    return entry is not None


def _current_yaml_rate(entry: object) -> Decimal | None:
    """The rate a yaml class charges today, from either of the two shapes it uses.

    A class is either a single mapping or a list of dated periods written
    oldest first. The newest period is the comparable one, because the other
    two tables carry current rates and nothing else: ``core/tax.py`` has no
    dates at all and only the seed's open rows are read.

    Returns None for a class that states no rate anywhere, such as the UAE and
    Russian ``exempt`` entries, which name what they apply to and have nothing
    to compare against.
    """
    if isinstance(entry, list):
        periods = [period for period in entry if isinstance(period, dict) and "rate" in period]
        if not periods:
            return None
        newest = max(periods, key=lambda period: str(period.get("effective_from") or ""))
        return Decimal(str(newest["rate"]))
    if isinstance(entry, dict) and "rate" in entry:
        return Decimal(str(entry["rate"]))
    return None


#: The yaml keys that hold rate classes. ``gst`` is here because leaving it
#: out was a real hole rather than a hypothetical one: SG and AU carry their
#: standard rate under ``gst``, the other two tables carry the same rate for
#: the same country, and this comparison never put the pairs together. A scope
#: written as a key name is blind to the sibling key holding the same thing,
#: which is the defect this whole file exists to catch, one layer up.
_RATE_BLOCKS = ("vat", "gst")


def _yaml_rates() -> dict[tuple[str, str], Decimal]:
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], Decimal] = {}
    for country, block in (doc.get("jurisdictions") or {}).items():
        for block_key in _RATE_BLOCKS:
            for cls, entry in ((block or {}).get(block_key) or {}).items():
                rate = _current_yaml_rate(entry)
                if rate is None:
                    continue
                assert (country, cls) not in out, (
                    f"{country}.{cls} is declared under more than one of {_RATE_BLOCKS}, so one "
                    f"of them would silently win here. Decide which block owns it."
                )
                out[(country, cls)] = rate
    return out


def _tables() -> list[tuple[str, dict[tuple[str, str], Decimal]]]:
    return [
        (str(_CORE.relative_to(_BACKEND)), _core_rates()),
        (str(_YAML.relative_to(_BACKEND)), _yaml_rates()),
        (str(_SEED.relative_to(_BACKEND)), _seed_rates()),
        (str(_CATALOGUE.relative_to(_BACKEND)), _catalogue_rates()),
    ]


def test_every_seed_tax_code_is_classified() -> None:
    """A new tax code must be classified or excluded, never silently dropped.

    Without this, adding a code the mapping does not know shrinks the compared
    population and the drift check keeps passing on less and less.
    """
    codes = {row["tax_code"] for row in _seed_rows() if row["tax_code"]}

    unaccounted = sorted(codes - set(_CLASS_OF) - set(_NOT_COMPARABLE))

    assert unaccounted == [], (
        f"{len(unaccounted)} tax codes are neither classified nor excluded: {unaccounted}. "
        f"Add each to _CLASS_OF (it names a standard/reduced/zero rate) or to "
        f"_NOT_COMPARABLE (with the reason it has no counterpart)."
    )


def _disagreements(
    tables: list[tuple[str, dict[tuple[str, str], Decimal]]],
    history: dict[tuple[str, str], list[tuple[Decimal, str]]],
) -> tuple[list[str], list[str]]:
    """Compare every key two or more tables carry.

    Returns ``(population, disagreements)`` - the keys actually compared and
    the ones that differ. This is the whole gate, and it is a plain function
    precisely so the controls below can call it on perturbed input rather
    than re-implementing the comparison and proving only that ``!=`` works.
    """
    population: list[str] = []
    disagreements: list[str] = []

    for key in sorted(set().union(*[set(t) for _, t in tables])):
        present = [(name, table[key]) for name, table in tables if key in table]
        if len(present) < 2:
            continue  # Absence is not disagreement.
        population.append(f"  {key[0]} {key[1]:<9} " + "  ".join(f"{n.split('/')[-1]}={v}" for n, v in present))
        for i, (name_a, value_a) in enumerate(present):
            for name_b, value_b in present[i + 1 :]:
                if value_a == value_b:
                    continue
                note = ""
                for old_rate, ended in history.get(key, []):
                    if value_a == old_rate:
                        note = f" - {name_a} looks stale: that was the rate until {ended}"
                    elif value_b == old_rate:
                        note = f" - {name_b} looks stale: that was the rate until {ended}"
                disagreements.append(f"  {key[0]} {key[1]}: {name_a} says {value_a}, {name_b} says {value_b}{note}")

    return population, disagreements


def census() -> str:
    """Every count this check has, in one printable line.

    Runnable rather than described, because the counts are what a reader
    needs and a paragraph telling somebody how to rebuild them is what goes
    stale. "0 disagreements" says nothing until it is read beside how many
    keys were compared and how many each table holds.
    """
    tables = _tables()
    population, disagreements = _disagreements(tables, _seed_history())
    sizes = ", ".join(f"{name} {len(rates)}" for name, rates in tables)
    return f"compared {len(population)} keys, {len(disagreements)} disagreements; tables hold: {sizes}"


def test_the_compared_population_is_not_empty() -> None:
    """Guards the vacuous pass: zero disagreements over zero pairs proves nothing."""
    population, _ = _disagreements(_tables(), _seed_history())
    print(census())

    assert len(population) >= 15, (
        f"{census()}. "
        f"Only {len(population)} comparable (country, rate class) keys - the drift check has "
        f"gone vacuous. Either a table stopped parsing, _CLASS_OF stopped matching, or the "
        f"comparison stopped comparing."
    )


def test_every_yaml_rate_class_is_either_compared_or_states_no_rate() -> None:
    """A class this reader cannot parse must fail here rather than drop out quietly.

    The reader used to accept a single mapping only. When GB and DE standard
    VAT were rewritten as dated histories they stopped being mappings, left the
    comparison, and this file went on passing over a population two keys
    smaller - the two headline rates the platform quotes most. That is the
    vacuous pass the module docstring is about, arriving through the shape of
    the data rather than through the tax-code mapping, and nothing here noticed.

    The rule: every rate class in the yaml is either compared or states no rate
    at all, which is the only honest reason to have nothing to compare. Both
    blocks are walked. Reading ``vat`` alone was the same defect in a second
    form - it did not lose a class to a shape it could not parse, it never
    looked at six of them, and a population that is never looked at cannot
    report that it is missing.
    """
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    compared = set(_yaml_rates())

    unread = sorted(
        f"{country}.{cls}"
        for country, block in (doc.get("jurisdictions") or {}).items()
        for block_key in _RATE_BLOCKS
        for cls, entry in ((block or {}).get(block_key) or {}).items()
        if (country, cls) not in compared and _declares_a_rate(entry)
    )

    assert unread == [], (
        f"{unread} state a rate the yaml reader did not return, so they have dropped out of the "
        f"drift comparison rather than failing it. Teach _current_yaml_rate the shape they use."
    )
    # Named as well as covered by the rule above: these are the classes the
    # rule was written for, and a reader that lost them again would otherwise
    # only be caught by a count. SG and AU are here because they are what the
    # ``vat``-only reader could not see, and a rule stated in the abstract is
    # worth less than one instance of it that used to be missing.
    assert {("GB", "standard"), ("DE", "standard"), ("SG", "standard"), ("AU", "standard")} <= compared


def test_every_seed_country_still_has_an_open_period() -> None:
    """A country whose periods have all closed leaves the comparison silently.

    ``_seed_rates`` only reads open rows, so a country whose last period was
    given an end date drops out of the population entirely - and because
    absence never convicts, genuine staleness would then look like agreement.
    """
    rows = _seed_rows()

    stranded = sorted(
        {r["country_code"] for r in rows} - {r["country_code"] for r in rows if r["effective_to"] is None}
    )

    assert stranded == [], (
        f"{stranded} have no open tax period, so they have dropped out of the drift comparison rather than failing it"
    )


def test_the_methodology_catalogue_is_actually_being_read() -> None:
    """A reader returning nothing would empty the fourth table without failing anything.

    Absence never convicts in this file, so a catalogue that stopped parsing
    would remove itself from every comparison and leave the whole gate greener
    than it was before it existed. Named instances as well as a count, because
    a count survives losing exactly the rows that matter.
    """
    rates = _catalogue_rates()

    assert len(rates) >= 30, f"the catalogue returned only {len(rates)} rates; it has stopped being read"

    assert rates[("IL", "standard")] == Decimal("0.18"), (
        "Israel is the case this table was added for. The seed carried 17 % with no end date for "
        "months after the rise to 18 % and the catalogue said 18 the whole time, with nothing "
        "comparing the two. If Israel ever leaves this table, the same gap is open again."
    )
    assert rates[("QA", "standard")] == Decimal("0"), (
        "Qatar's 0 % is a rate the platform quotes, not an absence. A reader testing vat_rate for "
        "truthiness rather than against None drops it, and every other zero-rated template with it."
    )


def test_every_named_catalogue_exception_still_diverges() -> None:
    """An exception that has stopped being needed must fail rather than sit there.

    Without this, ``_CATALOGUE_DIVERGES`` is an allowlist: a country put on it
    for a real reason stays excused forever, including after somebody aligns
    the two figures, and the next genuine divergence in that country is
    excused by an entry whose reason no longer applies. The list has to be
    checkable, so it is checked.
    """
    catalogue = _catalogue_rates_raw()
    seed = _seed_rates()

    for country, reason in _CATALOGUE_DIVERGES.items():
        key = (country, "standard")
        assert key in catalogue, (
            f"{country} is excused from the catalogue comparison, but the catalogue no longer "
            f"carries a rate for it, so the entry is excusing nothing. Remove it."
        )
        assert key in seed, (
            f"{country} is excused from the catalogue comparison, but the seed no longer carries "
            f"an open standard rate for it, so the entry is excusing nothing. Remove it."
        )
        assert catalogue[key] != seed[key], (
            f"{country} is on the exception list, which says: {reason} But both now say "
            f"{seed[key]}, so the exception is excusing an agreement and would go on excusing a "
            f"real disagreement later. Remove {country} and let it be compared like the rest."
        )


def test_the_tax_tables_agree_where_they_overlap() -> None:
    """Fail when two tables carry a rate for the same country and disagree."""
    population, disagreements = _disagreements(_tables(), _seed_history())

    print(f"\nCompared {len(population)} (country, rate class) keys carried by two or more tables:")
    print("\n".join(population))

    assert disagreements == [], "tax tables disagree:\n" + "\n".join(disagreements)


def test_a_planted_disagreement_is_caught() -> None:
    """The control: prove the gate can fail, not only that it passes.

    Note that this calls ``_disagreements`` - the same function the real test
    calls - on a perturbed copy of the tables. A control that re-implemented
    the comparison here would stay green if the real loop were broken, which
    is the failure this whole file exists to make impossible.
    """
    tables = _tables()
    # Found by name rather than by position. This used to take the last entry,
    # which was the seed until a fourth table was appended after it - at which
    # point the control was perturbing the methodology catalogue while claiming
    # to perturb the seed. A control that quietly measures something else is
    # worse than no control.
    seed_index = next(i for i, (name, _) in enumerate(tables) if name.endswith("tax_configurations.json"))
    seed_name, seed = tables[seed_index]

    others = [key for key in seed if sum(key in t for _, t in tables) >= 2]
    assert others, "no seed key is carried by a second table"
    key = sorted(others)[0]
    carriers = sum(key in t for _, t in tables)

    perturbed = [(name, dict(table)) for name, table in tables]
    perturbed[seed_index][1][key] = seed[key] + Decimal("0.01")

    population, disagreements = _disagreements(perturbed, _seed_history())

    # Every other table carrying that key now disagrees with the seed.
    assert len(disagreements) == carriers - 1, disagreements
    assert all(f"{key[0]} {key[1]}:" in line for line in disagreements)
    assert all(seed_name in line for line in disagreements)
    # Perturbing a value must not change which keys are comparable.
    assert len(population) == len(_disagreements(tables, _seed_history())[0])


def test_a_stale_value_is_named_as_stale() -> None:
    """The second control: the staleness message must actually be reachable.

    The lead asked for two failures, disagreement and staleness, and the
    staleness branch only runs inside a disagreement. No live country has
    both a closed period and a counterpart elsewhere, so on today's data
    that branch never executes and would rot untested. This plants the case:
    a second table still carrying a rate the seed closed on a known date.
    """
    history = _seed_history()
    active = _seed_rates()
    candidates = sorted(key for key in history if key in active)
    assert candidates, "no seed key has both a closed period and a current rate"

    key = candidates[0]
    old_rate, ended = history[key][0]
    assert old_rate != active[key]

    stale_table = ("app/core/some_other_table.py", {key: old_rate})
    seed_table = (str(_SEED.relative_to(_BACKEND)), active)

    _, disagreements = _disagreements([stale_table, seed_table], history)

    assert len(disagreements) == 1, disagreements
    line = disagreements[0]
    assert "looks stale" in line
    assert ended in line, f"the message does not name the date the period closed: {line}"
    assert stale_table[0] in line
    assert seed_table[0] in line


def test_reintroducing_the_israeli_defect_is_caught() -> None:
    """The control for the fourth table: put the real defect back and require a red.

    Everything above is measured on a file that has been fixed, and a check
    that has only ever seen the fixed state is not evidence it would have
    caught the broken one. So this rebuilds it exactly: the seed carrying
    Israel's superseded 17 % as the rate in force, the catalogue carrying 18,
    and nothing else changed.

    Two claims, and the second is the one that makes the failure useful rather
    than merely present. The comparison has to notice, and it has to say the
    seed is the stale side, which it can because the seed's own closed window
    names the date the 17 % rate ended.
    """
    perturbed = []
    for name, table in _tables():
        table = dict(table)
        if name.endswith("tax_configurations.json"):
            table[("IL", "standard")] = Decimal("0.17")
        perturbed.append((name, table))

    _, disagreements = _disagreements(perturbed, _seed_history())

    israeli = [line for line in disagreements if line.strip().startswith("IL standard")]
    assert israeli, (
        "the seed was put back to Israel's superseded 17 % and nothing was reported, so the "
        f"methodology catalogue is not being compared against it. Disagreements found: {disagreements}"
    )
    assert any("looks stale" in line and "2024-12-31" in line for line in israeli), (
        f"the disagreement was reported without naming the seed as the stale side: {israeli}"
    )


def test_the_catalogue_side_can_fail_too() -> None:
    """And in the other direction: a catalogue rate that drifts off the seed's.

    The test above moves the seed. If the comparison were somehow keyed to the
    seed alone, it would pass while a wrong figure in the catalogue - the table
    that decides what a project actually charges - went unreported.
    """
    perturbed = []
    for name, table in _tables():
        table = dict(table)
        if name.endswith("templates.py"):
            table[("IL", "standard")] = Decimal("0.17")
        perturbed.append((name, table))

    _, disagreements = _disagreements(perturbed, _seed_history())

    assert any("templates.py" in line and "IL standard" in line.strip() for line in disagreements), (
        f"a wrong rate in the methodology catalogue was not reported: {disagreements}"
    )


@pytest.mark.parametrize("path", [_SEED, _YAML, _CORE, _CATALOGUE])
def test_every_table_this_check_reads_still_exists(path: Path) -> None:
    """A moved or renamed table must break the check rather than empty it."""
    assert path.is_file(), f"{path} is gone; the drift check is reading nothing"
