# Building a regional cost database

This is how a regional cost database is put together in this platform: what the pieces are, what
decisions you have to make before you start collecting numbers, and which of those decisions are
expensive to change afterwards. The examples are drawn from the United States base, where the
units are imperial and the classification is MasterFormat, but nothing here is specific to it.

If you only want to load a database that already exists, or to import a spreadsheet you already
have, that is [importing your own cost database](./cost-database-import.md). This document is
about producing the numbers in the first place.

## What you are actually building

A regional cost database is three things that reference each other.

**Work items** are the priced lines an estimator picks: "strip footing, 2 ft wide, reinforced,
formed and poured, per linear foot". Each has a code, a description, a unit, a rate and a
currency. This is the layer users see.

**Components** are what a work item is made of, stored on the work item itself: so much
carpenter time, so much concrete, so much excavator time, per one unit of the work item. A work
item with components is a recipe. A work item without them is just a number someone quoted.

**Resource prices** are the unit prices of those ingredients, held per region: what an hour of
journeyman carpenter costs, what a cubic yard of 3000 psi concrete costs. There is one price per
resource per region.

The reason for the split is that the three change at completely different rates. A recipe is a
statement about how work is done, and it is stable for years. A resource price is a statement
about a market, and it is stale in months. Splitting them means updating a region's prices is one
pass over a few hundred resources rather than a re-survey of tens of thousands of work items, and
the recipes carry forward untouched.

You can skip the split. A database of flat rates with no components is perfectly valid and the
platform prices with it happily. You are trading the ability to reprice cheaply for not having to
decompose anything, which is the right trade for a small catalogue of your own negotiated prices
and the wrong one for a database you intend to maintain across years.

## Decide these before you collect anything

### The region tag

Every row carries a region tag, and it is load-bearing in three separate ways: it scopes
uniqueness, it selects resource prices, and it resolves currency. The canonical shape is
`<2 or 3 letter country>_<UPPERCASE place>`, matched against `^[A-Z]{2,3}_[A-Z0-9]+$`.
`US_NEWYORK`, `DE_MUNICH`, `US_CUSTOM`.

Uniqueness is on code and region together, so the same code in two regions is two independent
rows at two independent prices. This is what lets one installation hold Munich and New York side
by side, and it is why you cannot use one region tag for two cities you intend to compare.

Choose the granularity honestly. A region is the area over which you are willing to claim one set
of prices holds. If your labour rates come from a metropolitan survey, the region is that
metropolitan area, not the state.

### Currency

Currency is resolved from the region tag, not stored per row by the importer, because the source
data usually has no currency column. Every rate in a base is denominated in the local currency of
that base, so the region *is* the currency.

The resolution runs through one table in
`backend/app/modules/costs/region_currency.py`, built in three layers, each only filling gaps
left by the one before: the published catalogue editions, then every loadable base taking the
currency its own variant declares, then legacy alias tags that older files still carry.

A region in none of the three layers resolves to the empty string, and that is deliberate. There
is no default currency and you should not add one. An unknown currency renders as unknown and is
skipped by conversion; a *guessed* currency corrupts every conversion downstream and looks
correct while doing it. If you add a region, add it to that table in the same change, or you will
write a whole catalogue of prices with no unit of money on them.

### Rates are decimal, and travel as strings

Rates are `Decimal` internally and are serialised as strings on the wire. If you are writing an
extraction script, keep the value as text from the source all the way to the API call and quote
it in your JSON. The moment a rate passes through a float, `199.99` becomes
`199.98999999999998` and stays that way.

### Units

Units are free text up to 20 characters, so nothing stops you mixing `sf`, `SF` and `sq ft`
within one database. Nothing will tell you either, until an estimator gets three near-identical
search results. Fix a unit vocabulary before extraction and normalise to it during extraction,
not afterwards.

The one unit decision with machinery behind it is mass pricing. A structural steel section is
quoted per tonne or per kilogram but measured on a drawing by length, so the item carries
`mass_per_unit` (kilograms per one unit of length) and `mass_basis` (`t` or `kg`), and the
conversion happens at apply time. An empty `mass_basis` means mass pricing is off. If your
region's steel is priced by mass, set both during extraction; retrofitting them means revisiting
every section.

