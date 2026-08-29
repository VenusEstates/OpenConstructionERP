/**
 * Tests for the rules behind the project-coverage reporter.
 *
 * The half worth testing here is the zero-test branch. A missing browser
 * already announces itself (a full `--list` run on a machine carrying only
 * Chromium exits 1 and names firefox and webkit), but a project whose grep
 * matches nothing produces no signal anywhere in Playwright, so this logic is
 * the only thing between that and a green summary.
 *
 * The CLI parsing gets the most cases because it is the part most able to
 * break quietly. Playwright's resolved config cannot answer what a run asked
 * for: `config.projects` ignores `--project`, and `config.grep` keeps its
 * match-everything default even after `--grep` was passed.
 */
import { describe, expect, it } from 'vitest';
import {
  engineOf,
  evaluateCoverage,
  readCliScope,
  type EngineName,
  type ProjectUnderTest,
} from './project-coverage-rules';

const CHROMIUM: Record<string, unknown> = { browserName: 'chromium' };

const CHROMIUM_PROJECT: ProjectUnderTest = { name: 'chromium', use: CHROMIUM, grep: /.*/ };
const RTL_PROJECT: ProjectUnderTest = { name: 'rtl-arabic', use: CHROMIUM, grep: /@rtl|@i18n/ };
const TWO_PROJECTS: ProjectUnderTest[] = [CHROMIUM_PROJECT, RTL_PROJECT];

/** Every engine present. Overridden per-test to model a missing browser. */
const allInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: true });

function verdict(
  argv: string[],
  counts: Record<string, number>,
  projects: ProjectUnderTest[] = TWO_PROJECTS,
  executableFor = allInstalled,
) {
  return evaluateCoverage({
    projects,
    counts: new Map(Object.entries(counts)),
    scope: readCliScope(argv),
    executableFor,
  });
}

describe('readCliScope', () => {
  it('treats an unfiltered run as covering every project', () => {
    expect(readCliScope(['test'])).toEqual({ projects: [], narrowed: false });
  });

  it('reads --project in both the attached and the detached form', () => {
    expect(readCliScope(['test', '--project=chromium']).projects).toEqual(['chromium']);
    expect(readCliScope(['test', '--project', 'webkit']).projects).toEqual(['webkit']);
    expect(readCliScope(['test', '--project=a', '--project', 'b']).projects).toEqual(['a', 'b']);
  });

  it('counts a bare file path as narrowing', () => {
    expect(readCliScope(['test', 'smoke/health.spec.ts']).narrowed).toBe(true);
  });

  it('counts grep and shard as narrowing, attached or detached', () => {
    expect(readCliScope(['test', '--grep', '@smoke']).narrowed).toBe(true);
    expect(readCliScope(['test', '--grep=@smoke']).narrowed).toBe(true);
    expect(readCliScope(['test', '--shard=1/3']).narrowed).toBe(true);
  });

  it('does not mistake a detached option value for a file filter', () => {
    // `--workers 4` must not look like narrowing, or the enforcing branch
    // quietly downgrades itself to an advisory on perfectly ordinary runs.
    expect(readCliScope(['test', '--workers', '4']).narrowed).toBe(false);
    expect(readCliScope(['test', '--reporter', 'list']).narrowed).toBe(false);
  });

  it('does not mistake a grep value for a project name', () => {
    expect(readCliScope(['test', '--grep', '@rtl']).projects).toEqual([]);
  });
});

describe('engineOf', () => {
  it('prefers an explicit browserName over the descriptor default', () => {
    // This is exactly what mobile-chromium does: an iPhone SE descriptor whose
    // defaultBrowserType is webkit, overridden to launch chromium.
    expect(engineOf({ browserName: 'chromium', defaultBrowserType: 'webkit' })).toBe('chromium');
  });

  it('falls back to the descriptor default when browserName is unset', () => {
    expect(engineOf({ defaultBrowserType: 'firefox' })).toBe('firefox');
  });

  it('returns null when neither names a known engine', () => {
    expect(engineOf({})).toBeNull();
    expect(engineOf({ browserName: 'lynx' })).toBeNull();
  });
});

describe('evaluateCoverage', () => {
  it('fails an unnarrowed run in which a declared project selected no tests', () => {
    const { problems } = verdict(['test'], { chromium: 32, 'rtl-arabic': 0 });

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('rtl-arabic');
    expect(problems[0]).toContain('selected 0 tests');
  });

  it('passes when every declared project selected at least one test', () => {
    expect(verdict(['test'], { chromium: 32, 'rtl-arabic': 1 })).toEqual({
      problems: [],
      advisories: [],
    });
  });

  it('only advises when a filter the caller typed excluded the project', () => {
    const { problems, advisories } = verdict(['test', 'smoke/health.spec.ts'], {
      chromium: 2,
      'rtl-arabic': 0,
    });

    expect(problems).toEqual([]);
    expect(advisories).toHaveLength(1);
    expect(advisories[0]).toContain('rtl-arabic');
  });

  it('checks only the projects a --project flag pinned', () => {
    // rtl-arabic selects nothing, but this run never asked for it.
    expect(verdict(['test', '--project=chromium'], { chromium: 32, 'rtl-arabic': 0 }).problems).toEqual(
      [],
    );
  });

  it('still enforces inside a pinned set', () => {
    const { problems } = verdict(['test', '--project', 'rtl-arabic'], {
      chromium: 32,
      'rtl-arabic': 0,
    });

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('rtl-arabic');
  });

  it('fails a project whose browser is not on disk, and names the install command', () => {
    const missingFirefox = (engine: EngineName) => ({
      path: `/browsers/${engine}`,
      installed: engine !== 'firefox',
    });
    const projects: ProjectUnderTest[] = [
      { name: 'chromium', use: CHROMIUM, grep: /.*/ },
      { name: 'firefox', use: { browserName: 'firefox' }, grep: /.*/ },
    ];

    const { problems } = verdict(['test'], { chromium: 32, firefox: 32 }, projects, missingFirefox);

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('npx playwright install firefox');
  });

  it('reports the missing browser even when the project also selected tests', () => {
    // The two checks are independent; a project can be broken in one way and
    // fine in the other, and collapsing them would hide whichever came second.
    const noneInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: false });
    const { problems } = verdict(['test'], { chromium: 0 }, [CHROMIUM_PROJECT], noneInstalled);

    expect(problems).toHaveLength(2);
  });

  it('skips the binary check for a project pinned to a system channel', () => {
    const noneInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: false });
    const projects: ProjectUnderTest[] = [
      { name: 'branded', use: { browserName: 'chromium', channel: 'chrome' }, grep: /.*/ },
    ];

    expect(verdict(['test'], { branded: 4 }, projects, noneInstalled).problems).toEqual([]);
  });

  it('asks only the portable question when browser checking is off', () => {
    // What CI runs. No browser is installed there, so the binary half would
    // condemn every project and say nothing about the config.
    const noneInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: false });
    const { problems } = evaluateCoverage({
      projects: TWO_PROJECTS,
      counts: new Map([
        ['chromium', 32],
        ['rtl-arabic', 0],
      ]),
      scope: readCliScope(['test']),
      executableFor: noneInstalled,
      checkBrowsers: false,
    });

    // The dead project is still caught; the absent browsers are not held
    // against a machine that was never going to launch them.
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('selected 0 tests');
  });

  it('fails a project that resolves to no browser engine at all', () => {
    const projects: ProjectUnderTest[] = [{ name: 'mystery', use: {}, grep: /.*/ }];
    const { problems } = verdict(['test'], { mystery: 3 }, projects);

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('no resolvable browser engine');
  });
});
