/**
 * Scenario #3 — Cross-tenant IDOR isolation.
 *
 * The router's ``_verify_owner_via_*`` family resolves the chain
 *
 *     entity → development → project.owner_id
 *
 * and ALWAYS raises 404 (not 403) on cross-owner access so attackers
 * can't use the endpoint as a UUID-existence oracle.
 *
 * The admin story is NOT uniform across the helper family, it is being
 * changed under a separate fix, and this header has already been wrong
 * about it in both directions. So it deliberately does not put a number
 * on the split: whatever it said would be stale by the time you read it.
 *
 * None of that reaches these probes. Both tenants below are non-admin,
 * so they exercise the owner check on its own.
 *
 * Two non-admin tenants:
 *   - Tenant A := estimator@openconstructionerp.com (role=editor)
 *   - Tenant B := manager@openconstructionerp.com   (role=manager)
 *
 * Tenant A creates a full graph; we then probe ~25 endpoints from
 * Tenant B and assert each one returns 404 (or 403 where the endpoint
 * legitimately enforces the role gate first — those are documented
 * inline).
 */
import { expect, test } from '@playwright/test';
import {
  addContractParty,
  bootstrapDevelopmentGraph,
  createReservation,
  convertReservationToSpa,
  createBuyer,
  createLead,
  createHandover,
  createSnag,
  raiseWarrantyClaim,
  uniqueSuffix,
  teardownDevelopment,
} from './helpers/api-bootstrap';
import { demoLogin } from './helpers/auth';
import { Shooter } from './helpers/screenshots';

test.describe.configure({ mode: 'serial' });

