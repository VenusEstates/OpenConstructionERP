# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The regional markup table, and the one map that says which country uses it.

This is the canonical stack a bill gets seeded with, and it is the only place
the platform states what a national markup convention contains. It used to live
inside ``app.modules.boq.service`` next to the code that reads it, which was
fine while it had exactly one reader. It has two now: the methodology catalogue
in :mod:`app.modules.methodology.templates` derives its country templates from
this table rather than restating them, so that a country cannot be priced two
ways by two engines that both claim to describe it.

That second reader is why this module exists as its own file, and it comes with
a constraint. Standard library only: no ``app.*`` imports, no SQLAlchemy, no
Pydantic, nothing that ``templates.py`` is not allowed to pull in. The
methodology catalogue is loadable standalone on Python 3.11 for its unit tests
and must stay that way, and a single convenience import here would end that
without any test going red until somebody runs the local interpreter. Put a
helper that needs the ORM in ``service.py``, not here.

What the table is NOT: a claim that these percentages are regulated figures.
They are documented, defensible starting points for a medium commercial
building, and every seeded line is editable in-app the moment it lands.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_MARKUP_TEMPLATES",
    "REGION_BY_COUNTRY",
    "NON_SINGLE_TAX_REGIONS",
    "resolve_region_lines",
    "region_lines_for_country",
]


# ── Regional markup templates ────────────────────────────────────────────────
#
# Based on industry standards for medium commercial building projects.
# Percentages applied to direct cost unless noted; tax items are cumulative.
# Sources: VOB/HOAI, NRM1/RICS, US cost index/AIA, BATIPRIX, FIDIC, CPWD, AIQS,
# MLIT, TCU/SINAPI, Byggakademin, ГЭСН/МДС, 建标[2013]44号, 조달청.
#
# ``apply_to`` is NOT a stylistic choice and these templates deliberately do not
# agree on it. ``cumulative`` means direct cost plus EVERY preceding line, so a
# ``cumulative`` line placed after the profit line earns the contractor a margin
# on its own allowance. Whether that is right depends on the market:
#
#   * A bond or a tax is levied on the contract value the client actually signs,
#     which does include overhead and profit. ``cumulative`` is correct there.
#   * A contingency is an allowance against cost risk. Charging profit on it
#     inflates the bid by a margin on money nobody expects to spend, so it must
#     NOT sit on a base containing profit - unless the market's own standard
#     method says otherwise, which for UK and RU it does (see those blocks).
#
# Before you normalise these six-line stacks to one shape, read the per-region
# note. ``tests/unit/test_boq_service_pure_helpers.py`` pins the intent of every
# region, so a new template that compounds contingency onto profit fails until
# its entry there is updated deliberately.

