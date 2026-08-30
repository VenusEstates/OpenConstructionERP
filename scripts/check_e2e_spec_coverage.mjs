#!/usr/bin/env node
/**
 * The other half of check_playwright_config_selection.mjs.
 *
 * That script asks: does every CONFIGURATION select at least one spec? It is
 * green. This script asks the inverse: is every SPEC selected by at least one
 * configuration? Both are about the same relation between two sets, and green
 * on one side says nothing at all about the other. Read the green from that
 * gate as an answer to this question and you have a mistake that looks like a
 * confirmation, which is why this is a separate file with its own verdict.
 *
 * WHAT IS ASSERTED, AND WHY THE DENOMINATOR IS NOT A LIST
 *
 * Most specs that no configuration selects are not defects, and no gate can be
 * built by listing the innocent ones by hand: a hand list is a denominator held
 * up by a person, and it goes stale the moment someone adds a file. So this
 * gate asserts only the one thing the repository itself states.
 *
 * `testIgnore` and `testMatch` are not symmetric. `testMatch` is a positive
 * selector: naming two files says nothing about the third. `testIgnore` is an
 * active exclusion: it fires only on a file the configuration had already
 * reached and matched, and removing it is a decision about that specific file.
 * frontend/playwright.config.ts writes the decision out in words:
 *
 *     // Legacy specs sitting directly under tests/e2e/ (one-level deep)
 *     // are invoked via their dedicated configs at the repo root.
 *     'tests/e2e/*.spec.ts',
 *
 * That is a claim with a truth value, and it is the whole gate: every spec some
 * configuration reaches, matches, and then deliberately ignores must be
 * selected by some other configuration. The denominator is computed by
 * resolving the ignore patterns against the repository, so adding a spec next
 * to the ignored ones enlarges it automatically and nobody has to remember.
 *
 * WHAT IS NOT ASSERTED
 *
 * Everything else unselected is printed as a census and does not affect the
 * exit code. At the time of writing that is 65 files, 64 of them under
 * frontend/e2e, and they are not 64 separate findings. frontend/tests/e2e has a
 * sweeping harness (playwright.config.ts, testMatch '**\/*.spec.ts'), so a new
 * spec there runs by default. frontend/e2e has no such harness: the four
 * configurations aimed at it name their files one by one
 * ('geo-overlay.spec.ts', 'dashboard-widgets.spec.ts', 'V_DESIGN.spec.ts',
 * 'floating-chat-onboarding.spec.ts'), so a spec added there runs only if
 * somebody also edits a config. The exception proves the point: the one
 * subdirectory that does have a sweeping config, e2e/propdev under
 * playwright.propdev-e2e.config.ts, contributes nothing to the census at all.
 * That is one fact about one directory, and repeating it 64 times as per-file
 * verdicts would bury it.
 * Whether those specs should get a harness, be deleted, or stay as manual
 * scripts is a human call, and the census is the input to it.
 *
 * A count-only ratchet over the census was considered and rejected: it goes
 * green when one spec is orphaned and another deleted in the same change, which
 * is a number nothing recounts.
 *
 * POPULATION COMES FROM A NAMED COMMIT, NOT FROM THE DISK
 *
 * `git ls-tree` at an explicitly resolved ref, printed beside the verdict. Not
 * `ls-files` (the index, which carries whatever is staged right now) and not a
 * directory walk. A count taken off a working copy carrying unpushed commits is
 * a correct number about a tree nobody else has. The configurations themselves
 * are necessarily loaded from disk, because playwright can only load real
 * files; if any tracked config differs between the ref and the working tree,
 * this says so out loud instead of quietly mixing the two.
 *
 * Exit 1 means a defect was found. Exit 2 means the run could not be trusted --
 * playwright not installed, or the red control not behaving -- and is kept
 * distinct from 0 on purpose.
 */

import path from 'node:path';
import process from 'node:process';

import {
  TEST_FILE_EXTENSIONS,
  git,
  loadPlaywrightInternals,
  select,
} from './check_playwright_config_selection.mjs';

const SPEC = /\.spec\.tsx?$/i;

function bail(message) {
  console.error(message);
  process.exit(2);
}

