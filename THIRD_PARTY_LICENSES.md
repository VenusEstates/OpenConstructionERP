# Third-Party Licenses

This file is **auto-generated** by
[`.github/workflows/sbom-and-licenses.yml`](./.github/workflows/sbom-and-licenses.yml)
on each GitHub release. The latest generated inventory, along
with a CycloneDX Software Bill of Materials (SBOM) for both
backend (Python) and frontend (JavaScript/TypeScript), is
attached to the corresponding release as downloadable assets.

For the authoritative human-readable licensing overview, including
the dual-licensing model (AGPL-3.0-or-later / commercial),
third-party trademarks (buildingSMART, DIN, GAEB, NRM,
CSI MasterFormat, ISO), and the
AI / cryptography / export-control notices, see
[`./NOTICE`](./NOTICE).

## Manual fallback

If you need the current list without waiting for a release:

```bash
# Backend
cd backend
pip install pip-licenses
pip-licenses --format=markdown

# Frontend
cd frontend
npm ci
npx license-checker --production --markdown
```

## Non-exhaustive summary (maintained manually in NOTICE)

See the **Third-Party Software** section of
[`./NOTICE`](./NOTICE) for the human-curated non-exhaustive list
of primary dependencies and their SPDX identifiers.

## What the generated inventory covers, and what it does not

The generated backend half is resolved from the base install plus the
`[dev]` extra. That has two consequences worth knowing before you rely
on it. It lists build and test tooling that no user receives, and it
covers none of the optional dependency groups, so nothing reachable
only through `[server]`, `[semantic-clients]`, `[semantic-encoder]`,
`[cv]`, `[vector]`, `[s3]`, `[pointcloud]` or `[geo]` appears in it.
The container image resolves `[server]` and `[semantic-clients]`, and
the desktop sidecar resolves `[semantic-encoder]`, so neither of those
artefacts is fully described here. The frontend half has no such gap:
it is resolved from the production dependencies, which is what the
bundled UI is built from.

Where the generated inventory and NOTICE disagree about a package that
is in scope for both, the generated one is resolved from a real
environment and is the better source. Where a package is out of its
scope, NOTICE and a resolution you run yourself are the only sources.
NOTICE additionally records the bundled fonts and the native binaries
that arrive inside other packages' wheels, neither of which any
dependency scanner can enumerate. The generated inventory carries the
font licences in a **Bundled assets** section; the native binaries are
described in NOTICE only.

Note that the release job regenerates this file from scratch, so the
copy in the repository is this explanation rather than an inventory.

## The LGPL components, and why this file understates them

Both halves of the generated inventory read what a package declares
about itself, which is the right answer for the package and not always
the right answer for what the package ships. Two rows are worth reading
with that in mind.

`psycopg2-binary` declares LGPL and the generated inventory shows it, so
that one is visible. `opencv-python-headless` declares Apache-2.0 and the
generated inventory shows that, which is correct for OpenCV itself and
tells you nothing about FFmpeg, which is redistributed inside the same
wheel under LGPL-2.1-or-later and is in every artefact as a result. No
inventory built from package metadata can see that layer, in this
project or in any other.

The Linux `.AppImage` is a third case that no Python or npm inventory
reaches at all, because what it carries is not a package: it bundles the
GTK 3 and WebKitGTK desktop stack, which is LGPL-2.1.

`NOTICE` has a section called **LGPL Components and How We Convey Them**
that names all of these, says which artefact carries which, points at
the licence texts committed in the source tree, and carries the offer of
source. Read that rather than inferring the position from the table
above. It also states, because it is the question people ask, that the
same components on the same terms are in the community edition and the
commercial edition alike.
