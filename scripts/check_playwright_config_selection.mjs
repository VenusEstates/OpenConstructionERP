#!/usr/bin/env node
/**
 * Every Playwright configuration must select at least one spec that the
 * repository actually contains, and must select the same set whatever
 * directory the checkout lives in.
 *
 * Why this exists. The type checking added over the sixteen configs on
 * 2026-08-30 (frontend/tsconfig.configs.json) checks their SHAPE: a misspelled
 * option, an option that no longer exists, a value of the wrong type. It cannot
 * check their MEANING. A testDir that points at a directory holding nothing the
 * repository ships, and a testIgnore glob that is right against the repository
 * and wrong against the machine it runs on, are both well typed strings. Two
 * live defects of exactly that shape are the reason this file exists:
 *
 *   - playwright.assets-audit.config.ts selects _assets-audit.spec.ts and
 *     _assets-prove.spec.ts. Both sit under .gitignore line 70, so they exist on
 *     the machine of whoever wrote them and on no clean clone. Playwright says
 *     nothing: zero selected tests is a green run with an empty summary.
 *   - playwright.config.ts once carried testIgnore '**\/runner/**' meaning
 *     tests/e2e/runner/. Playwright matches testIgnore against the ABSOLUTE path
 *     of each file, GitHub checks Linux and macOS runners out under
 *     /home/runner/work and /Users/runner/work, and that one pattern therefore
 *     ignored every spec in the repository on two of three platforms while
 *     staying green on Windows. Fixed in c497946ce, undetectable by reading.
 *
 * So this gate answers two questions per project, not one. Does it select
 * anything from the repository, and does it select the same thing from two
 * different checkout roots.
 *
 * Two properties make the answers worth having.
 *
 * It measures the REPOSITORY, not the disk. The population comes from
 * `git ls-files`, so a spec that is present locally and ignored by git counts as
 * absent, which is what CI and every fresh clone will see. Measuring the disk
 * would return green on both sides of the assets-audit defect and be useless.
 *
 * It matches with PLAYWRIGHT'S OWN code, not an imitation of it. The config is
 * read by playwright's configLoader, which resolves defaults - three of the
 * sixteen configs declare no testMatch at all and depend entirely on the default
 * one - and the file filter is playwright's createFileMatcher applied in the
 * same order as collectFilesForProject: extension first, then
 * `!testIgnore(file) && testMatch(file)`. Only the population is swapped, from a
 * directory walk to the git index. The one thing copied rather than imported is
 * the set of test file extensions, which lives as a local constant inside
 * collectFilesForProject and is not exported; the copy is checked against the
 * original on every run by REPLICATION below, which recomputes each project's
 * disk selection through playwright and requires it to equal ours.
 *
 * Usage: node scripts/check_playwright_config_selection.mjs
 * Exit 0 all good, 1 a config selects nothing or disagrees across roots, 2 the
 * checker could not verify itself or could not run at all. Exit 2 is deliberate:
 * a checker that cannot check must not be indistinguishable from a clean run.
 */

import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const FRONTEND = path.join(REPO, 'frontend');

// Copied from collectFilesForProject in playwright/lib/runner. Not exported,
// so it cannot be imported; REPLICATION below proves the copy still agrees.
export const TEST_FILE_EXTENSIONS = new Set([
  '.js', '.ts', '.mjs', '.mts', '.cjs', '.cts',
  '.jsx', '.tsx', '.mjsx', '.mtsx', '.cjsx', '.ctsx',
]);

// Checkout roots to compare. The first is this machine. The rest are the shapes
// GitHub's hosted runners use, and they are here because the runner defect was
// invisible on Windows and fatal on Linux and macOS.
const SYNTHETIC_ROOTS = [
  '/home/runner/work/ERP_26030500/ERP_26030500',
  '/Users/runner/work/ERP_26030500/ERP_26030500',
  'D:/a/ERP_26030500/ERP_26030500',
];

function fail(message) {
  console.error(message);
  process.exit(2);
}