/** Forward slashes on both platforms, so a comparison means the same thing twice. */
function slash(value) {
  return String(value).split(path.win32.sep).join('/');
}

/**
 * Sort a population into the three states a file can be in with respect to one
 * project. Selected, reached-and-deliberately-ignored, or neither. This is the
 * only place the classification exists, so the red control below exercises the
 * same code the real count does.
 */
function classify(createFileMatcher, projects, population) {
  const selected = new Set();
  const ignoredBy = new Map();
  for (const project of projects) {
    const { label, testDir, testMatch, testIgnore } = project;
    const prefix = `${slash(testDir).replace(/\/+$/, '')}/`;
    const matchTest = createFileMatcher(testMatch);
    const matchIgnore = createFileMatcher(testIgnore);
    for (const file of population) {
      const normalised = slash(file);
      if (!normalised.toLowerCase().startsWith(prefix.toLowerCase())) continue;
      if (!TEST_FILE_EXTENSIONS.has(path.posix.extname(normalised))) continue;
      if (!matchTest(file)) continue;
      const key = normalised.toLowerCase();
      if (matchIgnore(file)) {
        if (!ignoredBy.has(key)) ignoredBy.set(key, []);
        ignoredBy.get(key).push(label);
      } else {
        selected.add(key);
      }
    }
  }
  return { selected, ignoredBy };
}

/**
 * The red control, run before the real count. Three cases, and all three have
 * to come out right: a spec excluded on the stated grounds and run nowhere must
 * be found; the same spec run by a second config must pass; and a pattern that
 * excludes a file no config ever reaches must stay out of the denominator, or
 * the gate inflates its own population and reports on files it cannot judge.
 */
function redControl(createFileMatcher) {
  const root = '/synthetic/repo';
  const population = [
    `${root}/frontend/tests/e2e/orphan.spec.ts`,
    `${root}/frontend/tests/e2e/covered.spec.ts`,
    `${root}/frontend/tests/e2e/smoke/kept.spec.ts`,
    `${root}/frontend/elsewhere/unreached.spec.ts`,
  ];
  const sweeping = {
    label: 'synthetic sweeping harness',
    testDir: `${root}/frontend/tests/e2e`,
    testMatch: ['**/*.spec.ts'],
    testIgnore: ['tests/e2e/*.spec.ts', '**/elsewhere/**'],
  };
  const dedicated = {
    label: 'synthetic dedicated config',
    testDir: `${root}/frontend/tests/e2e`,
    testMatch: ['covered.spec.ts'],
    testIgnore: [],
  };

  const problems = [];
  const planted = classify(createFileMatcher, [sweeping, dedicated], population);
  const cohort = [...planted.ignoredBy.keys()];
  const orphans = cohort.filter((file) => !planted.selected.has(file));

  if (!cohort.includes(`${root}/frontend/tests/e2e/orphan.spec.ts`))
    problems.push('a deliberately ignored spec did not enter the denominator; the gate cannot see the case it exists for');
  if (!cohort.includes(`${root}/frontend/tests/e2e/covered.spec.ts`))
    problems.push('a spec ignored by one config and run by another did not enter the denominator');
  if (cohort.includes(`${root}/frontend/elsewhere/unreached.spec.ts`))
    problems.push('a file outside every testDir entered the denominator; the population is inflating itself');
  if (planted.selected.has(`${root}/frontend/tests/e2e/orphan.spec.ts`))
    problems.push('an unselected spec was counted as selected');
  if (!planted.selected.has(`${root}/frontend/tests/e2e/smoke/kept.spec.ts`))
    problems.push('a healthy spec under the harness was not selected; the matcher is refusing everything');
  if (orphans.length !== 1 || !orphans[0].endsWith('/orphan.spec.ts'))
    problems.push(`expected exactly the planted orphan, got ${orphans.length}: ${orphans.join(', ') || '(none)'}`);

  // And the healthy shape: same population, but the ignored spec has a home.
  const healthy = classify(createFileMatcher, [sweeping, dedicated, {
    label: 'synthetic config for the orphan',
    testDir: `${root}/frontend/tests/e2e`,
    testMatch: ['orphan.spec.ts'],
    testIgnore: [],
  }], population);
  const healthyOrphans = [...healthy.ignoredBy.keys()].filter((f) => !healthy.selected.has(f));
  if (healthyOrphans.length !== 0)
    problems.push(`a healthy shape was reported as defective: ${healthyOrphans.join(', ')}`);

  return problems;
}

