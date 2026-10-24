# Issue workflow

## Status labels (automatic)

`.github/workflows/issue-status.yml` moves issues through these labels:

| Event | Label |
| --- | --- |
| Issue opened | `status: todo` |
| Commit referencing the issue pushed to a feature branch | `status: in-progress` |
| PR merged into `develop` | `status: in-review` |
| PR merged into `main` (release) | `status: done` + issue closed |

Labels never move backwards; a late commit on a released issue is ignored.

## Referencing an issue

Use the wave ID from the issue title (`W2-01`) or the issue number (`#25`).
Both work in branch names, commit messages and PR titles/bodies.

## Branch names

```
<type>/<issue>-<short-description>
feature/W2-01-invoice-numbering
fix/25-rounding-error
chore/bump-turbo
```

| Type | Issue ID required |
| --- | --- |
| `feature`, `fix`, `hotfix`, `refactor`, `test` | yes |
| `chore`, `ci`, `docs`, `release` | no |

Lowercase, hyphens only. `main`, `develop` and `dependabot/*` are exempt.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>
feat(core): add branch invoice numbering W2-01
fix(website): correct listing price rounding #25
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
Merge commits are exempt. The PR title follows the same format.

## Enforcement

- **Locally:** run `make hooks` once per clone. Bad commit messages are
  rejected at `git commit`, bad branch names and commits at `git push`.
- **On GitHub:** the `conventions` check fails on non-conforming PRs and is
  required on `develop` and `main`, so they can't be merged.

To fix a rejected branch: `git branch -m feature/W2-01-new-name`.
To fix commit messages: `git rebase -i develop`, mark commits `reword`, then
`git push --force-with-lease`.

## Flow

```
feature/W2-01-x ──commit──▶ in-progress
     │ PR → develop (merged) ▶ in-review
develop ─ PR → main (merged) ▶ done (closed)
```
