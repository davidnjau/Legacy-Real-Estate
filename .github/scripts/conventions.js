// Shared branch / commit / issue-reference rules.
// Used by issue-status.js, check-conventions.js and the local git hooks.

// Branch types and whether they must carry an issue reference.
const BRANCH_TYPES = {
  feature: true,
  fix: true,
  hotfix: true,
  refactor: true,
  test: true,
  chore: false,
  ci: false,
  docs: false,
  release: false,
};

// Branches that are never checked.
const EXEMPT_BRANCHES = [/^main$/, /^develop$/, /^dependabot\//, /^renovate\//];

// Issue reference inside a branch: W2-01, 25, GH-25, issue-25
const ISSUE_IN_BRANCH = '(?:W\\d+-\\d{2}|(?:issue-|gh-)?\\d+)';
const BRANCH_RE = new RegExp(
  `^(${Object.keys(BRANCH_TYPES).join('|')})\\/(?:(${ISSUE_IN_BRANCH})-)?[a-z0-9][a-z0-9.-]*$`,
  'i',
);

// Conventional commit header: type(scope)!: subject
const COMMIT_TYPES = ['feat', 'fix', 'chore', 'docs', 'refactor', 'test', 'ci', 'build', 'perf', 'style', 'revert'];
const COMMIT_RE = new RegExp(`^(${COMMIT_TYPES.join('|')})(\\([a-z0-9._/-]+\\))?!?: \\S.{0,99}$`);
const EXEMPT_COMMIT_RE = /^(Merge (pull request|branch|remote-tracking branch)|Revert ")/;

// "#25", "GH-25" -> issue number; "W2-01" -> wave ID (resolved via issue title).
const NUMBER_REF_RE = /(?:^|[^\w/])(?:#|GH-)(\d+)\b/gi;
const WAVE_REF_RE = /\b(W\d+-\d{2})\b/gi;

function extractRefs(...texts) {
  const numbers = new Set();
  const waves = new Set();
  for (const t of texts) {
    if (!t) continue;
    for (const m of t.matchAll(NUMBER_REF_RE)) numbers.add(Number(m[1]));
    for (const m of t.matchAll(WAVE_REF_RE)) waves.add(m[1].toUpperCase());
  }
  return { numbers, waves };
}

function refsFromBranch(branch) {
  const out = { numbers: new Set(), waves: new Set() };
  if (!branch) return out;
  const m = branch.match(/^(?:[a-z]+\/)?(W\d+-\d{2}|(?:issue-|gh-)?\d+)(?:[-_]|$)/i);
  if (!m) return out;
  const id = m[1];
  if (/^W/i.test(id)) out.waves.add(id.toUpperCase());
  else out.numbers.add(Number(id.replace(/\D/g, '')));
  return out;
}

function checkBranch(branch) {
  if (EXEMPT_BRANCHES.some((r) => r.test(branch))) return null;
  const m = branch.match(BRANCH_RE);
  if (!m) {
    return `Branch "${branch}" must look like <type>/<issue>-<short-description>, e.g. feature/W2-01-invoices or fix/25-rounding. Types: ${Object.keys(BRANCH_TYPES).join(', ')}. Lowercase, hyphens only.`;
  }
  const [, type, issue] = m;
  if (BRANCH_TYPES[type.toLowerCase()] && !issue) {
    return `Branch "${branch}" is a ${type} branch and must include an issue ID, e.g. ${type}/W2-01-short-description.`;
  }
  return null;
}

function checkCommit(message) {
  const header = message.split('\n')[0];
  if (EXEMPT_COMMIT_RE.test(header)) return null;
  if (!COMMIT_RE.test(header)) {
    return `Commit "${header}" must follow <type>(<scope>): <subject>, e.g. "feat(core): add invoice numbering W2-01". Types: ${COMMIT_TYPES.join(', ')}. Max 100 chars after the colon.`;
  }
  return null;
}

module.exports = {
  BRANCH_TYPES, COMMIT_TYPES, extractRefs, refsFromBranch, checkBranch, checkCommit,
};