async function main() {
  const refArg = process.argv.slice(2).find((a) => a.startsWith('--ref='));
  const ref = refArg ? refArg.slice('--ref='.length) : 'HEAD';
  let sha;
  try {
    sha = git(['rev-parse', '--verify', `${ref}^{commit}`])[0];
  } catch (error) {
    bail(`Cannot verify: ref ${ref} does not resolve to a commit.\n  ${error.message}`);
  }

  const { configLoader, createFileMatcher } = loadPlaywrightInternals();

  // `--prove-control` exists because a control nobody has ever seen refuse is
  // not a control. It hands redControl a matcher that ignores nothing, which is
  // the shape the machinery takes when it quietly stops working, and the run is
  // a success only if the control complains. Anyone can reproduce the refusal
  // without editing the file.
  if (process.argv.includes('--prove-control')) {
    const sabotaged = (patterns) => {
      const real = createFileMatcher(patterns);
      const empty = !patterns || (Array.isArray(patterns) && !patterns.length);
      return empty ? real : (file) => (String(patterns).includes('*.spec.ts') ? false : real(file));
    };
    const caught = redControl(sabotaged);
    if (!caught.length) {
      console.error('FAIL: the red control passed a sabotaged matcher. It is not checking anything.');
      process.exit(2);
    }
    console.log(`OK: the red control refused a sabotaged matcher with ${caught.length} problem(s):`);
    for (const problem of caught) console.log(`  ${problem}`);
    process.exit(0);
  }

  const controlProblems = redControl(createFileMatcher);
  if (controlProblems.length) {
    console.error('Cannot verify: the red control did not behave.\n');
    for (const problem of controlProblems) console.error(`  ${problem}`);
    console.error(
      '\n  The control plants a spec that is excluded on the stated grounds and run\n' +
      '  nowhere, and a healthy shape that must pass. Until both come out right a\n' +
      '  zero from this gate is not evidence of anything. Exiting 2.'
    );
    process.exit(2);
  }

  const repoRoot = slash(git(['rev-parse', '--show-toplevel'])[0]);
  const atRef = git(['ls-tree', '-r', '--name-only', sha, '--', 'frontend']);
  const population = atRef.map((rel) => `${repoRoot}/${rel}`);
  const configs = atRef.filter((rel) => /^frontend\/playwright[^/]*\.config\.ts$/.test(rel));
  if (!configs.length) bail(`Cannot verify: ${sha} carries no frontend/playwright*.config.ts at all.`);

  // Playwright loads configs from disk. Say so when disk and ref disagree,
  // rather than reporting a ref's population against the working tree's rules.
  const drifted = git(['diff', '--name-only', sha, '--', 'frontend/playwright*.config.ts']);

  const projects = [];
  let loaded = 0;
  for (const rel of configs) {
    let config;
    try {
      config = await configLoader.loadConfig(configLoader.resolveConfigLocation(path.join(repoRoot, rel)));
    } catch (error) {
      bail(
        `Cannot verify: ${rel} did not load.\n  ${error.message}\n` +
        '  A config that throws selects nothing, and counting that as "selects nothing"\n' +
        '  would blame every spec in its directory for one broken file. Exiting 2.'
      );
    }
    loaded += 1;
    for (const project of config.projects) {
      projects.push({
        label: `${rel} [${project.project.name || '-'}]`,
        testDir: project.project.testDir,
        testMatch: project.project.testMatch,
        testIgnore: project.project.testIgnore,
      });
    }
  }

  const { selected, ignoredBy } = classify(createFileMatcher, projects, population);

  // Cross-check the selected set against playwright's own matcher through the
  // sibling's select(), so a divergence between the two halves surfaces here
  // rather than as two gates that disagree in silence.
  const viaSelect = new Set();
  for (const project of projects)
    for (const file of select(createFileMatcher, project, population)) viaSelect.add(slash(file).toLowerCase());
  const onlyHere = [...selected].filter((f) => !viaSelect.has(f));
  const onlyThere = [...viaSelect].filter((f) => !selected.has(f));
  if (onlyHere.length || onlyThere.length) {
    bail(
      'Cannot verify: this file\'s classification and the sibling gate\'s select() disagree.\n' +
      `  only here:  ${onlyHere.join(', ') || '(none)'}\n` +
      `  only there: ${onlyThere.join(', ') || '(none)'}\n` +
      '  The two halves must resolve selection identically or their verdicts are not\n' +
      '  about the same relation. Exiting 2.'
    );
  }

  const specs = atRef.filter((rel) => SPEC.test(rel));
  const key = (rel) => `${repoRoot}/${rel}`.toLowerCase();
  const unselected = specs.filter((rel) => !selected.has(key(rel)));
  const cohort = specs.filter((rel) => ignoredBy.has(key(rel)));
  const orphans = cohort.filter((rel) => !selected.has(key(rel)));
  const census = unselected.filter((rel) => !ignoredBy.has(key(rel)));

  console.log(`ref:                    ${ref} -> ${sha}`);
  console.log(`files in frontend/ at that commit: ${atRef.length}`);
  console.log(`playwright configs at that commit: ${configs.length} (all ${loaded} loaded)`);
  console.log(`projects across them:   ${projects.length}`);
  console.log(`tracked specs (*.spec.ts, *.spec.tsx): ${specs.length}`);
  console.log(`  selected by at least one project:    ${specs.length - unselected.length}`);
  console.log(`  selected by none:                    ${unselected.length}`);
  console.log();
  console.log(`ASSERTED -- specs a project reaches, matches, then deliberately ignores: ${cohort.length}`);
  for (const rel of cohort) {
    console.log(`  ${selected.has(key(rel)) ? 'run elsewhere' : 'RUN NOWHERE  '}  ${rel}`);
  }
  if (drifted.length) {
    console.log();
    console.log('NOTE: these configs differ between the ref and the working tree, and playwright');
    console.log(`      loaded the working-tree version: ${drifted.join(', ')}`);
  }

  console.log();
  console.log(`CENSUS (not asserted, exit code unaffected) -- unselected and never deliberately ignored: ${census.length}`);
  const byDirectory = new Map();
  for (const rel of census) {
    const dir = path.posix.dirname(rel);
    if (!byDirectory.has(dir)) byDirectory.set(dir, []);
    byDirectory.get(dir).push(rel);
  }
  for (const [dir, list] of [...byDirectory].sort((a, b) => b[1].length - a[1].length)) {
    // dir is repo-relative and testDir is absolute; compare like with like.
    const absoluteDir = `${repoRoot}/${dir}/`.toLowerCase();
    const reaching = projects.filter((p) => absoluteDir.startsWith(`${slash(p.testDir).replace(/\/+$/, '')}/`.toLowerCase()));
    console.log();
    console.log(`  ${dir}  (${list.length})`);
    console.log(reaching.length
      ? `    reached by ${reaching.length} project(s), none of whose testMatch names these files`
      : '    outside every configured testDir');
    for (const rel of list) console.log(`      ${path.posix.basename(rel)}`);
  }

  console.log();
  if (orphans.length) {
    console.error(
      `FAIL: ${orphans.length} of the ${cohort.length} spec(s) excluded on the stated grounds that they\n` +
      '      run under a dedicated config are selected by no configuration at all.\n'
    );
    for (const rel of orphans) {
      console.error(`  ${rel}`);
      console.error(`      excluded by: ${[...new Set(ignoredBy.get(key(rel)))].join(', ')}`);
    }
    console.error(
      '\n  Either give each one a configuration that selects it, or stop excluding it,\n' +
      '  because the exclusion is currently telling a reader something untrue.'
    );
    process.exit(1);
  }

  console.log(`OK: all ${cohort.length} deliberately excluded spec(s) at ${sha.slice(0, 9)} are selected elsewhere. ${census.length} further spec(s) are unselected and listed above without a verdict.`);
}

await main();
