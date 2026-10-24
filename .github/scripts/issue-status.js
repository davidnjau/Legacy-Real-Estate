// Moves issues through status labels based on git activity.
//
//   new issue                              -> status: todo
//   commit on a feature branch (#25/W2-01) -> status: in-progress
//   PR merged into develop                 -> status: in-review
//   PR merged into main                    -> status: done  (issue closed)
//
// Issues can be referenced by number (#25, GH-25) or by wave ID (W2-01),
// which is matched against the start of the issue title ("W2-01 — ...").
//
// Invoked from .github/workflows/issue-status.yml via actions/github-script.

const { extractRefs, refsFromBranch } = require('./conventions.js');

const STATUS = {
  todo: { name: 'status: todo', color: 'ededed', description: 'Not started' },
  inProgress: { name: 'status: in-progress', color: 'fbca04', description: 'Work started on a branch' },
  inReview: { name: 'status: in-review', color: '1d76db', description: 'Merged to develop, awaiting release' },
  done: { name: 'status: done', color: '0e8a16', description: 'Merged to main' },
};
const ORDER = [STATUS.todo, STATUS.inProgress, STATUS.inReview, STATUS.done].map((s) => s.name);

const DEVELOP = process.env.DEVELOP_BRANCH || 'develop';
const MAIN = process.env.MAIN_BRANCH || 'main';
const MERGE_RE = /^Merge (pull request|branch)/;

class Refs {
  constructor() { this.numbers = new Set(); this.waves = new Set(); }
  add({ numbers, waves }) { numbers.forEach((n) => this.numbers.add(n)); waves.forEach((w) => this.waves.add(w)); }
  addText(...t) { this.add(extractRefs(...t)); }
  addBranch(b) { this.add(refsFromBranch(b)); }
  get size() { return this.numbers.size + this.waves.size; }
}

