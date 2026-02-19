# AlgoEnergy Web - Project Guide

## Security

- **Secrets live in `.env`** (gitignored). Never commit API keys or tokens.
- The `ENTSOE_API_KEY` is used by `scripts/fetch_entsoe.py` and GitHub Actions secrets.
- Never log, print, or hardcode secrets. Use `os.environ.get()`.
- Avoid `eval()`, `exec()`, unsanitized string formatting in URLs/commands.

## Repository Structure

```
algo_energy_web/
  index.html, reseni.html, proces.html, kontakt.html, data.html  -- static site
  assets/styles.css                                                -- styles
  scripts/
    fetch_ote.py           -- OTE day-ahead market data fetcher
    fetch_entsoe.py        -- ENTSO-E balancing capacity fetcher
    fetch_ro_dam.py        -- RO day-ahead prices from ENTSO-E (A44)
    debug_entsoe_xml.py    -- ENTSO-E API debug tool
  data/
    hourly/YYYY.csv        -- hourly OTE prices (CZ)
    qh/YYYY.csv            -- quarter-hourly OTE prices (from 2025-10-01)
    entsoe/afrr/YYYY.csv   -- aFRR accepted bid stats
    entsoe/mfrr/YYYY.csv   -- mFRR accepted bid stats
    ro/hourly/YYYY.csv     -- RO day-ahead hourly prices
  .github/workflows/       -- CI and data-fetch automation
  METHODOLOGY.md           -- data source documentation
  CLAUDE.md                -- this file
  pyproject.toml           -- ruff + pyright config
```

## Development Commands

```bash
# Lint
ruff check scripts/
ruff format --check scripts/

# Auto-fix
ruff check --fix scripts/
ruff format scripts/

# Type check
pyright

# Install dev tools
pip install ruff pyright
```

## Shell Command Style

- Use absolute paths: `ruff check C:/my_source/algo_energy_web/scripts/`
- Avoid `cd && command` chains -- use direct paths instead.
- Use `TaskOutput` to check on background tasks.

## Code Style

- **Formatter/linter**: ruff (line-length 120, Python 3.12 target)
- **Type checker**: pyright in standard mode
- **Rules**: E, W, F, I, N, UP, B, SIM, TCH, RUF
- ASCII only in source files -- no emojis in code or comments.
- No obvious comments ("increment counter", "return result"). Only comment non-obvious logic.
- All scripts use stdlib only -- no third-party runtime dependencies.
- Type hints on function signatures. Use `X | None` over `Optional[X]`.

## Context Recovery

After context compaction, re-read:
1. This file (`CLAUDE.md`)
2. `METHODOLOGY.md` for data source details
3. The specific files you were working on

## Allowed Operations

These are always safe (read-only):
- `git status`, `git log`, `git diff`, `git branch`, `git show`
- `gh pr list/view/diff/checks`, `gh issue list/view`
- `ls`, `tree`, `ruff check`, `ruff format --check`, `pyright`
- `WebSearch`

## Phase Completion Checklist (PCC)

Before committing any meaningful change, run through these steps:

| Step | Action | How |
|------|--------|-----|
| 1 | **Pre-commit hygiene** | Remove debug prints, check no secrets in diff, no leftover TODOs |
| 2 | **Code quality** | Run `code-quality-validator` agent (ruff + pyright) |
| 3 | **Code review** | Run `code-reviewer` agent for independent review |
| 4 | **Documentation** | Run `docs-updater` agent to verify docs are current |
| 5 | **Commit & push** | Stage changes, commit with descriptive message, push |
| 6 | **Create PR** | Run `pr-writer` agent, then create PR with generated description |

### Failure Handling

If code quality (step 2) or review (step 3) finds issues, fix them and re-run the failing step before proceeding. Keep it simple -- no formal tracking, just fix and move on.

### When to Skip Steps

- Trivial fixes (typos, data updates): steps 1 + 5 are sufficient.
- Data-only changes (CSV updates): step 5 only.
- Use judgment -- the PCC is a guide, not a gate.
