# Live checks — 2026-09-26

Machine: Windows 11, Claude Code **2.1.283**, codex-cli 0.157.0. HEAD `b74d581`.
Probes ran in an isolated scratch project (own CLAUDE.md marker, `/probe` command, `probeagent`), never in this repo.

## Owner decision (2026-09-26)

**Do not use Fable 5.1.** Wherever a spec references Fable (5 or 5.1) as a seat, use **`claude-opus-5-5`** (the highest-tier model in use). This is applied at decompose time, per wave. See the knock-on under F7/H-B below.

## Results

| Check | Result | Spec impact |
|---|---|---|
| `--help` lists `--json-schema`, `--max-budget-usd`, `--permission-prompts` (host\|none), `--effort` (low..max), `--agent`, `--agents`, `--resume`, `--output-format` | yes | — |
| `--max-turns` | **not in `--help`, but accepted** (hidden flag) | Usable; E-H CLI smoke test should assert it stays accepted |
| `--permission-mode` choices | acceptEdits, **auto**, bypassPermissions, manual, dontAsk, plan | F4 flag exists |
| Slash prompt in `claude -p "/probe"` | **Expands correctly**, *but only* with `MSYS_NO_PATHCONV=1`. Under Git Bash the leading `/probe` is rewritten to `C:/Program Files/Git/probe` and the session gets a meaningless prompt. | **Root cause of the 2026-08-13 "no-op" finding is very likely MSYS path conversion, not the CLI.** D3's single explicit read-and-execute helper stays correct, since it's robust in every shell. E-H should add `MSYS_NO_PATHCONV=1` to Windows launch paths and a test for it. |
| Agent frontmatter `model:` (`--agent`) | **honoured** | — |
| Agent frontmatter `effort:` (`--agent`) | **honoured, directionally**: Sonnet 5 agent with `effort: low` used 0 thinking tokens; the same run with `--effort max` used 61. The init event's `per_turn_effort_active` is always False and is not a usable signal. | Still pin `--effort` on the CLI (F1/F2); the CLI overrides frontmatter |
| Agent frontmatter `omitClaudeMd: true` (`--agent`, main thread) | **NOT honoured**: the agent quoted the CLAUDE.md marker | F5 cannot rely on it for `--agent` headless runs. Re-test via `--agents` JSON / subagent at F5 time. `--bare` skips CLAUDE.md but forces `ANTHROPIC_API_KEY` auth, so it isn't usable on the subscription. |
| `/advisor <model>` headless | Works after the user skill was renamed to `stress-test` (owner decision). **But:** (1) it swallows the whole prompt ("Advisor set to Opus 5.5"), so no task runs in that session; (2) it **persists `advisorModel` to the user-level `~/.claude/settings.json`**, a global side effect that is unsafe for the autopilot. **Working per-run form:** `claude -p … --settings '{"advisorModel":"claude-opus-5-5"}'`. It produced a real `server_tool_use: advisor` call with separate cost in `modelUsage` (Sonnet 5 $0.125 + Opus 5.5 $0.132 for a trivial Q) and left global settings untouched. | F7/H-B must use the `--settings` form, never `/advisor`. The ledger can meter advisor cost from `modelUsage`. |
| Auto-mode classifier billing on this subscription | **Not determinable locally.** The changelog says free "for Claude API and Enterprise users" and is silent on Max. | F4 ships disabled; measure with the F2 ledger before any flip |
| Auto-memory per agent / settings | On by default in `-p` (init `memory_paths.auto` set). The only CLI switch found is `--bare` (unusable, see above). No per-agent control found. | H-E / builder sessions: treat auto-memory as on; settings-key control is still unverified |
| `claude-opus-5-5` | available (one-turn cold ≈ $0.128 notional) | top seat |
| `claude-sonnet-5` | available (≈ $0.054) | — |
| `claude-haiku-4-5` | available (≈ $0.025) | — |
| `claude-fable-5-1` | **not on subscription** ("requires usage credits") | excluded by owner decision |
| Codex models (ChatGPT account, `models_cache.json` fetched 2026-09-26T13:32Z) | listed: gpt-6-astra, **gpt-6-sol**, **gpt-6-luna**, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-5.5 | `~/.codex/config.toml` defaults to `gpt-6-luna`/medium while `autopilot.json` pins `gpt-5.6-terra`: pin CX explicitly in F10 |

## Knock-on of the Fable decision

The spec's F7 options assumed two distinct top-tier models: advisor = Fable 5.1 with reviewer = Opus 5.5, or the reverse. With Opus 5.5 as the only top seat, an Opus 5.5 advisor for S5 would share the reviewer's model and break checker independence. Options, to decide at F7/H-B decompose time:
1. no advisor;
2. an advisor on a model other than the reviewer's (for example Sonnet 5 advising Haiku-tier work only);
3. review advisor-assisted tasks on a different model family (CX) as the checker.
