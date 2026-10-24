// Fails the "conventions" check when a branch name, PR title or commit
// message doesn't follow the standard in conventions.js.
// Required on develop and main, so non-conforming PRs can't be merged.

const { checkBranch, checkCommit } = require('./conventions.js');

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const { eventName, payload } = context;
  const problems = [];
  let branch;
  let messages = [];

  if (eventName === 'pull_request') {
    const pr = payload.pull_request;
    branch = pr.head.ref;
    // Release PRs (develop -> main) carry already-checked commits.
    if (branch === 'develop' && pr.base.ref === 'main') return core.info('Release PR, skipped');
    const titleProblem = checkCommit(pr.title);
    if (titleProblem) problems.push(titleProblem.replace(/^Commit/, 'PR title'));
    const commits = await github.paginate(github.rest.pulls.listCommits, {
      owner, repo, pull_number: pr.number, per_page: 100,
    });
    messages = commits.map((c) => c.commit.message);
  } else if (eventName === 'push') {
    branch = payload.ref.replace('refs/heads/', '');
    messages = (payload.commits || []).map((c) => c.message);
  } else {
    return core.info(`Event ${eventName} not checked`);
  }

  const branchProblem = checkBranch(branch);
  if (branchProblem) problems.push(branchProblem);
  for (const m of messages) {
    const p = checkCommit(m);
    if (p) problems.push(p);
  }

  if (!problems.length) {
    core.info(`✓ ${branch}: branch and ${messages.length} commit(s) follow the standard`);
    return;
  }

  await core.summary
    .addHeading('Branch / commit standard not met', 3)
    .addList(problems)
    .addRaw('See docs/runbooks/issue-workflow.md. Fix with `git branch -m` or `git rebase -i` and force-push.')
    .write();
  core.setFailed(`${problems.length} problem(s):\n- ${problems.join('\n- ')}`);
};
