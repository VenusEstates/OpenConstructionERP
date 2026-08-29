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
  requiredTagsOf,
  type EngineName,
  type ProjectUnderTest,
} from './project-coverage-rules';

const CHROMIUM: Record<string, unknown> = { browserName: 'chromium' };

const CHROMIUM_PROJECT: ProjectUnderTest = { name: 'chromium', use: CHROMIUM, grep: /.*/ };
const RTL_PROJECT: ProjectUnderTest = { name: 'rtl-arabic', use: CHROMIUM, grep: /@rtl|@i18n/ };
const TWO_PROJECTS: ProjectUnderTest[] = [CHROMIUM_PROJECT, RTL_PROJECT];

/** Every engine present. Overridden per-test to model a missing browser. */
const allInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: true });

/**
 * A healthy selection: every tag rtl-arabic's grep asks for is carried by
 * something. Cases about the tag check pass their own map instead. The other
 * fixture projects grep `/.*\/` and so demand no tag at all.
 */
const COVERED: Record<string, string[]> = { 'rtl-arabic': ['@rtl', '@i18n'] };

function tagMap(tags: Record<string, string[]>): Map<string, Set<string>> {
  return new Map(Object.entries(tags).map(([name, list]) => [name, new Set(list)]));
}

function verdict(
  argv: string[],
  counts: Record<string, number>,
  projects: ProjectUnderTest[] = TWO_PROJECTS,
  executableFor = allInstalled,
  tags: Record<string, string[]> = COVERED,
) {
  return evaluateCoverage({
    projects,
    counts: new Map(Object.entries(counts)),
    selectedTags: tagMap(tags),
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
      selectedTags: tagMap(COVERED),
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

describe('requiredTagsOf', () => {
  it('reads every alternative of a plain tag alternation', () => {
    expect(requiredTagsOf(/@rtl|@i18n/).sort()).toEqual(['@i18n', '@rtl']);
    expect(requiredTagsOf(/@mobile|@responsive/).sort()).toEqual(['@mobile', '@responsive']);
  });

  it('reads a single tag with no alternation', () => {
    expect(requiredTagsOf(/@smoke/)).toEqual(['@smoke']);
  });

  it('demands nothing of a project that greps everything', () => {
    // What the three desktop projects use. Asking for everything is not a
    // claim about any particular tag, and treating it as one would condemn
    // chromium, firefox and webkit on the first run.
    expect(requiredTagsOf(/.*/)).toEqual([]);
  });

  it('demands nothing when no grep was declared', () => {
    expect(requiredTagsOf(undefined)).toEqual([]);
  });

  it('pools the alternatives of an array of expressions', () => {
    expect(requiredTagsOf([/@rtl/, /@i18n|@smoke/]).sort()).toEqual(['@i18n', '@rtl', '@smoke']);
  });

  it('gives up on any expression it cannot read literally', () => {
    // The gate must never invent a requirement out of an expression it only
    // half understands: a false accusation costs it its credibility, and a
    // skipped project is no worse than the counting-only gate it replaces.
    expect(requiredTagsOf(/@rtl.*/)).toEqual([]);
    expect(requiredTagsOf(/(@rtl|@i18n)/)).toEqual([]);
    expect(requiredTagsOf(/^@rtl$/)).toEqual([]);
    expect(requiredTagsOf(/@rtl|/)).toEqual([]);
    expect(requiredTagsOf(/@rtl/i)).toEqual([]);
    // One unreadable member voids the whole array; we cannot tell what it
    // was meant to bring in, so we cannot say what is missing.
    expect(requiredTagsOf([/@rtl/, /.*/])).toEqual([]);
  });
});

describe('evaluateCoverage, tag intent', () => {
  it('fails a project whose grep names a tag no selected test carries', () => {
    // The defect this check exists for, in the shape it actually shipped in:
    // rtl-arabic greps /@rtl|@i18n/, one @i18n spec existed, no @rtl spec
    // did, and the count of 1 made the old gate green over a project that
    // never rendered a right-to-left page.
    const { problems } = verdict(['test'], { chromium: 32, 'rtl-arabic': 1 }, TWO_PROJECTS, allInstalled, {
      'rtl-arabic': ['@smoke', '@i18n'],
    });

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('rtl-arabic');
    expect(problems[0]).toContain('@rtl');
    // The tag that IS covered must not be blamed alongside it.
    expect(problems[0]).not.toContain('none of which carry @i18n');
  });

  it('passes once a test carrying the missing tag exists', () => {
    expect(
      verdict(['test'], { chromium: 32, 'rtl-arabic': 2 }, TWO_PROJECTS, allInstalled, {
        'rtl-arabic': ['@smoke', '@i18n', '@rtl'],
      }),
    ).toEqual({ problems: [], advisories: [] });
  });

  it('names each uncovered tag separately', () => {
    const { problems } = verdict(['test'], { chromium: 32, 'rtl-arabic': 3 }, TWO_PROJECTS, allInstalled, {
      'rtl-arabic': ['@smoke'],
    });

    expect(problems).toHaveLength(2);
    expect(problems.join('\n')).toContain('@rtl');
    expect(problems.join('\n')).toContain('@i18n');
  });

  it('says nothing about tags when the project selected nothing at all', () => {
    // The zero-test branch already explains that case, and every tag would
    // trivially be uncovered. Three messages for one fault is noise.
    const { problems } = verdict(['test'], { chromium: 32, 'rtl-arabic': 0 }, TWO_PROJECTS, allInstalled, {});

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('selected 0 tests');
  });

  it('only advises when the caller narrowed the run themselves', () => {
    // `--grep @i18n` legitimately leaves @rtl unselected. That is the
    // caller's filter, not a defect in the config.
    const { problems, advisories } = verdict(
      ['test', '--grep', '@i18n'],
      { chromium: 1, 'rtl-arabic': 1 },
      TWO_PROJECTS,
      allInstalled,
      { 'rtl-arabic': ['@i18n'] },
    );

    expect(problems).toEqual([]);
    expect(advisories).toHaveLength(1);
    expect(advisories[0]).toContain('@rtl');
  });

  it('holds a project to its tags independently of its browser', () => {
    const noneInstalled = (engine: EngineName) => ({ path: `/browsers/${engine}`, installed: false });
    const { problems } = verdict(
      ['test'],
      { 'rtl-arabic': 1 },
      [RTL_PROJECT],
      noneInstalled,
      { 'rtl-arabic': ['@i18n'] },
    );

    expect(problems).toHaveLength(2);
    expect(problems.join('\n')).toContain('@rtl');
    expect(problems.join('\n')).toContain('npx playwright install chromium');
  });
});
