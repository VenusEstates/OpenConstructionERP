/**
 * The decision logic behind the project-coverage reporter, kept free of any
 * Playwright import.
 *
 * The split is not decoration. `@playwright/test` takes several seconds to
 * load and vitest cannot transform it inside its worker startup budget: a test
 * importing it dies with "Timeout waiting for worker to respond", which vitest
 * then summarises as "Test Files  no tests". That is the same
 * did-not-run-reads-as-nothing-wrong failure this reporter exists to catch, so
 * the rules live here where they can be tested cheaply, and the reporter keeps
 * only the part that genuinely needs the browser registry.
 */

export type EngineName = 'chromium' | 'firefox' | 'webkit';

/** Flags that narrow a run to a subset the caller chose on purpose. */
const NARROWING_FLAGS = new Set([
  '-g',
  '--grep',
  '--grep-invert',
  '--shard',
  '--last-failed',
  '--only-changed',
]);

/** Options that take a value, so the value is not mistaken for a file filter. */
const VALUE_FLAGS = new Set([
  '-g',
  '--grep',
  '--grep-invert',
  '--shard',
  '--project',
  '--reporter',
  '--config',
  '-c',
  '--workers',
  '-j',
  '--timeout',
  '--retries',
  '--output',
  '--repeat-each',
  '--max-failures',
  '--global-timeout',
  '--tag',
]);

export interface CliScope {
  /** Project names the caller pinned with --project; empty when they pinned none. */
  projects: string[];
  /** True when the caller restricted which tests can match at all. */
  narrowed: boolean;
}

/**
 * Works out what the run asked for, from the command line and nothing else.
 *
 * Playwright's own resolved config cannot answer this. `config.projects` is not
 * filtered by `--project` (a run pinned to chromium still lists all five), and
 * `config.grep` keeps its match-everything default even after `--grep @smoke`.
 * Both were measured; both would have produced a gate that silently never
 * fired.
 */
export function readCliScope(argv: string[]): CliScope {
  const projects: string[] = [];
  let narrowed = false;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === undefined || arg === 'test') continue;

    const next = argv[i + 1];
    /** True when this flag's value sits in the following argument. */
    const takesDetachedValue = VALUE_FLAGS.has(arg) && next !== undefined && !next.startsWith('-');

    if (arg.startsWith('--project=')) {
      projects.push(arg.slice('--project='.length));
      continue;
    }
    if (arg === '--project') {
      if (next !== undefined) projects.push(next);
      i++;
      continue;
    }

    const flagName = arg.startsWith('--') && arg.includes('=') ? arg.slice(0, arg.indexOf('=')) : arg;
    if (NARROWING_FLAGS.has(flagName)) {
      narrowed = true;
      // Step over a detached value so it is not read as a file filter.
      if (takesDetachedValue) i++;
      continue;
    }

    if (arg.startsWith('-')) {
      if (takesDetachedValue) i++;
      continue;
    }

    // A bare positional argument is a file filter, which narrows the run.
    narrowed = true;
  }

  return { projects, narrowed };
}

/**
 * The engine a project really launches.
 *
 * `use.browserName` is null whenever the project spreads a device descriptor,
 * and the engine then comes from the descriptor's `defaultBrowserType`.
 * Reading only `browserName` would call every device-based project engineless;
 * reading only `defaultBrowserType` would miss that `mobile-chromium`
 * deliberately overrides an iPhone SE descriptor whose default is webkit.
 */
export function engineOf(use: Record<string, unknown>): EngineName | null {
  const name = (use.browserName as string | undefined) ?? (use.defaultBrowserType as string | undefined);
  if (name === 'chromium' || name === 'firefox' || name === 'webkit') return name;
  return null;
}

export interface ProjectUnderTest {
  name: string;
  use: Record<string, unknown>;
  grep?: RegExp | RegExp[];
}

/** A single `@tag` alternative and nothing else: `@rtl`, not `@rtl.*` or `(@rtl)`. */
const LITERAL_TAG = /^@[A-Za-z0-9_-]+$/;

/**
 * The tags a project's own `grep` promises to select, or none when the
 * expression is anything we cannot read with certainty.
 *
 * The anchor is the grep rather than the project name or a separate
 * declaration, because the grep is the only place the author has already
 * written down what the project is for, in a form Playwright itself obeys. A
 * name is a label nothing enforces, and a hand-written `mustCover` list would
 * be supplied by the same person who wrote the grep: whoever declared
 * `rtl-arabic` as `/@rtl|@i18n/` would have declared `mustCover: ['@i18n']`
 * with equal confidence, and the gate would have agreed with them.
 *
 * Deliberately conservative. Every alternative has to be a bare literal tag
 * and the expression must carry no flags, otherwise this returns nothing and
 * the caller skips the check. `/.*\/` (what the desktop projects use) yields
 * nothing and is not held to anything, which is correct: a project that asks
 * for everything promises no particular tag. A false accusation here would
 * cost the gate its credibility on the first run, so an unreadable expression
 * is treated as no claim rather than as a claim we guess at.
 *
 * `grepInvert` is not consulted. It removes tests from a selection; it never
 * adds a promise about what the selection contains.
 */