// Map "W2-01" -> issue number, using issue titles. Loaded once per run.
async function waveIndex(github, owner, repo) {
  const issues = await github.paginate(github.rest.issues.listForRepo, { owner, repo, state: 'all', per_page: 100 });
  const index = new Map();
  for (const i of issues) {
    if (i.pull_request) continue;
    const m = i.title.match(/^\s*\[?(W\d+-\d{2})\b/i);
    if (m && !index.has(m[1].toUpperCase())) index.set(m[1].toUpperCase(), i.number);
  }
  return index;
}

async function resolve(github, owner, repo, refs, core) {
  const numbers = new Set(refs.numbers);
  if (refs.waves.size) {
    const index = await waveIndex(github, owner, repo);
    for (const w of refs.waves) {
      if (index.has(w)) numbers.add(index.get(w));
      else core.info(`${w}: no issue title starts with this ID, skipped`);
    }
  }
  return numbers;
}

async function ensureLabels(github, owner, repo) {
  for (const s of Object.values(STATUS)) {
    try {
      await github.rest.issues.getLabel({ owner, repo, name: s.name });
    } catch (e) {
      if (e.status !== 404) throw e;
      await github.rest.issues.createLabel({ owner, repo, ...s });
    }
  }
}

async function setStatus({ github, owner, repo, number, target, core, close, note }) {
  let issue;
  try {
    ({ data: issue } = await github.rest.issues.get({ owner, repo, issue_number: number }));
  } catch (e) {
    if (e.status === 404 || e.status === 410) return core.info(`#${number}: not found, skipped`);
    throw e;
  }
  if (issue.pull_request) return core.info(`#${number}: is a pull request, skipped`);

  const labels = issue.labels.map((l) => (typeof l === 'string' ? l : l.name));
  const current = labels.filter((l) => ORDER.includes(l));
  const currentRank = Math.max(-1, ...current.map((l) => ORDER.indexOf(l)));
  const targetRank = ORDER.indexOf(target);

  // Never move an issue backwards (e.g. a late commit on an already-merged issue).
  if (currentRank > targetRank) {
    return core.info(`#${number}: already at "${ORDER[currentRank]}", not moving back to "${target}"`);
  }

  for (const l of current) {
    if (l !== target) {
      await github.rest.issues.removeLabel({ owner, repo, issue_number: number, name: l }).catch(() => {});
    }
  }
  const changed = !current.includes(target);
  if (changed) {
    await github.rest.issues.addLabels({ owner, repo, issue_number: number, labels: [target] });
    if (note) await github.rest.issues.createComment({ owner, repo, issue_number: number, body: note });
  }
  if (close && issue.state === 'open') {
    await github.rest.issues.update({ owner, repo, issue_number: number, state: 'closed', state_reason: 'completed' });
  }
  core.info(`#${number}: ${changed ? `-> ${target}` : `already ${target}`}${close ? ' (closed)' : ''}`);
}

async function prRefs(github, owner, repo, pr) {
  const refs = new Refs();
  refs.addText(pr.title, pr.body);
  refs.addBranch(pr.head.ref);
  const commits = await github.paginate(github.rest.pulls.listCommits, {
    owner, repo, pull_number: pr.number, per_page: 100,
  });
  const innerPrs = new Set();
  for (const c of commits) {
    const msg = c.commit.message;
    const inner = msg.match(/^Merge pull request #(\d+)/);
    if (inner) innerPrs.add(Number(inner[1]));
    if (MERGE_RE.test(msg)) continue;
    refs.addText(msg);
  }
  // Release PRs (develop -> main): also pick up issues from the PRs merged into develop.
  for (const n of innerPrs) {
    try {
      const { data: p } = await github.rest.pulls.get({ owner, repo, pull_number: n });
      refs.addText(p.title, p.body);
      refs.addBranch(p.head.ref);
    } catch (_) { /* ignore */ }
  }
  refs.numbers.delete(pr.number);
  return refs;
}

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const { eventName, payload } = context;
  await ensureLabels(github, owner, repo);

  if (eventName === 'issues' && payload.action === 'opened') {
    const has = payload.issue.labels.some((l) => ORDER.includes(l.name));
    if (!has) await setStatus({ github, owner, repo, number: payload.issue.number, target: STATUS.todo.name, core });
    return;
  }

  if (eventName === 'push') {
    const branch = payload.ref.replace('refs/heads/', '');
    if ([MAIN, DEVELOP].includes(branch)) return core.info('Push to protected branch, nothing to do');
    const refs = new Refs();
    for (const c of payload.commits || []) {
      if (MERGE_RE.test(c.message)) continue;
      refs.addText(c.message);
    }
    refs.addBranch(branch);
    if (!refs.size) return core.info(`No issue references on ${branch}`);
    const sha = payload.head_commit ? payload.head_commit.id.slice(0, 7) : 'push';
    for (const number of await resolve(github, owner, repo, refs, core)) {
      await setStatus({
        github, owner, repo, number, core, target: STATUS.inProgress.name,
        note: `🚧 Work started on branch \`${branch}\` (${sha}).`,
      });
    }
    return;
  }

  if (eventName === 'pull_request' && payload.action === 'closed' && payload.pull_request.merged) {
    const pr = payload.pull_request;
    const base = pr.base.ref;
    let target, close = false, note;
    if (base === DEVELOP) {
      target = STATUS.inReview.name;
      note = `🔍 Merged into \`${DEVELOP}\` via #${pr.number}. Now in review.`;
    } else if (base === MAIN) {
      target = STATUS.done.name;
      close = true;
      note = `✅ Released to \`${MAIN}\` via #${pr.number}.`;
    } else {
      return core.info(`PR merged into ${base}; no status mapping`);
    }
    const refs = await prRefs(github, owner, repo, pr);
    if (!refs.size) return core.info(`PR #${pr.number} references no issues`);
    for (const number of await resolve(github, owner, repo, refs, core)) {
      await setStatus({ github, owner, repo, number, core, target, close, note });
    }
  }
};
