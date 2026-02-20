# AlgoEnergy Web - Project Guide

## Security

- **Secrets live in `.env`** (gitignored). Never commit API keys or tokens.
- The `ENTSOE_API_KEY` is used by `scripts/fetch_entsoe.py` and GitHub Actions secrets.
- Never log, print, or hardcode secrets. Use `os.environ.get()`.
- Avoid `eval()`, `exec()`, `pickle.loads()`, `subprocess(shell=True)`, `yaml.load()` (use `yaml.safe_load()`), and unsanitized string formatting in URLs/commands.
- The `security-guidance` plugin is enabled -- follow its recommendations on any flagged code.

## Repository Structure

```
algo_energy_web/
  index.html, reseni.html, proces.html, kontakt.html, data.html  -- static site
  assets/styles.css                                                -- styles
  scripts/
    fetch_ote.py           -- OTE day-ahead market data fetcher
    fetch_entsoe.py        -- ENTSO-E balancing capacity fetcher
    fetch_ro_dam.py        -- RO day-ahead prices from ENTSO-E (A44)
  data/
    hourly/YYYY.csv        -- hourly OTE prices (CZ)
    qh/YYYY.csv            -- quarter-hourly OTE prices (from 2025-10-01)
    entsoe/afrr/YYYY.csv   -- aFRR accepted bid stats
    entsoe/mfrr/YYYY.csv   -- mFRR accepted bid stats
    ro/hourly/YYYY.csv     -- RO day-ahead hourly prices
  .github/workflows/       -- CI and data-fetch automation
  .claude/agents/          -- Claude Code agent definitions
  METHODOLOGY.md           -- data source documentation
  CLAUDE.md                -- this file
  pyproject.toml           -- ruff + pyright config
```

## Development Commands

```bash
# Install dev tools
uv sync --group dev

# Lint
uv run ruff check scripts/
uv run ruff format --check scripts/

# Auto-fix
uv run ruff check --fix scripts/
uv run ruff format scripts/

# Type check
uv run pyright
```

## Shell Command Style

- Use absolute paths: `uv run ruff check /home/coder/algo-energy-web/scripts/`
- Avoid `cd && command` chains -- use direct paths instead. Chained commands with `&&` break permission matching in `.claude/settings.json` because the shell sees the entire string, not individual commands.
- Never use `git -C <path>` -- it also breaks permission matching. Use absolute paths to files instead.
- Use `TaskOutput` to check on background tasks rather than polling with `sleep` loops.

## Code Style

- **Formatter/linter**: ruff (line-length 120, Python 3.12 target)
- **Type checker**: pyright in standard mode
- **Rules**: E, W, F, I, N, UP, B, SIM, TCH, RUF
- ASCII only in source files -- no emojis, no special Unicode characters in code or comments.
- No obvious comments ("increment counter", "return result"). Only comment non-obvious logic.
- All scripts use stdlib only -- no third-party runtime dependencies.
- Type hints on function signatures. Use `X | None` over `Optional[X]`.

## Context Recovery

After context compaction, re-read:
1. This file (`CLAUDE.md`)
2. `METHODOLOGY.md` for data source details
3. The specific files you were working on

## Allowed Operations

These are always safe (auto-allowed in settings.json):

**Read-only / inspection:**
- `git status`, `git log`, `git diff`, `git branch`, `git show`, `git blame`, `git ls-files`, `git describe`, `git shortlog`, `git rev-list`, `git rev-parse`, `git reflog`
- `gh pr list/view/diff/checks`, `gh issue list/view`, `gh api`, `gh run list/view/watch`, `gh repo view`, `gh release list/view`, `gh label list`, `gh browse`, `gh search`
- `ls`, `tree`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, `pwd`, `which`, `sort`, `uniq`, `diff`
- `WebSearch`

**Development:**
- `uv run ruff check`, `uv run ruff format`, `uv run pyright`
- `uv sync`, `uv add`, `uv lock`, `uv tree`, `uv export`, `uv pip`, `uv venv`
- `pytest`, `uv run pytest`

**Git write operations:**
- `git add`, `git commit`, `git push`, `git fetch`, `git pull`, `git rebase`, `git merge`, `git stash`, `git switch`, `git checkout`, `git tag`, `git cherry-pick`, `git remote`, `git submodule`

## Phase Completion Checklist (PCC)

Before committing any meaningful change, run through these steps:

| Step | Action | How |
|------|--------|-----|
| -1 | **Feature branch** | Create a feature branch if not already on one |
| 0 | **Sync with remote** | `git fetch origin && git rebase origin/main` |
| 1 | **Pre-commit hygiene** | Remove debug prints, check no secrets in diff, no leftover TODOs |
| 2 | **Code quality** | Run `code-quality-validator` agent (`.claude/agents/code-quality-validator.md`) -- runs ruff + pyright |
| 3 | **Code review** | Run `code-reviewer` agent (`.claude/agents/code-reviewer.md`) -- independent review |
| 4 | **Documentation** | Run `docs-updater` agent (`.claude/agents/docs-updater.md`) -- verify docs are current |
| 5 | **Commit & push** | Stage changes, commit with descriptive message, push |
| 6 | **Create PR** | Run `pr-writer` agent (`.claude/agents/pr-writer.md`), then create PR with generated description |

### Agent Reference

| Agent | File | Purpose |
|-------|------|---------|
| code-quality-validator | `.claude/agents/code-quality-validator.md` | Runs ruff + pyright, reports/fixes issues |
| code-reviewer | `.claude/agents/code-reviewer.md` | Independent review for bugs, security, quality |
| docs-updater | `.claude/agents/docs-updater.md` | Verifies documentation matches codebase |
| pr-writer | `.claude/agents/pr-writer.md` | Generates PR title and description |

### Failure & Rollback Protocol

- If code quality (step 2) or review (step 3) finds issues, fix them and re-run the failing step before proceeding.
- If a step fails twice consecutively, stop and report the issue to the user.
- Keep it simple -- no formal tracking, just fix and move on.

### When to Skip Steps

- Trivial fixes (typos, data updates): steps 1 + 5 are sufficient.
- Data-only changes (CSV updates): step 5 only.
- Use judgment -- the PCC is a guide, not a gate.

## Planning Requirement

Before starting non-trivial implementation work, verify consistency:
- Check that planned changes align with the patterns in `METHODOLOGY.md`.
- If changes affect data fetching scripts, confirm the data format stays compatible with existing CSVs in `data/`.
- If changes affect the static site, confirm links and references remain valid.
