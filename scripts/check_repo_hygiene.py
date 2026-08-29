#!/usr/bin/env python3
"""Repo hygiene guard: keep internal-only files out of the repo and builds.

Internal planning docs, strategy, QA artifacts, audits and runbooks are kept
local only. They must never ship in the public repository or in a release
artifact (the wheel, the frontend bundle, an installer). This guard fails the
commit, the CI run, or the build if any tracked file, or any file inside a
built artifact, matches the internal denylist below.

Usage:
    python scripts/check_repo_hygiene.py              # scan git-tracked files
    python scripts/check_repo_hygiene.py --zip X.whl  # scan a wheel / zip
    python scripts/check_repo_hygiene.py --dir DIR    # scan a directory tree

Exit code 0 means clean. Exit code 1 means an internal-only path was found and
the output names every offending file.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile

# Path patterns that must never be published. Matched against the repo-relative
# path for the git scan and against the in-archive path for the wheel/dir scan,
# so each pattern allows a leading directory prefix.
DENY_PATTERNS = [
    # The agent working notes and their siblings. .gitignore stops these
    # being added; this list is the other half, and catches one that is
    # already tracked, which .gitignore cannot do. Until 2026-08-29 neither
    # half existed for them: the only guard was .git/info/exclude, which is
    # per-clone and absent from a fresh clone, and this gate passed clean
    # while blind to the whole class. .claude/ carries API tokens next to
    # the notes, so the cost of the gap was not only a leaked document.
    # Runtime user data. .gitignore already stops these being added; this is
    # the half that catches one already tracked. Not hypothetical: 18 files
    # under backend/uploads/ shipped in the two v5.6.0 release commits
    # (bcc4180e8, e50d2eb33) and were removed in c3efd76fd. Those 18 turned
    # out to be 13-209 byte test fixtures containing 'real pdf', not personal
    # data, but the directory reached public history once and .gitignore was
    # written afterwards.
    #
    # Anchored to the real paths on purpose. A bare (^|/)uploads?/ matched 6
    # tracked files under backend/app/modules/uploads/, which is a shipped
    # module: a pattern that fires on real code is worse than the gap it
    # closes, because the gate that cries wolf is the gate someone deletes.
    r"(^|/)backend/uploads/",
    r"(^|/)data/uploads/",
    r"(^|/)dwg_uploads/",
    r"(^|/)data/exports/",
    r"(^|/)\.claude/",
    r"(^|/)CLAUDE-DASHBOARDS\.md$",
    r"(^|/)marketing-site/CLAUDE\.md$",
    r"(^|/)R\d+_[A-Z0-9_]*REPORT\.md$",
    r"(^|/)ISSUE_\d+_HANDOVER\.md$",
    r"(^|/)_handover_dossiers/",
    r"(^|/)docs/strategy/",
    r"(^|/)docs/qa/",
    r"(^|/)docs/postgres-migration/",
    r"(^|/)docs/roadmap/",
    r"(^|/)docs/handover/",
    r"(^|/)docs/initiative-ai-estimator/",
    r"(^|/)docs/RUNBOOK\.md$",
    r"(^|/)docs/MASTER_PLAN[^/]*\.md$",
    r"(^|/)docs/SECURITY_AUDIT[^/]*\.md$",
    r"(^|/)docs/I18N_AUDIT[^/]*\.md$",
    r"(^|/)docs/ROADMAP_v[^/]*\.md$",
    r"(^|/)docs/MONEY_FLOAT[^/]*\.md$",
    r"(^|/)docs/validation_report\.md$",
    r"(^|/)qa/",
    r"(^|/)qa-wave/",
    r"(^|/)qa-sweep/",
    r"(^|/)qa-personas/",
    r"(^|/)qa-screenshots/",
    r"(^|/)scripts/[^/]*_report\.(json|txt)$",
    r"(^|/)[^/]*__audit_report\.md$",
    # Screen-flow mockups for a feature that has not been built. They are a
    # design-review artefact with no reader once the feature ships, they sit
    # next to shippable code rather than under docs/, and the first one of
    # these was neither ignored nor matched by any pattern here, so a pathless
    # add would have published it.
    r"(^|/)[^/]*UI_FLOWS\.html$",
    # Underscore-prefixed markdown / text working notes co-located next to
    # source (agent handoffs, audit residue, a11y sweeps, planning notes,
    # per-issue reply drafts) are local-only per constraint #9 - never tracked,
    # never in a wheel. Matches any number of leading underscores in the
    # basename; JSON scratch and normally-named docs are intentionally not hit.
    r"(^|/)_+[^/]*\.(md|txt)$",
    # Cost-base build pipeline internal notes: reports, plans, feasibility
    # studies, dossiers, runbooks, activation notes and the platform
    # integration guide. Emitted beside the country parquets; local only.
    r"(^|/)[A-Z][A-Z0-9_]*_REPORT(_FULL)?\.md$",
    r"(^|/)[A-Z][A-Z0-9_]*_PLAN\.md$",
    r"(^|/)[A-Z][A-Z0-9_]*_FEASIBILITY\.md$",
    r"(^|/)[A-Z][A-Z0-9_]*_(DOSSIER|RUNBOOK)\.md$",
    r"(^|/)WORLD_[A-Z0-9_]*_INDEX\.md$",
    r"(^|/)[A-Z][A-Z0-9_]*_ACTIVATION\.md$",
    r"(^|/)INTEGRATION_GUIDE[^/]*\.md$",
    # Provenance / watermark tooling and the integrity verifier are internal
    # only - never public (they document the covert marker scheme). Kept
    # locally, gitignored, blocked here across git tree, CI and wheel/dir.
    r"(^|/)tools/watermark/",
    r"(^|/)scripts/integrity_check\.py$",
    # The public website is not part of the product repository. It is built
    # and deployed on its own, and the source of truth is the live host, not
    # this tree, so tracking it here only produced a copy that drifted.
    r"(^|/)marketing-site/",
    r"(^|/)website-marketing/",
    # Documentation build helpers: internal tooling, not something a reader of
    # the project is meant to run.
    r"(^|/)docs/expand_docs\d*\.py$",
    # Personal data must never enter this repository, which is public. The
    # marketing host keeps its signup and enquiry captures as JSONL under
    # /root/clawd, and exporting them for a mailing tool produces a CSV of
    # real people. Those files are named here so that a working copy of one,
    # or an export built from one, cannot be committed even by a pathless
    # `git commit` during an unrelated sweep. The extension list is data
    # formats only: `*_subscribers.py` is a notification handler and is not
    # matched. Cost catalogues under data/catalog are unaffected.
    r"(^|/)(demo-registrations|demo-tokens|newsletter-subscribers|license-requests"
    r"|partner-applications|contact-requests|email-delivery-failures)[^/]*\.(jsonl|json|csv)$",
    r"(^|/)[^/]*(subscribers?|mailing[_-]?list|newsletter|email[_-]?export"
    r"|contacts?[_-]?export|leads?[_-]?export|audience)[^/]*\.(csv|jsonl|xlsx)$",
]
_RX = [re.compile(p) for p in DENY_PATTERNS]


def _offending(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if any(rx.search(p) for rx in _RX)})


def _git_tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def _zip_names(path: str) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _dir_names(root: str) -> list[str]:
    names: list[str] = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(base, name), root)
            names.append(rel.replace(os.sep, "/"))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block internal-only files from the repo and build artifacts.",
    )
    parser.add_argument("--zip", help="scan a wheel/zip archive instead of git")
    parser.add_argument("--dir", help="scan a directory tree instead of git")
    args = parser.parse_args()

    if args.zip:
        names, where = _zip_names(args.zip), f"archive {args.zip}"
    elif args.dir:
        names, where = _dir_names(args.dir), f"directory {args.dir}"
    else:
        names, where = _git_tracked(), "git-tracked tree"

    bad = _offending(names)
    if bad:
        print(f"ERROR: internal-only files found in {where} ({len(bad)}):", file=sys.stderr)
        for path in bad:
            print(f"  {path}", file=sys.stderr)
        print(
            "\nThese are local-only planning, strategy, QA, audit or runbook "
            "files and must not be published. Keep them out of git (.gitignore) "
            "and out of build artifacts.",
            file=sys.stderr,
        )
        return 1

    print(f"repo hygiene OK: {len(names)} files in {where}, no internal-only paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
