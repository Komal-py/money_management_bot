# Contributing

## Standing rule: pull regularly, merge through pull requests

When more than one person (or agent) works on this repository, keep your copy current and never push straight to `main`.

**Before you start work, and again before every push:**

```bash
git switch main
git pull --rebase origin main      # bring in everyone else's work
git switch -c feature/<short-name> # or: git switch feature/<short-name> && git rebase main
```

**While working (at least daily, and whenever someone else merges):**

```bash
git fetch origin
git rebase origin/main             # replay your commits on the latest main
# fix any conflicts, then: git add <files> && git rebase --continue
```

**To deliver:**

```bash
git push -u origin feature/<short-name>
gh pr create --base main --fill    # open a pull request; let CI run
```

Merge only after CI passes and a reviewer approves. After merging, everyone runs `git switch main && git pull --rebase` again.

Why: pulling often keeps conflicts small and early; pull requests give a review and CI checkpoint before code reaches `main`.

## Never commit secrets

`.env`, `.private/`, `.coordination/` and `.worktrees/` are ignored. Put real tokens, passwords, database URLs and private endpoint hostnames only in `.env`; use placeholders such as `https://<your-resource>.openai.azure.com/openai/v1` in documentation.
