# A worked example: a small resource-priced database

This walks the whole pipeline end to end on data that ships in this repository, so you can run it
and compare against the figures below rather than trusting them. It takes a few minutes and it is
the fastest way to understand what [the methodology](./US_COST_DATABASE_METHODOLOGY.md) is
describing.

Two files do the work, both under `data/templates/`:

- `example_us_construction.csv` carries 30 resources: nine labour categories, twelve materials, seven
  equipment lines and two subcontractor lines, with a US dollar unit price each.
- `cost_database_with_assemblies.json` carries six work items, each with a recipe referencing those
  resources by code.

They are a matched pair. Every resource code the six recipes reference exists in the CSV, so the
example has no gaps to paper over.

Everything below assumes a running server on `localhost:8000` and a bearer token in `$TOKEN`.
The region tag is `US_CUSTOM`, which is what the JSON file already declares.

## Step 1: load the resource list

```bash
curl -X POST http://localhost:8000/api/v1/costs/import/file/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@data/templates/example_us_construction.csv" \
  -F "catalog_name=US_CUSTOM" \
  -F "catalog_currency=USD"
```

Expect `imported: 30`. These are ordinary cost items. At this point you have a flat rate sheet
and nothing more, which is already a usable cost database.

## Step 2: fix the recipes, then load them

The shipped JSON writes each component's quantity under the key `factor`. Nothing reads `factor`.
Load it as it stands and the recipes will store perfectly, price at zero, and report themselves
as fully priced. Rename the key first:

```bash
ASSEMBLIES=$(python -c "
import json, pathlib, tempfile
src = pathlib.Path('data/templates/cost_database_with_assemblies.json')
items = json.loads(src.read_text(encoding='utf-8'))
for item in items:
    for comp in item['components']:
        comp['quantity'] = comp.pop('factor')
out = pathlib.Path(tempfile.gettempdir()) / 'oe_assemblies.json'
out.write_text(json.dumps(items, indent=2), encoding='utf-8')
print(out)
")
```

It writes outside the repository on purpose, so this walkthrough leaves nothing behind to clean
up. Then post them:

```bash
curl -X POST http://localhost:8000/api/v1/costs/bulk/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @"$ASSEMBLIES"
```

Six items come back. Their rates are whatever the file said; those are about to be replaced.

## Step 3: give the region a price sheet

Repricing multiplies a component quantity by a *resource price*, which lives in a separate
per-region price sheet. The cost items you loaded in step 1 are not that sheet. Write it:

```bash
curl -X POST http://localhost:8000/api/v1/costs/resource-prices/US_CUSTOM/bulk/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"items":[
    {"resource_key":"LAB-CARP-01",   "unit_price":"68.50", "unit":"hr",  "resource_type":"labor"},
    {"resource_key":"LAB-CARP-02",   "unit_price":"42.00", "unit":"hr",  "resource_type":"labor"},
    {"resource_key":"LAB-MASON-01",  "unit_price":"72.00", "unit":"hr",  "resource_type":"labor"},
    {"resource_key":"LAB-LAB-01",    "unit_price":"35.00", "unit":"hr",  "resource_type":"labor"},
    {"resource_key":"LAB-FOREM-01",  "unit_price":"95.00", "unit":"hr",  "resource_type":"labor"},
    {"resource_key":"MAT-CONC-3K",   "unit_price":"165.00","unit":"yd3", "resource_type":"material"},
    {"resource_key":"MAT-REBAR-60",  "unit_price":"0.85",  "unit":"lb",  "resource_type":"material"},
    {"resource_key":"MAT-CMU-8",     "unit_price":"2.85",  "unit":"ea",  "resource_type":"material"},
    {"resource_key":"MAT-DRYW-58",   "unit_price":"0.95",  "unit":"sf",  "resource_type":"material"},
    {"resource_key":"MAT-PAINT-INT", "unit_price":"1.85",  "unit":"sf",  "resource_type":"material"},
    {"resource_key":"MAT-LUMB-2X4",  "unit_price":"5.85",  "unit":"ea",  "resource_type":"material"},
    {"resource_key":"EQP-EXC-SML",   "unit_price":"125.00","unit":"hr",  "resource_type":"equipment"},
    {"resource_key":"EQP-PUMP-CONC", "unit_price":"285.00","unit":"hr",  "resource_type":"equipment"},
    {"resource_key":"SUB-ROOF-ASPH", "unit_price":"425.00","unit":"sq",  "resource_type":"subcontractor"}
  ]}'
```

