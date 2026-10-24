# Issue workflow

Issue status labels move automatically (`.github/workflows/issue-status.yml`).

| Event | Label |
| --- | --- |
| Issue opened | `status: todo` |
| Commit referencing the issue pushed to a feature branch | `status: in-progress` |
| PR merged into `develop` | `status: in-review` |
| PR merged into `main` (release) | `status: done` + issue closed |

Labels never move backwards; a late commit on a released issue is ignored.

## Tagging an issue

Any of these link work to issue #12:

- Branch name: `feature/12-tenant-login`, `fix/12-mpesa-timeout`, `12-tenant-login`
- Commit message: `feat(core): tenant login #12` or `refs GH-12`
- PR title/body: `Closes #12`

One commit can reference several issues (`#12 #15`).

## Flow

```
feature/12-x  ──commit #12──▶  in-progress
     │ PR → develop (merged)  ▶  in-review
develop ─ PR → main (merged)  ▶  done (closed)
```

The `develop → main` release PR picks up every issue referenced by the commits,
branches and PRs it contains, so it doesn't need to list them by hand.