DEFAULT_MARKUP_TEMPLATES: dict[str, list[dict[str, object]]] = {
    # ── Germany / Austria / Switzerland ─────────────────────────────────
    # VOB/B Zuschlagskalkulation, EFB Preisblatt 221
    "DACH": [
        {
            "name": "Baustellengemeinkosten (BGK)",
            "category": "overhead",
            "percentage": "10.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Allgemeine Geschäftskosten (AGK)",
            "category": "overhead",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Wagnis (W)",
            "category": "contingency",
            "percentage": "2.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Gewinn (G)",
            "category": "profit",
            "percentage": "3.0",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
        {
            "name": "Mehrwertsteuer (MwSt.)",
            "category": "tax",
            "percentage": "19.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
    ],
    # ── United Kingdom ──────────────────────────────────────────────────
    # RICS NRM1/NRM2, UK cost index Elemental Standard Form
    # The two risk lines are ``cumulative`` after profit ON PURPOSE. NRM1 builds
    # the cost plan as works cost estimate, then main contractor's overheads and
    # profit, and only then risk allowances, so under that method the risk base
    # legitimately contains the contractor's margin. This is the opposite of the
    # US block below and both are correct in their own market.
    "UK": [
        {
            "name": "Main Contractor's Preliminaries",
            "category": "overhead",
            "percentage": "13.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Main Contractor's Overheads",
            "category": "overhead",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Main Contractor's Profit",
            "category": "profit",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Design Development Risk",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
        {
            "name": "Construction Contingency",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        {
            "name": "VAT",
            "category": "tax",
            "percentage": "20.0",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
    ],
    # ── United States ───────────────────────────────────────────────────
    # US cost index / AIA / CSI MasterFormat Division 01
    "US": [
        {
            "name": "General Conditions (Div. 01)",
            "category": "overhead",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "General Contractor Overhead",
            "category": "overhead",
            "percentage": "7.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "General Contractor Profit",
            "category": "profit",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "General Liability Insurance",
            "category": "insurance",
            "percentage": "1.0",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
        # ``cumulative`` is deliberate and correct here: a payment and
        # performance bond is written against the contract sum, so the premium
        # base includes general conditions, overhead, profit and insurance.
        {
            "name": "Performance & Payment Bond",
            "category": "bond",
            "percentage": "1.5",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        # Both contingencies are ``direct_cost`` and must stay that way. They
        # were ``cumulative``, which put the general contractor's profit line
        # (sort_order 2) inside the contingency base and charged a 5 % margin on
        # an allowance that exists precisely because the money may never be
        # spent. US practice carries contingency in the cost of work, under the
        # fee, not on top of it. Do not "tidy" these to match the bond above.
        {
            "name": "Design Contingency",
            "category": "contingency",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 5,
        },
        {
            "name": "Construction Contingency",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "direct_cost",
            "sort_order": 6,
        },
    ],
    # ── France ──────────────────────────────────────────────────────────
    # Méthode du Déboursé Sec, BATIPRIX, Code des marchés publics
    "FR": [
        {
            "name": "Frais de chantier (FC)",
            "category": "overhead",
            "percentage": "10.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Frais généraux (FG)",
            "category": "overhead",
            "percentage": "15.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Bénéfice et aléas (B&A)",
            "category": "profit",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "TVA",
            "category": "tax",
            "percentage": "20.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
    ],
    # ── Gulf / UAE ──────────────────────────────────────────────────────
    # FIDIC Red Book, AECOM ME Handbook
    "GULF": [
        {
            "name": "Preliminaries & General (P&G)",
            "category": "overhead",
            "percentage": "13.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Contractor Overhead",
            "category": "overhead",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Contractor Profit",
            "category": "profit",
            "percentage": "7.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Insurance (CAR + TPL)",
            "category": "insurance",
            "percentage": "0.5",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
        {
            "name": "Performance Bond",
            "category": "bond",
            "percentage": "0.5",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        {
            "name": "VAT",
            "category": "tax",
            "percentage": "5.0",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
    ],
    # ── India ───────────────────────────────────────────────────────────
    # UNRATIFIED: the contingency line is ``cumulative`` after profit, the shape
    # corrected in the US block. Left as written because no ordering source was
    # confirmed either way, not because it was checked and endorsed.
    # CPWD Works Manual 2019, DSR, IS:7272
    "IN": [
        {
            "name": "Site Overhead / Establishment",
            "category": "overhead",
            "percentage": "7.5",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Head Office Overhead",
            "category": "overhead",
            "percentage": "7.5",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Contractor's Profit",
            "category": "profit",
            "percentage": "7.5",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Contingency",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
        {
            "name": "Labour Cess (BOCW)",
            "category": "other",
            "percentage": "1.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        {
            "name": "GST",
            "category": "tax",
            "percentage": "18.0",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
    ],
    # ── Australia ───────────────────────────────────────────────────────
    # UNRATIFIED: three ``cumulative`` allowances sit after the margin line, so
    # they carry contractor margin the way the US block no longer does. Left as
    # written because no ordering source was confirmed, not because it is known
    # to be right. The "Escalation Allowance" here is also a flat percentage
    # standing in for time-based escalation; the price-index module holds the
    # real date-to-date arithmetic.
    # AIQS ACMM, AS 4000
    "AU": [
        {
            "name": "Contractor's Preliminaries",
            "category": "overhead",
            "percentage": "13.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Contractor's Margin (OH&P)",
            "category": "profit",
            "percentage": "10.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Design Contingency",
            "category": "contingency",
            "percentage": "5.0",
            "apply_to": "cumulative",
            "sort_order": 2,
        },
        {
            "name": "Construction Contingency",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
        {
            "name": "Escalation Allowance",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        {
            "name": "GST",
            "category": "tax",
            "percentage": "10.0",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
    ],
    # ── Japan ───────────────────────────────────────────────────────────
    # 公共建築工事共通費積算基準 (MLIT)
    "JP": [
        {
            "name": "\u5171\u901a\u4eee\u8a2d\u8cbb (Common Temporary)",
            "category": "overhead",
            "percentage": "7.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "\u73fe\u5834\u7ba1\u7406\u8cbb (Site Management)",
            "category": "overhead",
            "percentage": "12.0",
            "apply_to": "cumulative",
            "sort_order": 1,
        },
        {
            "name": "\u4e00\u822c\u7ba1\u7406\u8cbb\u7b49 (General Admin & Profit)",
            "category": "profit",
            "percentage": "7.0",
            "apply_to": "cumulative",
            "sort_order": 2,
        },
        {
            "name": "\u6d88\u8cbb\u7a0e (Consumption Tax)",
            "category": "tax",
            "percentage": "10.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
    ],
    # ── Brazil ──────────────────────────────────────────────────────────
    # BDI per TCU Acórdão 2.622/2013, SINAPI
    "BR": [
        {
            "name": "Administra\u00e7\u00e3o Central (AC)",
            "category": "overhead",
            "percentage": "5.5",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Despesas Financeiras (DF)",
            "category": "other",
            "percentage": "1.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Seguros (S)",
            "category": "insurance",
            "percentage": "0.5",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Garantias (G)",
            "category": "bond",
            "percentage": "0.5",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
        {
            "name": "Riscos e Imprevistos (R)",
            "category": "contingency",
            "percentage": "1.0",
            "apply_to": "direct_cost",
            "sort_order": 4,
        },
        {
            "name": "Lucro (L)",
            "category": "profit",
            "percentage": "7.5",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
        {
            "name": "PIS + COFINS",
            "category": "tax",
            "percentage": "3.65",
            "apply_to": "cumulative",
            "sort_order": 6,
        },
        {
            "name": "ISS",
            "category": "tax",
            "percentage": "3.0",
            "apply_to": "cumulative",
            "sort_order": 7,
        },
    ],
    # ── Scandinavia / Nordic ────────────────────────────────────────────
    # Byggakademin (SE), AB 04, NS 3420 (NO)
    "NORDIC": [
        {
            "name": "Arbetsplatsomkostnader (APO)",
            "category": "overhead",
            "percentage": "15.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Centralomkostnader (CO)",
            "category": "overhead",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Vinst (V)",
            "category": "profit",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Risk (R)",
            "category": "contingency",
            "percentage": "3.0",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
        {
            "name": "MOMS",
            "category": "tax",
            "percentage": "25.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
    ],
    # ── Russia / CIS ────────────────────────────────────────────────────
    # The contingency line is ``cumulative`` after profit ON PURPOSE: the
    # summary estimate calculation takes unforeseen costs on the total of the
    # preceding chapters, which already carry the overhead and profit lines.
    # МДС 81-35.2004, Приказ Минстроя 812/пр, 774/пр
    # НР/СП norms applied to ФОТ; effective % of direct costs shown here.
    "RU": [
        {
            "name": "\u041d\u0430\u043a\u043b\u0430\u0434\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b (\u041d\u0420)",
            "category": "overhead",
            "percentage": "16.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "\u0421\u043c\u0435\u0442\u043d\u0430\u044f \u043f\u0440\u0438\u0431\u044b\u043b\u044c (\u0421\u041f)",
            "category": "profit",
            "percentage": "7.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "\u041d\u0435\u043f\u0440\u0435\u0434\u0432\u0438\u0434\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b",
            "category": "contingency",
            "percentage": "2.0",
            "apply_to": "cumulative",
            "sort_order": 2,
        },
        {
            "name": "\u041d\u0414\u0421",
            "category": "tax",
            "percentage": "20.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
    ],
    # ── China ───────────────────────────────────────────────────────────
    # 建标[2013]44号, regional 定额
    #
    # 建标[2013]44号 states what a construction price is made of twice, in
    # parallel, and the two statements are alternatives rather than layers of
    # one another. By cost element (费用构成要素) the price is labour, material,
    # plant, enterprise management fee, profit, statutory charges and tax. By
    # price formation (造价形成) it is bill items, preliminaries, other items,
    # statutory charges and tax. A stack that takes some of its lines from one
    # axis and some from the other counts the same money under two names and
    # reads to a Chinese estimator as neither convention. This one used to.
    #
    # Under 清单计价 the enterprise management fee and profit belong inside the
    # 综合单价 rather than beside the bill heads, because a bill item's rate
    # already carries them. They are therefore the first two lines, and what
    # they build out of the labour, material and plant a position stores is
    # 分部分项工程费.
    #
    # They are still bill-level rows all the same, because there is nowhere
    # else to put them. ``BOQMarkup`` cannot carry a percentage that informs a
    # unit rate without also applying it to the bill, so ordering them first
    # and categorising them as the rate's own composition is as close as this
    # schema gets. A Chinese estimator reading the bill still sees them as
    # siblings of the heads, and somewhere to declare a unit-rate composition
    # is the change that would actually finish this.
    #
    # Every base here stays ``direct_cost`` on purpose. Chinese practice
    # commonly takes a 总价措施项目 or a statutory percentage on 分部分项工程费
    # rather than on bare direct cost, which would make those two lines
    # ``cumulative``. But that base is set provincially, some provinces take it
    # on the labour component alone, and the text that would settle it could
    # not be obtained. Changing it reprices every newly seeded Chinese bill, so
    # it is a decision to take on evidence rather than a tidy-up to fold into a
    # categorisation fix.
    #
    # The categories are read by something other than the bill. The
    # per-position price analysis derives a unit rate's overhead and profit
    # from the categories of the bill's markup lines, so a bill head left in
    # the ``overhead`` category is pulled into the 综合单价 analysis as if it
    # were part of the rate. 措施项目费 is a head and not a rate component and
    # is categorised ``other`` for that reason. Before this it was ``overhead``
    # and the analysis sheet reported sixteen percent of overhead on a Chinese
    # bill whose management fee is eight.
    #
    # 规费 is a placeholder and is meant to be read as one. The charge is real
    # and mandatory, but it is set provincially and itemised (社会保险费,
    # 住房公积金, and where it still applies 工程排污费), so no single national
    # percentage is right anywhere; five is an order of magnitude to start
    # editing from. The line is kept rather than dropped: the text of
    # GB/T 50500-2024 could not be obtained, and not having read a repeal is
    # not the same as having read a retention.
    "CN": [
        {
            "name": "\u4f01\u4e1a\u7ba1\u7406\u8d39 (Enterprise management fee)",
            "category": "overhead",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "\u5229\u6da6 (Profit)",
            "category": "profit",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "\u63aa\u65bd\u9879\u76ee\u8d39 (Preliminaries)",
            "category": "other",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "\u89c4\u8d39 (Statutory charges)",
            "category": "other",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
        {
            "name": "\u589e\u503c\u7a0e (VAT)",
            "category": "tax",
            "percentage": "9.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
    ],
    # ── South Korea ─────────────────────────────────────────────────────
    # 조달청 예정가격작성기준, 계약예규
    "KR": [
        {
            "name": "\uac04\uc811\ub178\ubb34\ube44 (Indirect Labor)",
            "category": "overhead",
            "percentage": "8.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "\uc0b0\uc5c5\uc548\uc804\ubcf4\uac74\uad00\ub9ac\ube44 (Safety & Health)",
            "category": "overhead",
            "percentage": "2.15",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "\uae30\ud0c0\uacbd\ube44 (Other Expenses)",
            "category": "overhead",
            "percentage": "6.5",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "\uc77c\ubc18\uad00\ub9ac\ube44 (General Admin)",
            "category": "overhead",
            "percentage": "6.0",
            "apply_to": "cumulative",
            "sort_order": 3,
        },
        {
            "name": "\uc774\uc724 (Profit)",
            "category": "profit",
            "percentage": "10.0",
            "apply_to": "cumulative",
            "sort_order": 4,
        },
        {
            "name": "\ubd80\uac00\uac00\uce58\uc138 (VAT)",
            "category": "tax",
            "percentage": "10.0",
            "apply_to": "cumulative",
            "sort_order": 5,
        },
    ],
    # ── Default (generic international) ─────────────────────────────────
    # The fallback for every region without a template of its own, so it can
    # appeal to no national standard method and follows the general rule: the
    # contingency stays off any base that contains profit.
    "DEFAULT": [
        {
            "name": "Site Overhead",
            "category": "overhead",
            "percentage": "10.0",
            "apply_to": "direct_cost",
            "sort_order": 0,
        },
        {
            "name": "Head Office Overhead",
            "category": "overhead",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 1,
        },
        {
            "name": "Profit",
            "category": "profit",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 2,
        },
        {
            "name": "Contingency",
            "category": "contingency",
            "percentage": "5.0",
            "apply_to": "direct_cost",
            "sort_order": 3,
        },
    ],
}

# ── Which country reads which stack ──────────────────────────────────────────
#
# A region key above is a convention, not a border. DACH is the German
# Zuschlagskalkulation, and Austria and Switzerland cost a job that way too;
# GULF is the GCC preliminaries-and-general tradition shared by its members;
# NORDIC is the Swedish APO/CO structure the other three read without
# translation. The rest are single-country conventions that happen to be named
# after the country.
#
# A country is in this map when the table genuinely states its national method.
# A country that is absent is not an oversight and must not be added to make a
# catalogue look complete: absence is the honest answer that we ship the
# neutral international method for that market, and the methodology catalogue
# says exactly that in the template it builds. Adding a country here changes
# the numbers a bill is seeded with for that market, so it is a data decision,
# not a mapping tidy-up.
#
# Russia was the one stack in the table above that no country reached. The
# lines were written, cited to МДС 81-35.2004 and Приказ Минстроя 812/пр, and
# unreachable, because this map had no ``RU`` line: ``region_lines_for_country``
# answered ``None``, so a Russian project was offered the neutral international
# method and told, correctly, that it was the neutral international method. The
# criterion above is whether the table states the country's national method,
# and for Russia it does. ``test_every_stack_is_reachable_from_some_country``
# now asserts the direction that was missing.
REGION_BY_COUNTRY: dict[str, str] = {
    "AT": "DACH",
    "CH": "DACH",
    "DE": "DACH",
    "GB": "UK",
    "US": "US",
    "FR": "FR",
    "AE": "GULF",
    "KW": "GULF",
    "QA": "GULF",
    "SA": "GULF",
    "IN": "IN",
    "AU": "AU",
    "JP": "JP",
    "BR": "BR",
    "DK": "NORDIC",
    "FI": "NORDIC",
    "NO": "NORDIC",
    "SE": "NORDIC",
    "CN": "CN",
    "KR": "KR",
    "RU": "RU",
}

# Regions whose tax lines a single country VAT rate cannot stand in for, and
# why. Everywhere else a region carries exactly one tax line, so swapping its
# percentage for the rate the payer actually faces is a complete statement:
# that is what the per-project ``default_vat_rate`` override does, and it is
# how one DACH stack serves Germany at 19, Austria at 20 and Switzerland at
# 8.1. These three cannot be served that way, so nothing overrides them and
# their own rates stand.
#
# ``tests/unit/test_one_country_one_markup_stack.py`` walks every region and
# fails on any region with a tax-line count other than one that is not listed
# here, so a fifteenth region with two levies has to state its reason before it
# can ship rather than silently taking one country's rate twice.
NON_SINGLE_TAX_REGIONS: dict[str, str] = {
    "US": (
        "sales tax is levied on materials at the point of purchase and sits in the unit rate, "
        "not on the contract sum, so a US stack carries no bill-level tax line at all"
    ),
    "BR": (
        "PIS + COFINS is federal and ISS is municipal, two levies at two statutory rates, "
        "so one VAT number cannot stand in for both"
    ),
    "DEFAULT": ("the neutral stack names no jurisdiction, so it names no consumption tax either"),
}


def resolve_region_lines(region_key: str, *, vat_rate: str | None = None) -> list[dict[str, object]]:
    """Return a region's markup lines in seeding order, with VAT swapped in.

    The single reader of :data:`DEFAULT_MARKUP_TEMPLATES` for anything that
    prices. Both engines come through here, which is the point: the rule for
    what a country's stack contains is written once, and a change to it moves
    both at the same time instead of moving one and leaving the other to be
    discovered by a customer comparing two totals.

    The VAT rule is the one the per-project override has always applied: when a
    rate is supplied, every line in the ``tax`` category takes it. Whether to
    supply one is the caller's decision and the two callers decide differently.
    A project supplies the rate its own jurisdiction charges. The methodology
    catalogue supplies the country's standard rate unless the region is in
    :data:`NON_SINGLE_TAX_REGIONS`, where one number cannot describe the levies
    (see :func:`region_lines_for_country`).

    Args:
        region_key: A key of :data:`DEFAULT_MARKUP_TEMPLATES`, case-insensitive.
            An unknown key falls back to ``DEFAULT``, matching how a bill has
            always been seeded for a region nobody wrote a stack for.
        vat_rate: Percentage as a decimal string, e.g. ``"19"`` or ``"8.1"``.
            ``None`` leaves the region's own tax rates alone. ``"0"`` is a real
            rate and does override, because a zero-rated jurisdiction is a
            statement, not a missing value.

    Returns:
        Fresh dicts in ``sort_order``, so a caller may mutate them freely. Each
        carries the template's own keys plus ``vat_override``, which is ``True``
        exactly on the lines whose percentage this call replaced.
    """
    template = DEFAULT_MARKUP_TEMPLATES.get(region_key.upper(), DEFAULT_MARKUP_TEMPLATES["DEFAULT"])
    lines: list[dict[str, object]] = []
    for entry in sorted(template, key=lambda e: int(e.get("sort_order", 0))):  # type: ignore[arg-type]
        line = dict(entry)
        swapped = vat_rate is not None and line.get("category") == "tax"
        if swapped:
            line["percentage"] = vat_rate
        line["vat_override"] = swapped
        lines.append(line)
    return lines


def region_lines_for_country(country_code: str, *, vat_rate: str | None = None) -> list[dict[str, object]] | None:
    """Return the national stack for a country, or ``None`` if we do not have one.

    ``None`` is a real answer and callers must carry it as one. It means the
    table states no convention for that market, and the honest thing to offer
    there is the neutral international method described as the neutral
    international method. Substituting the ``DEFAULT`` stack here would hide
    that distinction behind a stack that looks national and is not, which is
    the failure this whole arrangement exists to end.

    Args:
        country_code: ISO 3166-1 alpha-2, case-insensitive.
        vat_rate: The country's standard consumption-tax rate as a decimal
            string. Applied only where the region carries a single tax line;
            see :data:`NON_SINGLE_TAX_REGIONS` for the three that do not.

    Returns:
        The region's lines as :func:`resolve_region_lines` returns them, or
        ``None`` when the country has no entry in :data:`REGION_BY_COUNTRY`.
    """
    region_key = REGION_BY_COUNTRY.get(country_code.upper())
    if region_key is None:
        return None
    override = None if region_key in NON_SINGLE_TAX_REGIONS else vat_rate
    return resolve_region_lines(region_key, vat_rate=override)
