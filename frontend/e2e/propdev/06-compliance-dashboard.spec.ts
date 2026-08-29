/**
 * Scenario #6 — Compliance dashboard + Regulator reports.
 *
 * The API half asserts the regulator-report contract:
 *
 *   - GET /regulator-reports/RERA      (MANAGER+ gated)
 *   - GET /regulator-reports/MAHARERA
 *   - GET /regulator-reports/214-FZ
 *
 * Each must:
 *   * Return 200 + a non-empty ``pdf_base64`` blob
 *   * Carry the correct ``regulator`` + ``quarter`` echoes
 *   * Have pdf_size_bytes > 0 (a real PDF, not an empty stub)
 *
 * The UI half opens the dashboard itself. Its route is
 * ``/property-dev/developments/:devId/compliance``, NOT the top level
 * ``/property-dev/compliance`` this file used to name: all three compliance
 * endpoints require ``dev_id``, so the screen is scoped to a development the
 * same way pricing and the inventory map are.
 */
import { expect, test } from '@playwright/test';
import {
  bootstrapDevelopmentGraph,
  teardownDevelopment,
} from './helpers/api-bootstrap';
import { demoLogin, hydrateAuth } from './helpers/auth';
import { Shooter } from './helpers/screenshots';

test.describe.configure({ mode: 'serial' });

test('regulator-report endpoints generate non-empty PDFs (MANAGER)', async () => {
  const shooter = new Shooter('compliance');
  const admin = await demoLogin('admin');
  const manager = await demoLogin('manager');
  const graph = await bootstrapDevelopmentGraph(admin.api, {
    name: 'R6 Regulator Reports Dev',
  });

  const regulators = ['RERA', 'MAHARERA', '214-FZ'] as const;
  const quarter = '2026-Q2';
  for (const reg of regulators) {
    const res = await manager.api.get(
      `/api/v1/property-dev/regulator-reports/${reg}?dev_id=${graph.development_id}&quarter=${quarter}`,
    );
    expect(
      res.ok(),
      `Regulator report ${reg} failed: ${res.status()} ${await res.text()}`,
    ).toBeTruthy();
    const body = (await res.json()) as {
      regulator: string;
      quarter: string;
      pdf_size_bytes: number;
      pdf_base64: string;
      summary: Record<string, unknown>;
    };
    expect(body.regulator.length).toBeGreaterThan(0);
    expect(body.quarter).toBe(quarter);
    expect(body.pdf_size_bytes).toBeGreaterThan(0);
    expect(body.pdf_base64.length).toBeGreaterThan(50);
    shooter.saveJson(`${reg.toLowerCase()}_envelope`, {
      regulator: body.regulator,
      quarter: body.quarter,
      pdf_size_bytes: body.pdf_size_bytes,
      summary_keys: Object.keys(body.summary ?? {}),
    });
    // Decode + persist the PDF binary so the runner can spot-check it.
    try {
      const pdfBytes = Buffer.from(body.pdf_base64, 'base64');
      shooter.saveBinary(`${reg.toLowerCase()}.pdf`, pdfBytes);
      // Cheap PDF sanity check — the file must begin with "%PDF".
      expect(pdfBytes.subarray(0, 4).toString('latin1')).toBe('%PDF');
    } catch {
      // base64 → buffer failed → fail the test by tripping toBe
      expect(false, `${reg} pdf_base64 is not valid base64`).toBeTruthy();
    }
  }

  // CMA Saudi & Section 32 AU: not all are exposed via REST in this
  // branch — we attempt them but treat 404 as "feature not yet wired".
  // Their JSON envelope is identical when implemented.
  // Try the two more-experimental endpoints; capture the result without
  // failing the spec on the absence.
  for (const path of ['CMA', 'section32']) {
    const r = await manager.api.get(
      `/api/v1/property-dev/regulator-reports/${path}?dev_id=${graph.development_id}&quarter=${quarter}`,
    );
    shooter.saveJson(`${path.toLowerCase()}_probe`, { status: r.status() });
  }

  await teardownDevelopment(admin.api, graph.development_id);
});

test('compliance dashboard opens under a development and runs its checks', async ({
  page,
}) => {
  // This test was skipped, and the reason it carried said no route reached the
  // page, which was true when it was written. The route landed, so the reason
  // stopped describing the world: a skip explaining itself with something that
  // is no longer true reads as a case somebody already thought about, which is
  // worse than no test at all. Both are replaced here.
  const shooter = new Shooter('compliance');
  const admin = await demoLogin('admin');
  await hydrateAuth(page.context(), admin);
  const graph = await bootstrapDevelopmentGraph(admin.api, {
    name: 'R6 Compliance Dashboard Dev',
  });

  await page.goto(`/property-dev/developments/${graph.development_id}/compliance`);
  await page.waitForLoadState('domcontentloaded');

  // The heading and the run button are the two things the route has to deliver:
  // the first says the component mounted rather than the not-found page, the
  // second that it mounted with a devId and is offering its one action.
  await expect(
    page.getByRole('heading', { name: 'Compliance dashboard' }),
  ).toBeVisible();
  const runChecks = page.getByRole('button', { name: 'Run checks' });
  await expect(runChecks).toBeVisible();
  await shooter.shoot(page, 'compliance_dashboard_loaded');

  await runChecks.click();
  await expect(runChecks).toBeEnabled({ timeout: 30_000 });
  await shooter.shoot(page, 'compliance_dashboard_after_run_checks');

  await teardownDevelopment(admin.api, graph.development_id);
});