### Classification

`classification` is a dictionary keyed by scheme, so one item can carry several: `{"masterformat":
"03 30 53", "din276": "330"}`. Use the scheme name as the key. The importer for spreadsheets
cannot know which scheme a single column represents and stores it under the generic key `code`,
which is honest but is not something to aim for if you control the pipeline.

Pick the scheme your users actually estimate in. A database classified in a scheme nobody in the
region uses is a database with no usable hierarchy.

## Sourcing the numbers

Three classes of source, in descending order of how much you should trust them and ascending
order of how easy they are to get.

**Your own completed jobs.** Actual paid costs from work you delivered, divided by the quantity
installed. This is the only source that is true by construction. It is also the narrowest: it
covers what you happen to have built, at your own productivity, with your own crews.

**Published statistical data.** Government wage and materials series, published tender results,
and public agency unit price books. These cover a whole market rather than one contractor, and
they are usually the backbone of a regional base. They tell you what things cost, not how long
work takes.

**Quoted prices.** Supplier lists and subcontractor quotes. Current and specific, but they are a
negotiating position rather than a measurement, and they expire.

Productivity, meaning how much labour a unit of work consumes, is the part that no price source
gives you. It comes from your own records, from published productivity references, or from
crew-level observation. It is also the part that survives longest once you have it, because
material prices move constantly and the time it takes to form a footing does not.

> **On licensing.** Facts are not copyrightable, but a particular compilation of them can be, and
> commercial cost publications are compilations that are licensed rather than sold. Extracting a
> published rate book into your database, even with every number re-derived, is a licensing
> question and not a technical one. Government statistical series are usually the safe backbone
> for exactly this reason. Check the terms of anything you extract from, and record where each
> figure came from in `source` while you still remember.
>
> The source references above are described in general terms on purpose. This document does not
> carry a list of URLs, because a link list rots faster than anything else in a document and a
> stale link is worse than no link.

## Building it

### 1. Fix the vocabularies

Units, resource codes, classification scheme, region tag. All four before extraction. Each one is
cheap now and expensive later, because changing any of them means rewriting every row that
already used the old value.

Resource codes are the join between a component and its price, so they matter most. The key used
is the component's `code` if it has one, and otherwise its name lowercased with whitespace
collapsed and prefixed `name:`. Both seeding and repricing use the same function, so they agree
by construction, but a database keyed on names is one typo away from an orphaned component.
Give your resources codes.

### 2. Write the recipes

Each work item gets a `components` array. The shape that both the repricing engine and the
resource extractor act on is:

```json
{"type": "labor", "code": "LAB-CARP-01", "name": "Carpenter, journeyman", "quantity": 0.18, "unit": "hr"}
```

`type` must be one of `material`, `equipment`, `labor` or `operator`. `quantity` is per one unit
of the parent work item.

The column is free-form JSON and nothing validates it on the way in. Get the key names wrong and
the components are stored successfully, and the failure that follows is the worst-shaped one in
this whole pipeline: a missing `quantity` is read as a quantity of *zero* rather than as an
absent value, so once the resources carry real prices every line prices at zero cost and counts
as successfully priced. The item is reported fully priced, with no missing resources, and its
rate is overwritten with `0.00`. The coverage report comes back clean over a database that is now
entirely worthless.

It will not show up on a run made before you have filled the price sheet, because an item on
which nothing priced is left alone. It shows up on the first run after your prices are real,
which is also the first run you are inclined to trust.

Name the key `quantity`. Then check an actual rate after your first reprice, because no count in
the result object will tell you if you got this wrong.

Load the work items with their components and a nominal rate. The rate will be replaced.

### 3. Seed the resource prices

`POST /api/v1/costs/resource-prices/{region}/seed/` walks the components in a region and gives
each distinct resource a row. All four of these endpoints take `costs.update`.

Understand what seeding does and does not give you. It reads the price already written on each
component under `unit_rate`, and takes the highest it sees. On a base imported with rates already
attached to its components, that is a real starting price. On recipes you have just written, no
component carries a `unit_rate` yet, so every resource seeds at zero. That is not a failure: the
seeded sheet is the editable list of slots to fill, and a resource priced at zero simply counts
as unpriced until you fill it. Re-seeding never overwrites a row a user has edited, so it is safe
to run again after adding recipes.

