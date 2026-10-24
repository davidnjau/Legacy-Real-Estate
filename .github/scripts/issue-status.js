// Moves issues through status labels based on git activity.
//
//   new issue                         -> status: todo
//   commit on a feature branch #N     -> status: in-progress
//   PR merged into develop            -> status: in-review
//   PR merged into main               -> status: done  (issue closed)
//
// Invoked from .github/workflows/issue-status.yml via actions/github-script.

const STATUS = {
  todo: { name: 'status: todo', color: 'ededed', description: 'Not started' },
  inProgress: { name: 'status: in-progress', color: 'fbca04', description: 'Work started on a branch' },
  inReview: { name: 'status: in-review', color: '1d76db', description: 'Merged to develop, awaiting release' },
  done: { name: 'status: done', color: '0e8a16', description: 'Merged to main' },
};
const ORDER = [STATUS.todo, STATUS.inProgress, STATUS.inReview, STATUS.done].map((s) => s.name);

const DEVELOP = process.env.DEVELOP_BRANCH || 'develop';
const MAIN = process.env.MAIN_BRANCH || 'main';

// "#12", "GH-12", "fixes #12", "owner/repo#12" (same repo only is enforced later)
const REF_RE = /(?:^|[^\w/])(?:#|GH-)(\d+)\b/gi;
// branch names like feature/12-login, fix/GH-12, 12-login, issue-12
const BRANCH_RE = /^(?:[a-z]+\/)?(?:issue-|gh-)?(\d+)(?:[-_]|$)/i;

function extractRefs(...texts) {
  const out = new Set();
  for (const t of texts) {
    if (!t) continue;
    for (const m of t.matchAll(REF_RE)) out.add(Number(m[1]));
  }
  return out;
}

function refFromBranch(branch) {
  if (!branch) return null;
  const m = branch.match(BRANCH_RE);
  return m ? Number(m[1]) : null;
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

async function setStatus({ github, owner, repo, number, target, core, close, note, allowBackward = false }) {
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
  if (!allowBackward && currentRank > targetRank) {
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

async function prIssueRefs(github, owner, repo, pr) {
  const refs = extractRefs(pr.title, pr.body);
  const b = refFromBranch(pr.head.ref);
  if (b) refs.add(b);
  const commits = await github.paginate(github.rest.pulls.listCommits, {
    owner, repo, pull_number: pr.number, per_page: 100,
  });
  for (const c of commits) {
    // Merge commits like "Merge pull request #3 from ..." reference PRs, not issues;
    // the PR check in setStatus filters those, but skip them to save API calls.
    if (/^Merge (pull request|branch)/.test(c.commit.message)) continue;
    for (const n of extractRefs(c.commit.message)) refs.add(n);
  }
  // Also include issues referenced by the PRs merged into this one (develop -> main release PRs).
  const mergedPrs = new Set();
  for (const c of commits) {
    const m = c.commit.message.match(/^Merge pull request #(\d+)/);
    if (m) mergedPrs.add(Number(m[1]));
  }
  for (const n of mergedPrs) {
    try {
      const { data: inner } = await github.rest.pulls.get({ owner, repo, pull_number: n });
      for (const r of extractRefs(inner.title, inner.body)) refs.add(r);
      const ib = refFromBranch(inner.head.ref);
      if (ib) refs.add(ib);
    } catch (_) { /* ignore */ }
  }
  refs.delete(pr.number);
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
    const refs = new Set();
    for (const c of payload.commits || []) {
      if (/^Merge (pull request|branch)/.test(c.message)) continue;
      for (const n of extractRefs(c.message)) refs.add(n);
    }
    const b = refFromBranch(branch);
    if (b) refs.add(b);
    if (!refs.size) return core.info(`No issue references on ${branch}`);
    for (const number of refs) {
      await setStatus({
        github, owner, repo, number, core, target: STATUS.inProgress.name,
        note: `🚧 Work started on branch \`${branch}\` (${payload.head_commit ? payload.head_commit.id.slice(0, 7) : 'push'}).`,
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
    const refs = await prIssueRefs(github, owner, repo, pr);
    if (!refs.size) return core.info(`PR #${pr.number} references no issues`);
    for (const number of refs) {
      await setStatus({ github, owner, repo, number, core, target, close, note });
    }
  }
};

module.exports._test = { extractRefs, refFromBranch };