export function requiredTagsOf(grep: RegExp | RegExp[] | undefined): string[] {
  if (grep === undefined) return [];

  // An array of expressions selects a test matching ANY of them, so the tags
  // they name are pooled. One unreadable member voids the whole pool: we
  // cannot tell which tests it was meant to bring in.
  const expressions = Array.isArray(grep) ? grep : [grep];
  const tags = new Set<string>();

  for (const expression of expressions) {
    if (expression.flags !== '') return [];
    for (const alternative of expression.source.split('|')) {
      if (!LITERAL_TAG.test(alternative)) return [];
      tags.add(alternative);
    }
  }

  return [...tags];
}

export interface CoverageInput {
  /** Every project the config declares, in declaration order. */
  projects: ProjectUnderTest[];
  /** How many tests each project selected, by project name. */
  counts: Map<string, number>;
  /**
   * Every tag carried by at least one test the project selected, by project
   * name.
   *
   * A union is enough for the question being asked, which is whether any
   * selected test carries a given tag, not how many do.
   */
  selectedTags: Map<string, Set<string>>;
  scope: CliScope;
  /** Resolves an engine to its executable and whether that file is on disk. */
  executableFor: (engine: EngineName) => { path: string; installed: boolean };
  /**
   * Whether to check that each project's browser is installed here.
   *
   * The two checks answer different questions and travel differently. Whether
   * a project selects any test is a property of the config, true or false on
   * every machine alike. Whether its browser is on disk is a property of this
   * machine, so a runner that installs no browsers would fail all of them and
   * the check would say nothing about the config. Set false to ask only the
   * portable question.
   */
  checkBrowsers?: boolean;
}

export interface CoverageVerdict {
  /** Reasons the run must fail. */
  problems: string[];
  /** Reasons worth naming that the caller chose by filtering. */
  advisories: string[];
}

export function evaluateCoverage(input: CoverageInput): CoverageVerdict {
  const { projects, counts, selectedTags, scope, executableFor, checkBrowsers = true } = input;
  const problems: string[] = [];
  const advisories: string[] = [];

  const pinned = new Set(scope.projects);
  const asked = projects.filter((p) => pinned.size === 0 || pinned.has(p.name));

  for (const project of asked) {
    const count = counts.get(project.name) ?? 0;

    if (count === 0) {
      const message =
        `project "${project.name}" selected 0 tests, so it proves nothing about this run. ` +
        `Its filter (grep ${String(project.grep)}) matches no spec.`;
      if (scope.narrowed) advisories.push(message);
      else problems.push(message);
    } else {
      // Selecting tests is not the same as selecting the right ones. A grep
      // naming several tags is satisfied by any one of them, so `rtl-arabic`
      // with `/@rtl|@i18n/` ran green for as long as a single @i18n spec
      // existed and no @rtl spec did: the count was 1 and the direction was
      // never exercised. Hold each alternative to its own test.
      //
      // Reported per uncovered tag rather than once per project, so the
      // message names the tag that has to be written or dropped. Skipped
      // entirely when the project selected nothing, because every tag would
      // then be uncovered and the count above already says why.
      const present = selectedTags.get(project.name) ?? new Set<string>();
      for (const tag of requiredTagsOf(project.grep)) {
        if (present.has(tag)) continue;
        const message =
          `project "${project.name}" selected ${count} test${count === 1 ? '' : 's'}, none of which carry ${tag}. ` +
          `Its grep (${String(project.grep)}) offers ${tag} as an alternative, so the project is passing on ` +
          `the strength of its other tags while ${tag} measures nothing. Tag a spec ${tag}, or stop asking for it.`;
        if (scope.narrowed) advisories.push(message);
        else problems.push(message);
      }
    }

    // A project pinned to a channel launches a browser we never downloaded
    // (system Chrome, say), so the bundled executable path says nothing.
    if (project.use.channel) continue;

    const engine = engineOf(project.use);
    if (!engine) {
      problems.push(
        `project "${project.name}" declares no resolvable browser engine, so nothing can verify it will launch.`,
      );
      continue;
    }

    if (!checkBrowsers) continue;

    const executable = executableFor(engine);
    if (!executable.installed) {
      problems.push(
        `project "${project.name}" needs ${engine}, which is not installed (${executable.path}). ` +
          `Install it with: npx playwright install ${engine}`,
      );
    }
  }

  return { problems, advisories };
}