Fourteen of the thirty resources, because that is all six recipes between them use. The prices are
the CSV's own rates, so the figures in the next step follow from files you already have.

`resource_key` is the component's `code`. Where a resource has no code, the key is its name
lowercased and prefixed `name:`, which is why giving your resources codes is worth the trouble.

`POST /resource-prices/US_CUSTOM/seed/` builds the sheet from the work items instead of from a
payload you write. It reads whatever price is already recorded on each component under
`unit_rate`, and these recipes carry none, so seeding here would create fourteen rows priced at
zero for you to fill in by hand. On a real base that is the right first move. Here it is a
detour, so the example writes the prices directly.

## Step 4: reprice, dry run first

```bash
curl -X POST "http://localhost:8000/api/v1/costs/resource-prices/US_CUSTOM/reprice/?dry_run=true" \
  -H "Authorization: Bearer $TOKEN"
```

You want `items_partially_priced: 0` and `missing_resource_count: 0` before committing. Then drop
`dry_run` and run it again.

Expect `items_unpriced: 30`, and do not let it alarm you. Repricing walks every cost item in the
region, and the thirty rows you loaded in step 1 are resources with no components of their own.
An item with no components is counted as unpriced and left exactly as it was. On a real base the
resource list would usually not share a region with the work items, but here it does, and this is
what that looks like.

## What you should see

Computed from the two template files:

| Work item | Rate in the file | After repricing | Labour | Material | Equipment |
|---|---:|---:|---:|---:|---:|
| `WI-FOOT-STRIP-2X1` | 38.50 | **39.37** | 20.03 | 16.21 | 3.13 |
| `WI-WALL-CIP-8` | 18.75 | **21.91** | 14.48 | 5.15 | 2.28 |
| `WI-WALL-CMU-8` | 14.20 | **17.80** | 14.32 | 3.48 |   |
| `WI-FRAM-WALL-2X4` | 7.85 | **13.04** | 6.90 | 6.14 |   |
| `WI-DRYW-58-PNT` | 5.95 | **6.29** | 3.44 | 2.85 |   |
| `WI-ROOF-ASPH-PITCH` | 485.00 | **442.10** | 17.10 |   |   |

Take the first row apart, because it is the whole mechanism in one line. A linear foot of strip
footing consumes 0.18 hours of carpenter at 68.50 and 0.22 hours of labourer at 35.00, which is
20.03 of labour; 0.075 cubic yards of concrete at 165.00 and 4.5 pounds of rebar at 0.85, which
is 16.21 of material; and 0.025 hours of small excavator at 125.00, which is 3.13. Total 39.37.

Change the carpenter's rate and rerun the reprice, and that number moves on its own. That is the
entire point of the exercise.

## Three things this example is quietly teaching

**The rate in a recipe file is not the sum of its components.** Every one of the six moved, some
by a third. A recipe file carries a nominal rate so the item is valid before it has a price
sheet; it is not an assertion that the arithmetic agrees. On a real base, treat a large gap
between the stated and the repriced rate as a signal to check the recipe rather than as a result.

**The last row does not add up, and it should tell you something.** `WI-ROOF-ASPH-PITCH` reprices
to 442.10, but its labour breakdown shows only 17.10 and there is no material or equipment
figure. The missing 425.00 is its subcontractor line. Repricing sums every component type into
the rate, but the stored breakdown only records labour, material and equipment, so a
`subcontractor` component is inside the price and absent from the explanation of it. If your
recipes use types beyond those, the breakdown will not reconcile against the rate.

**Extraction sees a different subset again.** `POST /api/v1/catalog/extract/` builds a resource
catalogue from these components, and it only accepts types `material`, `equipment`, `labor` and
`operator`. The subcontractor line is skipped there too. Five of the six recipes extract
completely; the roofing one does not.

## Where to go next

Delete the region with `DELETE /api/v1/costs/actions/clear-region/US_CUSTOM` and you can run the
whole thing again from a clean state, which is worth doing once with a deliberate mistake in it.
Leave a `quantity` off one component and watch the reprice report success.

For building a real database rather than replaying this one, the decisions that matter and the
order to make them in are in [the methodology](./US_COST_DATABASE_METHODOLOGY.md). For the import
paths and their failure modes, see [importing your own cost
database](./cost-database-import.md).