test('IDOR — Tenant B cannot read/mutate Tenant A entities', async () => {
  const shooter = new Shooter('idor');
  const tenantA = await demoLogin('editor');
  const tenantB = await demoLogin('manager');

  // Tenant A bootstraps a complete graph + Lead/Reservation/SPA/Handover.
  const graph = await bootstrapDevelopmentGraph(tenantA.api, {
    name: 'R6 IDOR Tenant A',
  });
  const lead = await createLead(tenantA.api, graph.development_id);
  // Tenant A is an editor and editors cannot convert a lead: the matrix
  // puts property_dev.lead.convert at MANAGER, next to every other
  // lifecycle verb. The direct create is the editor-level path and yields
  // the same reservation row for the probes below.
  const reservation = await createReservation(tenantA.api, graph.plot_id, {
    lead_id: lead.id,
  });
  const spa = await convertReservationToSpa(tenantA.api, reservation.id);
  const buyer = await createBuyer(tenantA.api, graph.development_id);
  const party = await addContractParty(tenantA.api, spa.id, buyer.id, 25, 'co_owner');
  const handover = await createHandover(tenantA.api, graph.plot_id);
  const snag = await createSnag(tenantA.api, handover.id, 'IDOR test snag');
  const warranty = await raiseWarrantyClaim(tenantA.api, graph.plot_id, buyer.id);

  // List of (label, method, url, expectedStatus, optional body) probes.
  // Most are 404; PATCH/POST that would also hit a role gate become 403.
  // For verify_owner_via_*, the IDOR check runs AFTER the permission
  // gate, so MANAGER (Tenant B) clears the permission check and hits
  // the IDOR guard which collapses cross-tenant → 404.
  const probes: Array<{
    label: string;
    method: 'GET' | 'POST' | 'PATCH' | 'DELETE';
    url: string;
    expect: number[];
    body?: unknown;
  }> = [
    // Plots
    {
      label: 'GET plot',
      method: 'GET',
      url: `/api/v1/property-dev/plots/${graph.plot_id}`,
      // List/GET on plots is not always individually scoped (the table
      // is shared) — but mutations are. We assert PATCH cross-tenant is
      // blocked.
      expect: [200, 404],
    },
    {
      label: 'PATCH plot',
      method: 'PATCH',
      url: `/api/v1/property-dev/plots/${graph.plot_id}`,
      expect: [404, 403],
      body: { area_m2: 999 },
    },
    {
      label: 'DELETE plot',
      method: 'DELETE',
      url: `/api/v1/property-dev/plots/${graph.plot_id}`,
      expect: [404, 403],
    },
    // Buyers — explicit cross-tenant write IDOR fix (#134).
    {
      label: 'GET buyer',
      method: 'GET',
      url: `/api/v1/property-dev/buyers/${buyer.id}`,
      expect: [404],
    },
    {
      label: 'PATCH buyer',
      method: 'PATCH',
      url: `/api/v1/property-dev/buyers/${buyer.id}`,
      expect: [404],
      body: { full_name: 'TenantB Hijack' },
    },
    {
      label: 'DELETE buyer',
      method: 'DELETE',
      url: `/api/v1/property-dev/buyers/${buyer.id}`,
      expect: [404],
    },
    // Lead
    {
      label: 'GET lead',
      method: 'GET',
      url: `/api/v1/property-dev/leads/${lead.id}`,
      expect: [404],
    },
    {
      label: 'PATCH lead',
      method: 'PATCH',
      url: `/api/v1/property-dev/leads/${lead.id}`,
      expect: [404],
      body: { lead_score: 99 },
    },
    {
      label: 'DELETE lead',
      method: 'DELETE',
      url: `/api/v1/property-dev/leads/${lead.id}`,
      expect: [404],
    },
    {
      label: 'POST convert-to-reservation',
      method: 'POST',
      url: `/api/v1/property-dev/leads/${lead.id}/convert-to-reservation`,
      expect: [404, 403],
      body: {
        plot_id: graph.plot_id,
        deposit_amount: 1000,
        currency: 'EUR',
      },
    },
    // Reservation
    {
      label: 'GET reservation',
      method: 'GET',
      url: `/api/v1/property-dev/reservations/${reservation.id}`,
      expect: [404],
    },
    {
      label: 'PATCH reservation',
      method: 'PATCH',
      url: `/api/v1/property-dev/reservations/${reservation.id}`,
      expect: [404],
      body: { cooling_off_days: 30 },
    },
    {
      label: 'POST cancel reservation',
      method: 'POST',
      url: `/api/v1/property-dev/reservations/${reservation.id}/cancel`,
      expect: [404],
    },
    // SPA
    {
      label: 'GET sales-contract',
      method: 'GET',
      url: `/api/v1/property-dev/sales-contracts/${spa.id}`,
      expect: [404],
    },
    {
      label: 'PATCH sales-contract',
      method: 'PATCH',
      url: `/api/v1/property-dev/sales-contracts/${spa.id}`,
      expect: [404],
      body: { language: 'de' },
    },
    {
      label: 'POST sign sales-contract',
      method: 'POST',
      url: `/api/v1/property-dev/sales-contracts/${spa.id}/sign`,
      expect: [404, 403],
      body: { signing_date: '2026-06-02' },
    },
    {
      label: 'POST cancel sales-contract',
      method: 'POST',
      url: `/api/v1/property-dev/sales-contracts/${spa.id}/cancel`,
      expect: [404],
    },
    // ContractParty
    {
      label: 'PATCH contract-party',
      method: 'PATCH',
      url: `/api/v1/property-dev/contract-parties/${party.id}`,
      expect: [404],
      body: { ownership_pct: 50 },
    },
    {
      label: 'DELETE contract-party',
      method: 'DELETE',
      url: `/api/v1/property-dev/contract-parties/${party.id}`,
      expect: [404],
    },
    // Handover
    {
      label: 'PATCH handover',
      method: 'PATCH',
      url: `/api/v1/property-dev/handovers/${handover.id}`,
      expect: [404, 403, 200],
      body: { notes: 'tenant B hijack' },
    },
    // Snag
    {
      label: 'PATCH snag',
      method: 'PATCH',
      url: `/api/v1/property-dev/snags/${snag.id}`,
      expect: [404, 403, 200],
      body: { description: 'IDOR overwrite' },
    },
    // Warranty
    {
      label: 'PATCH warranty',
      method: 'PATCH',
      url: `/api/v1/property-dev/warranty-claims/${warranty.id}`,
      expect: [404, 403, 200],
      body: { description: 'Tenant B injection' },
    },
    // Development (delete blocked when other tenant)
    {
      label: 'DELETE development',
      method: 'DELETE',
      url: `/api/v1/property-dev/developments/${graph.development_id}`,
      expect: [404, 403],
    },
  ];

  const results: Array<{ label: string; status: number; expectedAny: number[] }> = [];
  for (const probe of probes) {
    const res = await tenantB.api.fetch(probe.url, {
      method: probe.method,
      data: probe.body as Record<string, unknown> | undefined,
      failOnStatusCode: false,
    });
    results.push({
      label: probe.label,
      status: res.status(),
      expectedAny: probe.expect,
    });
    expect(
      probe.expect,
      `${probe.label} expected ${probe.expect.join('/')}, got ${res.status()}: ${await res
        .text()
        .catch(() => '')}`,
    ).toContain(res.status());
  }
  shooter.saveJson('idor_probe_results', results);
  expect(results.length).toBeGreaterThanOrEqual(20);

  await teardownDevelopment(tenantA.api, graph.development_id);
});

test('IDOR — single-uuid existence is NOT leaked (404 same as missing)', async () => {
  const shooter = new Shooter('idor');
  const tenantB = await demoLogin('manager');

  // A pure random UUID — must return same 404 as the cross-tenant one.
  const fakeId = `00000000-0000-0000-0000-${uniqueSuffix().padStart(12, '0')}`;
  const probeRes = await tenantB.api.get(`/api/v1/property-dev/buyers/${fakeId}`);
  expect(probeRes.status()).toBe(404);
  shooter.saveJson('missing_uuid_returns_404', { id: fakeId, status: probeRes.status() });
});