export function loadPlaywrightInternals() {
  const require_ = createRequire(path.join(FRONTEND, 'package.json'));
  try {
    // By absolute path, not by package specifier: playwright's "exports" map
    // does not publish these internals, and a specifier import is refused.
    const root = path.dirname(require_.resolve('playwright'));
    const common = require_(path.join(root, 'lib/common/index.js'));
    const runner = require_(path.join(root, 'lib/runner/index.js'));
    const util = require_(path.join(root, 'lib/util.js'));
    if (!common.configLoader?.loadConfig) throw new Error('configLoader.loadConfig missing');
    if (!runner.projectUtils?.collectFilesForProject) throw new Error('projectUtils.collectFilesForProject missing');
    if (typeof util.createFileMatcher !== 'function') throw new Error('createFileMatcher missing');
    return { configLoader: common.configLoader, projectUtils: runner.projectUtils, createFileMatcher: util.createFileMatcher };
  } catch (error) {
    fail(
      'Cannot verify: playwright is not importable from frontend/node_modules.\n' +
      `  ${error.message}\n` +
      '  This gate reuses playwright\'s own config loader and file matcher rather than\n' +
      '  imitating them, so without the package it has nothing to reuse. Run it in a job\n' +
      '  that has installed the frontend dependencies. Exiting 2 rather than 0 on purpose:\n' +
      '  a silent pass here would be a checker reporting on configs it never opened.'
    );
  }
}

