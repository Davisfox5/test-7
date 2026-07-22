# CLAUDE.md

Repo: **pokemon-bulk-lister** — Python 3.11 Flask 3 web app (`pokemon-bulk-lister/webapp/`) plus a 7-step CLI pipeline (`pokemon-bulk-lister/scripts/`) that turns binder-page photos into priced bulk card listings for TCGPlayer, Whatnot, and eBay. Business logic in `pokemon-bulk-lister/lib/`; SQLite state in `webapp/db.py`; tests in `pokemon-bulk-lister/tests/`.

Test command (matches CI): `pytest -q` run from `pokemon-bulk-lister/`. CI also runs `python -m compileall -q lib webapp scripts`. There is no deployment — the app runs locally only; no branch auto-deploys anywhere.

## Model routing (dev)

Cost-aware, top-down routing for Claude Code subagents (`.claude/agents/`). The highest tier (fable) is the default for judgment-heavy work and delegates DOWN to cheaper tiers for mechanical work. No agent ever self-assesses its own capability and escalates upward; every escalation trigger is external (a fixed path rule, a failing test).

| Agent | Model | Invoke when |
|---|---|---|
| `codebase-analyst` | fable | Explaining architecture, tracing data/control flow (pricing flow, OAuth lifecycle). NOT for pure lookups — see scout-first rule. |
| `code-reviewer` | fable | Reviewing diffs/PRs; includes sensitive-path and migration-safety checklists; runs `pytest -q`. |
| `planner` | fable | Refactor strategies, roadmaps, rollout sequencing. Writes to `docs/` only. |
| `bug-hunter` | fable | Reproducing/localizing bugs; runs tests; proposes fixes but never writes them. |
| `security-reviewer` | fable | Auditing secrets/token handling, Playwright session state, Flask/SQL surface, dependency risk. |
| `spec-writer` | opus | Turning a fable-tier plan/diagnosis into an implementation spec in `docs/specs/`. |
| `code-writer` | sonnet | Implementing changes strictly against a spec; runs `pytest -q` and shows real output; stops rather than improvises. |
| `researcher` | sonnet | Gathering external API/library docs (eBay, pokemontcg.io, Cloudinary, anthropic SDK, Playwright); output is always unverified claims. |
| `code-scout` | haiku | Pure lookups: "where is X", "list call sites", "which files reference Y". file:line output only. |

### Fixed rules

1. **Sensitive-path rule (external trigger, never a judgment call).** Specs and edits touching these four files are authored at the fable tier directly; `spec-writer` and `code-writer` refuse them and report back:
   - `pokemon-bulk-lister/lib/pricing.py` (sets real list prices)
   - `pokemon-bulk-lister/lib/ebay_lister.py` (publishes live eBay listings)
   - `pokemon-bulk-lister/lib/ebay_oauth.py` (client secret + disk-cached refresh token)
   - `pokemon-bulk-lister/webapp/db.py` (schema + migrations run on every launch)

   *Revisit caveat:* this rule trades a small fable increase (4 files) for the large scout/writer reduction elsewhere. If sensitive-path work ever dominates the workload, revisit this rule — it would then be costing more than it saves.
2. **Scout-first rule.** Any pure search/lookup goes to `code-scout` (haiku), never to `codebase-analyst` (fable). Analyst is only for questions requiring interpretation.
3. **Researcher output is unverified.** All `researcher` findings are documentation claims, not verified behavior. Fable-tier consumers must treat them as unverified input and confirm against pinned versions (`requirements.txt`) and real behavior before relying on them. The researcher's own prompt enforces the labeling; this rule enforces the consumer side.

### Enforcement layers

- **Mechanically enforced** (applied by the harness whenever an agent is invoked): each agent's `model:` and `tools:` frontmatter. Read-only agents genuinely lack edit/write tools.
- **Advisory / prompt-enforced**: whether to delegate at all, and all path restrictions (planner→`docs/`, spec-writer→`docs/specs/`, the sensitive-path refusals). Frontmatter cannot scope file paths, so these live in agent prompts and this file. CLAUDE.md steers; frontmatter binds.
