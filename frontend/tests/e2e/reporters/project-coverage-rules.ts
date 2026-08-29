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

export interface CoverageInput {
  /** Every project the config declares, in declaration order. */
  projects: ProjectUnderTest[];
  /** How many tests each project selected, by project name. */
  counts: Map<string, number>;
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
  const { projects, counts, scope, executableFor, checkBrowsers = true } = input;
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