export function git(args) {
  return execFileSync('git', args, { cwd: REPO, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Absolute paths, under the given root, of every file git tracks in frontend/. */
export function trackedFrontendFiles(root) {
  const rel = git(['ls-files', '--', 'frontend']);
  return rel.map((p) => joinUnderRoot(root, p));
}

/** Rebuild an absolute path under a different checkout root, keeping separators. */
function joinUnderRoot(root, repoRelative) {
  const posixRoot = root.includes('\\') || /^[A-Za-z]:/.test(root) ? root.replace(/\\/g, '/') : root;
  return `${posixRoot.replace(/\/+$/, '')}/${repoRelative}`;
}

/** Move an absolute path from one checkout root to another. */
function reroot(absolute, fromRoot, toRoot) {
  const from = fromRoot.replace(/\\/g, '/').replace(/\/+$/, '');
  const abs = absolute.replace(/\\/g, '/');
  if (!abs.toLowerCase().startsWith(`${from.toLowerCase()}/`)) return null;
  return joinUnderRoot(toRoot, abs.slice(from.length + 1));
}

/**
 * Playwright's own selection rule, over a population we choose.
 * Mirrors collectFilesForProject: extension, then !testIgnore && testMatch.
 */
export function select(createFileMatcher, { testDir, testMatch, testIgnore }, population) {
  const dirPrefix = `${testDir.replace(/\\/g, '/').replace(/\/+$/, '')}/`;
  const matchTest = createFileMatcher(testMatch);
  const matchIgnore = createFileMatcher(testIgnore);
  return population.filter((file) => {
    const normalised = file.replace(/\\/g, '/');
    if (!normalised.toLowerCase().startsWith(dirPrefix.toLowerCase())) return false;
    if (!TEST_FILE_EXTENSIONS.has(path.posix.extname(normalised))) return false;
    return !matchIgnore(file) && matchTest(file);
  });
}

/**
 * The red control. It runs before every real count and it has to do three
 * things: notice a config whose files the repository does not contain, notice a
 * config that answers differently under a different checkout root, and let a
 * healthy config through. A checker that only ever says no is not a checker.
 */
function selfTest(createFileMatcher) {
  const root = '/repo';
  const dir = `${root}/frontend/e2e`;
  const tracked = [`${dir}/boq.spec.ts`, `${dir}/geo-overlay.spec.ts`];
  const ignoredButOnDisk = [`${dir}/_assets-audit.spec.ts`, `${dir}/_assets-prove.spec.ts`];
  const problems = [];

  // 1. The assets-audit shape: selects on disk, selects nothing in the repository.
  const assetsLike = { testDir: dir, testMatch: /_assets-(audit|prove)\.spec\.ts/, testIgnore: [] };
  const onDisk = select(createFileMatcher, assetsLike, [...tracked, ...ignoredButOnDisk]);
  const inRepo = select(createFileMatcher, assetsLike, tracked);
  if (onDisk.length !== 2) problems.push(`planted disk-only config selected ${onDisk.length} on disk, expected 2`);
  if (inRepo.length !== 0) problems.push(`planted disk-only config selected ${inRepo.length} from the repository, expected 0`);

  // 2. The runner shape: a pattern that means one directory here and every
  //    directory on a machine whose checkout path contains that word.
  const runnerLike = { testDir: dir, testMatch: ['**/*.spec.ts'], testIgnore: ['**/runner/**'] };
  const here = select(createFileMatcher, runnerLike, tracked);
  const onRunner = select(
    createFileMatcher,
    { ...runnerLike, testDir: reroot(dir, root, '/home/runner/work/x/x') },
    tracked.map((f) => reroot(f, root, '/home/runner/work/x/x')),
  );
  if (here.length !== 2) problems.push(`planted runner config selected ${here.length} here, expected 2`);
  if (onRunner.length !== 0) problems.push(`planted runner config selected ${onRunner.length} under a runner path, expected 0`);

  // 3. A healthy config must come back green under both roots.
  const healthy = { testDir: dir, testMatch: ['**/*.spec.ts'], testIgnore: [] };
  const healthyHere = select(createFileMatcher, healthy, tracked);
  const healthyThere = select(
    createFileMatcher,
    { ...healthy, testDir: reroot(dir, root, '/home/runner/work/x/x') },
    tracked.map((f) => reroot(f, root, '/home/runner/work/x/x')),
  );
  if (healthyHere.length !== 2 || healthyThere.length !== 2) {
    problems.push(`planted healthy config selected ${healthyHere.length} and ${healthyThere.length}, expected 2 and 2`);
  }

  return problems;
}

function describeMatcher(value) {
  if (value === undefined) return '(default)';
  if (value instanceof RegExp) return String(value);
  if (Array.isArray(value)) return `[${value.map(describeMatcher).join(', ')}]`;
  return JSON.stringify(value);
}

async function main() {
  const { configLoader, projectUtils, createFileMatcher } = loadPlaywrightInternals();

  const selfTestProblems = selfTest(createFileMatcher);
  if (selfTestProblems.length) {
    console.error('SELF-TEST FAILED, no verdict given:');
    for (const problem of selfTestProblems) console.error(`  ${problem}`);
    process.exit(2);
  }
  console.log(
    'SELF-TEST OK: catches a config whose specs the repository does not contain,\n' +
    '              catches a config that answers differently under another checkout\n' +
    '              root, and passes a healthy one.\n'
  );

  // Config paths may be passed on the command line, and then they REPLACE the
  // tracked set. That exists so a human can point the gate at a deliberately
  // broken config and watch it refuse, which is the only way to believe a
  // checker whose good answer is silence. It is loud about it because a
  // narrowed set is exactly how a green verdict gets faked: the denominator
  // moves and the verdict does not.
  const argv = process.argv.slice(2);
  const demonstration = argv.length > 0;
  const configs = demonstration
    ? argv.map((p) => path.relative(REPO, path.resolve(p)).replace(/\\/g, '/'))
    : git(['ls-files', '--', 'frontend/playwright*.config.ts']);
  if (demonstration) {
    console.log(`DEMONSTRATION MODE: checking ${configs.length} config(s) named on the command line,`);
    console.log('              not the ones the repository tracks. This verdict is about those');
    console.log('              files only and says nothing about the repository.\n');
  }
  if (!configs.length) fail('Cannot verify: git tracks no frontend/playwright*.config.ts at all.');

  const tracked = trackedFrontendFiles(REPO);
  const failures = [];
  const rows = [];
  let projectCount = 0;
  let loaded = 0;

  for (const relative of configs) {
    const absolute = path.join(REPO, relative);
    let config;
    try {
      config = await configLoader.loadConfig(configLoader.resolveConfigLocation(absolute));
    } catch (error) {
      failures.push(`${relative}: config failed to load - ${error.message.split('\n')[0]}`);
      continue;
    }
    loaded += 1;

    for (const project of config.projects) {
      projectCount += 1;
      const label = `${relative} [${project.project.name || 'unnamed'}]`;
      const spec = {
        testDir: project.project.testDir,
        testMatch: project.project.testMatch,
        testIgnore: project.project.testIgnore,
      };

      const selected = select(createFileMatcher, spec, tracked);

      // REPLICATION. Playwright's own walk of the disk, narrowed to the files
      // git tracks, has to equal what our matcher picked. If it does not, the
      // copied extension set or the ordering has drifted and no verdict below
      // is worth reading.
      const trackedSet = new Set(tracked.map((f) => f.replace(/\\/g, '/').toLowerCase()));
      const playwrightOnDisk = await projectUtils.collectFilesForProject(project);
      const playwrightTracked = playwrightOnDisk
        .map((f) => f.replace(/\\/g, '/'))
        .filter((f) => trackedSet.has(f.toLowerCase()));
      const ours = new Set(selected.map((f) => f.replace(/\\/g, '/').toLowerCase()));
      const theirs = new Set(playwrightTracked.map((f) => f.toLowerCase()));
      const drift = [...theirs].filter((f) => !ours.has(f)).concat([...ours].filter((f) => !theirs.has(f)));
      if (drift.length) {
        console.error(`REPLICATION FAILED for ${label}: our selection and playwright's own differ by ${drift.length} file(s).`);
        for (const f of drift.slice(0, 5)) console.error(`  ${f}`);
        console.error('  Refusing to report on any config: the rule this gate applies is no longer playwright\'s.');
        process.exit(2);
      }

      // A testDir outside the repository cannot be moved to another checkout
      // root, so the cross-root question does not apply to it. Say so rather
      // than skipping quietly: "not applicable" and "compared, agreed" are
      // different answers and must not print the same.
      const rerootedDir = reroot(spec.testDir, REPO, SYNTHETIC_ROOTS[0]);
      const acrossRoots = rerootedDir === null ? [] : SYNTHETIC_ROOTS.map((root) => {
        const rerooted = {
          testDir: reroot(spec.testDir, REPO, root),
          testMatch: spec.testMatch,
          testIgnore: spec.testIgnore,
        };
        const population = tracked.map((f) => reroot(f, REPO, root)).filter(Boolean);
        return { root, count: select(createFileMatcher, rerooted, population).length };
      });
      if (rerootedDir === null) {
        console.log(`note: ${label} has a testDir outside this repository (${spec.testDir}), so it was checked for an empty selection but not across checkout roots.`);
      }

      const onDiskCount = playwrightOnDisk.length;
      rows.push({
        label,
        repo: selected.length,
        disk: onDiskCount,
        roots: acrossRoots,
        testDir: path.relative(FRONTEND, spec.testDir).replace(/\\/g, '/'),
        testMatch: describeMatcher(spec.testMatch),
      });

      if (selected.length === 0) {
        failures.push(
          `${label}: selects 0 specs from the repository (${onDiskCount} on this disk).\n` +
          `    testDir ${path.relative(FRONTEND, spec.testDir).replace(/\\/g, '/')}, testMatch ${describeMatcher(spec.testMatch)}\n` +
          '    A run of this config is green and empty. Either the specs it wants are not\n' +
          '    committed, or the pattern no longer describes anything that is.'
        );
      }

      const disagreeing = acrossRoots.filter((r) => r.count !== selected.length);
      if (disagreeing.length) {
        failures.push(
          `${label}: selects ${selected.length} here and ` +
          disagreeing.map((r) => `${r.count} under ${r.root}`).join(', ') + '.\n' +
          `    testIgnore ${describeMatcher(spec.testIgnore)}\n` +
          '    A pattern that depends on where the checkout lives passes on one platform\n' +
          '    and empties the run on another.'
        );
      }
    }
  }

  const width = Math.max(...rows.map((r) => r.label.length));
  console.log('config [project]'.padEnd(width) + '   repo   disk');
  for (const row of rows) {
    const mark = row.repo === 0 || row.roots.some((r) => r.count !== row.repo) ? ' <-' : '';
    console.log(`${row.label.padEnd(width)}  ${String(row.repo).padStart(5)}  ${String(row.disk).padStart(5)}${mark}`);
  }

  console.log();
  console.log(`${demonstration ? 'configs named on the command line:' : 'configs tracked by git:'} ${configs.length}`);
  console.log(`configs loaded:         ${loaded}`);
  console.log(`projects checked:       ${projectCount}`);
  console.log(`checkout roots compared: ${1 + SYNTHETIC_ROOTS.length} (this one, ${SYNTHETIC_ROOTS.join(', ')})`);
  console.log(`repository files considered: ${tracked.length}`);
  console.log();

  if (failures.length) {
    console.error(`FAIL: ${failures.length} problem(s) across ${projectCount} project(s) in ${loaded} config(s).\n`);
    for (const failure of failures) console.error(`  ${failure}\n`);
    process.exit(1);
  }

  console.log(`OK: every one of the ${projectCount} project(s) selects at least one committed spec, and selects the same set under every checkout root compared.`);
}

// Run only when invoked directly. check_e2e_spec_coverage.mjs imports the four
// helpers above so that the two halves of the same question -- does every config
// select a spec, does every spec get selected -- resolve paths through one
// implementation. Two copies of a selection rule are two things that can drift.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await main();
}
