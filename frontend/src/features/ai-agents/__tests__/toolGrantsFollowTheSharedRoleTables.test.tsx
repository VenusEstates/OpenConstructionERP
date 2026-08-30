// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * The tool picker compares RANKS, and that makes it the one caller that must
 * not resolve roles the way every other caller does.
 *
 * The alias map and the rank table now live in shared/lib/roles.ts, because
 * three feature files had each typed them out by hand. Two of those callers
 * compare role NAMES and use the shared `normalizeRole`, which maps an absent
 * role to `viewer` so that a comparison against `editor` or `admin` denies.
 * This one compares ranks, `viewer` ranks 0, and its cheapest permissions need
 * rank 0, so the same mapping here would turn a denial into a grant. It
 * therefore resolves through ROLE_ALIASES directly, leaving an unknown role
 * with no rank at all.
 *
 * That distinction is invisible to a reader tidying up the last hand-written
 * resolver, and it is invisible to the parity gate, which compares tables
 * rather than the code that reads them. So it is pinned here, by mounting the
 * real picker and asking the rendered document what a person would be able to
 * click.
 *
 * The first case is the regression guard: if this component is ever rewired
 * onto `normalizeRole`, the unknown-role checkbox becomes enabled and this
 * test is the only thing that says so.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ToolPanel } from '../components/ToolPanel';
import { useAuthStore } from '@/stores/useAuthStore';
import type { ToolWithPermission } from '../api';

// One tool at each end of the range that matters. `costs.read` needs rank 0,
// which is what makes the absent-role case decidable at all: a permission
// needing rank 1 would deny an absent role even under the wrong resolution,
// and the test would pass without measuring anything.
const TOOLS: ToolWithPermission[] = [
  {
    name: 'costs.read',
    description: 'reads the cost catalogue',
    input_schema: {},
    required_permission: 'costs.read',
  },
  {
    name: 'ai_agents.run',
    description: 'runs another agent',
    input_schema: {},
    required_permission: 'ai_agents.run',
  },
];

function mountAs(role: string | null) {
  useAuthStore.setState({ userRole: role });
  render(<ToolPanel tools={TOOLS} selected={[]} onChange={() => {}} />);
}

const cheapest = () => screen.getByRole('checkbox', { name: /reads the cost catalogue/ });
const dearest = () => screen.getByRole('checkbox', { name: /runs another agent/ });

describe('tool grants follow the shared role tables', () => {
  beforeEach(() => {
    useAuthStore.setState({ userRole: null });
  });

  it('withholds even the cheapest tool while the role is unknown', () => {
    mountAs(null);
    // Resolving an absent role to `viewer` would enable this one, because
    // viewer ranks 0 and costs.read needs 0. It must stay disabled.
    expect(cheapest()).toBeDisabled();
    expect(dearest()).toBeDisabled();
  });

  it('withholds every tool from a role string nobody recognises', () => {
    mountAs('chief_of_vibes');
    expect(cheapest()).toBeDisabled();
    expect(dearest()).toBeDisabled();
  });

  it('gives a viewer the rank-0 tool and withholds the rank-1 tool', () => {
    mountAs('viewer');
    expect(cheapest()).toBeEnabled();
    expect(dearest()).toBeDisabled();
  });

  it('treats an alias exactly as the role it resolves to', () => {
    // estimator resolves to editor through the shared alias table, and editor
    // outranks what both tools need. A picker that did not know the alias
    // would grey both of these out for a user the backend fully permits.
    mountAs('estimator');
    expect(cheapest()).toBeEnabled();
    expect(dearest()).toBeEnabled();
  });

  it('still grants everything to an admin alias', () => {
    mountAs('superuser');
    expect(cheapest()).toBeEnabled();
    expect(dearest()).toBeEnabled();
  });
});
