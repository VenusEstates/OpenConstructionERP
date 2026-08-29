/**
 * Reporter that fails a run in which a declared project contributed nothing.
 *
 * Playwright is loud about one half of this and silent about the other, and
 * the two halves are easy to confuse. A project whose browser is not installed
 * fails every one of its tests with "browserType.launch: Executable doesn't
 * exist at ...", so the run exits 1 and nobody is fooled. A project whose
 * `grep` matches no spec is invisible: asking for `--project=chromium
 * --project=rtl-arabic` over a file carrying neither @rtl nor @i18n prints
 * "Running 2 tests using 2 workers" and a chromium-only summary. Nothing
 * anywhere names rtl-arabic, and the run is green. Not running and passing look
 * identical, which is the failure mode this reporter exists to remove.
 *
 * Three independent checks, because they fail for unrelated reasons: every
 * project the run asked for selected at least one test, every tag its `grep`
 * names is carried by at least one of those tests, and every such project's
 * browser is on disk. The project list comes from the resolved config rather
 * than a hardcoded array, so a sixth project is covered the moment it is
 * declared.
 *
 * The middle check exists because counting alone gives the right answer by a
 * mechanism that cannot be wrong. `rtl-arabic` greps `/@rtl|@i18n/`, and for
 * as long as one @i18n spec existed and no @rtl spec did, its count was 1 and
 * this reporter was satisfied by a project that never rendered a right-to-left
 * page. A count says a project ran; only the tags say it ran what it is for.
 *
 * The decision logic lives in ./project-coverage-rules so it can be tested
 * without loading Playwright; this file holds only the parts that need the
 * real browser registry and the real run.
 */
import fs from 'node:fs';
import { chromium, firefox, webkit } from '@playwright/test';
import type { FullConfig, FullResult, Reporter, Suite } from '@playwright/test/reporter';
import { evaluateCoverage, readCliScope, type EngineName } from './project-coverage-rules';

const BROWSERS: Record<EngineName, typeof chromium> = { chromium, firefox, webkit };

function executableFor(engine: EngineName): { path: string; installed: boolean } {
  try {
    const path = BROWSERS[engine].executablePath();
    return { path, installed: fs.existsSync(path) };
  } catch (error) {
    return { path: `unresolvable: ${(error as Error).message}`, installed: false };
  }
}

export default class ProjectCoverageReporter implements Reporter {
  private problems: string[] = [];

  onBegin(config: FullConfig, suite: Suite): void {
    const counts = new Map<string, number>();
    // Which tags actually turned up in each project's selection. `test.tags`
    // is Playwright's own parse of the `@word` tokens in the title chain, so
    // this reads the same tags the `grep` was matched against rather than
    // re-deriving them from titles.
    const selectedTags = new Map<string, Set<string>>();
    for (const test of suite.allTests()) {
      const name = test.parent.project()?.name;
      if (!name) continue;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      let tags = selectedTags.get(name);
      if (!tags) {
        tags = new Set<string>();
        selectedTags.set(name, tags);
      }
      for (const tag of test.tags) tags.add(tag);
    }

    const { problems, advisories } = evaluateCoverage({
      projects: config.projects.map((p) => ({
        name: p.name,
        use: (p.use ?? {}) as Record<string, unknown>,
        grep: p.grep,
      })),
      counts,
      selectedTags,
      scope: readCliScope(process.argv.slice(2)),
      executableFor,
      // A runner that installs no browsers would fail every project on the
      // binary check and tell us nothing about the config, so CI asks only the
      // portable question: does every declared project still select a test.
      checkBrowsers: process.env.OE_E2E_VERIFY_CONFIG_ONLY !== '1',
    });

    this.problems = problems;

    if (advisories.length > 0) {
      console.log('\nProject coverage, not enforced because this run was narrowed by a filter:');
      for (const advisory of advisories) console.log(`  - ${advisory}`);
      console.log('');
    }
  }

  async onEnd(result: FullResult): Promise<{ status?: FullResult['status'] } | undefined> {
    if (this.problems.length === 0) return undefined;

    console.log('');
    console.log('  A configured Playwright project did not run what it claims to.');
    console.log('  Not running is not passing, and running something else is not passing either.');
    for (const problem of this.problems) console.log(`  - ${problem}`);
    console.log('');
    console.log('  Fix the project, install the browser, write the missing spec, or stop');
    console.log('  declaring it in playwright.config.ts.');
    console.log('');

    // Only ever escalate. A run that already failed keeps its own status.
    return result.status === 'passed' ? { status: 'failed' } : undefined;
  }
}