Fill the sheet with `POST .../bulk/`, which takes up to 5000 edits in one transaction, or
`PUT .../{resource_key}` for one. `GET .../stats/` reports coverage.

This is where your sourced numbers go. Everything up to here was structure.

### 4. Reprice

`POST /api/v1/costs/resource-prices/{region}/reprice/` walks every work item in the region,
multiplies each component's `quantity` by its resource price, writes `unit_rate` and `cost` back
onto the component, and sets the work item's rate to the sum. It also records `labor_cost`,
`material_cost` and `equipment_cost` on the item's metadata, which is what makes a cost breakdown
displayable afterwards.

**Run it as a dry run first.** The endpoint supports it, and the result is the only honest
coverage report you will get.

## Reading the reprice result, which is the actual validation step

```json
{
  "region": "US_NEWYORK",
  "items_total": 4916,
  "items_repriced": 4820,
  "items_changed": 4790,
  "items_fully_priced": 4102,
  "items_partially_priced": 718,
  "items_unpriced": 96,
  "coverage": 0.8344,
  "missing_resource_count": 34,
  "missing_resources_sample": ["MAT-REBAR-60"],
  "dry_run": true
}
```

`coverage` is `items_fully_priced` over `items_total`, so it is the fraction you can defend
rather than the fraction that changed.

`items_partially_priced` is the number to look at, and it is a trap if you do not.

An item where *no* component could be priced is left completely alone, which is safe. An item
where *some* components could be priced is repriced anyway, and its new rate is the sum of only
the lines that had prices. A footing whose concrete has a price and whose rebar does not comes
out cheaper than it is, with no error, no warning on the row, and a rate that looks entirely
plausible. It will be wrong by exactly the cost of the rebar.

So the acceptance condition for a repricing pass is `items_partially_priced == 0`, not
`items_repriced > 0`. If it is not zero, take `missing_resources_sample`, price those resources,
and run again. The sample is capped at 25 entries, so use `missing_resource_count` for the real
figure.

`items_unpriced` above zero means no component on those items matched a priced resource at all.
Those items are left at their previous rate rather than zeroed, so this counter is the safe
failure. Take the resource keys from `missing_resources_sample` and check them against your
resource codes; a whole block of unpriced items usually means one code prefix was spelled
differently on the price side than on the recipe side.

Note what this counter will *not* catch. The `quantity` mistake from step 2 does not appear here,
or anywhere else in this object. It reports as fully priced, and only the rates themselves show
it.

## Checking the result is defensible

Repricing arithmetic being correct does not make the database right. Three checks worth running
before anyone estimates with it.

**Against known outcomes.** Price a handful of jobs whose final cost you already know and compare
totals. This is the only check that tests the whole chain at once, and a systematic bias across
several jobs points at productivity assumptions rather than at prices.

**Against an independent published price.** Public agency unit price books and published tender
results give you a second opinion per item. Do this on a sample spread across trades, not on the
items you happen to have already looked at.

**Composition ratios.** For each trade, the split between labour, material and equipment should
land where anyone in that trade would expect. Concrete work heavy on material, formwork heavy on
labour, earthworks heavy on equipment. A ratio that is badly off usually means a unit mismatch in
a recipe, and it will be a factor of 3, 27 or 1000 rather than a few percent, because it is a
volume or mass conversion that went wrong.

Record what you compared against and when. A cost database with no provenance is impossible to
audit later and impossible to defend when someone disputes a number.

## Keeping it current

Resource prices go stale, recipes mostly do not. A refresh is a bulk update of resource prices
followed by a reprice, and it does not touch the recipes.

Two behaviours to plan around. Import never updates: every path skips a row whose code and region
already exist, so re-importing a corrected file does nothing and reports every row as skipped.
And repricing overwrites rates in place with no history, so if you need to answer "what did this
cost last year", snapshot the region before you refresh it.

Adding items to an existing region is straightforward. Extending a region with new *resources*
needs a reprice afterwards, and until you run it the new resources exist and nothing uses them.
