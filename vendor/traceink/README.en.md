<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/logo-light.svg">
    <img src=".github/logo-light.svg" alt="Traceink" width="360">
  </picture>
</p>

<p align="center"><strong>Make sense of a day—or a week—of agent work.</strong></p>

<p align="center">A read-only agent skill for reviewing Copilot CLI, Codex, and Claude Code work.</p>

<p align="center">English · <a href="README.md">简体中文</a></p>

<div align="center">

[![CI][ci-badge]][ci-url]
[![Latest release][release-badge]][release-url]
[![MIT License][license-badge]][license-url]
[![Node.js 18+][node-badge]][node-url]

</div>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#why-this-exists">Why</a> ·
  <a href="#what-a-review-looks-like">Example</a> ·
  <a href="#privacy-and-boundaries">Privacy</a>
</p>

## Why this exists

After a day of parallel work with Codex or Claude Code, the record is usually complete and still hard to read. The useful parts are buried across transcripts, diffs, tests, documents, and long unattended runs.

A list of session titles does not say how the work changed. Reading every transcript takes too long. A neat automatic summary can be worse: it often removes failed routes and implies decisions you never made.

Traceink groups related evidence into a few **worklines**. A workline is one thread of intent that may span several sessions or tools. You get the context, the important changes, the evidence, and the questions that still need you.

Traceink stops there. It does not write your conclusion for you.

## What you get

- **Worklines instead of session summaries.** Related Codex and Claude Code sessions stay together. Linked local material can be included when it matters.
- **A short index before the detail.** See what deserves attention, then open one workline at a time.
- **Evidence you can reopen.** Important claims point back to the provider, session, file, and relevant time or message range.
- **A clear participation record.** Traceink distinguishes where you intervened, where an agent worked alone, where both were involved, and where the evidence is unclear.
- **Daily, weekly, and custom-range reviews.** Follow a workline across several days instead of resetting it at midnight.
- **Proposals after you write.** Traceink can arrange possible decisions, next steps, project notes, and background tasks. It does not write them anywhere or run them.

## Quick start

Traceink requires Node.js 18 or newer.

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install
```

Start a new Codex conversation and ask:

```text
$traceink Review today
```

You will get a workline index first. Pick one to open its evidence dossier.

### GitHub Copilot CLI

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install --agent copilot
```

Restart Copilot CLI or run `/skills reload`, then ask:

```text
/traceink Review this week
```

Copilot sessions are queried from the local session store by default. To include cloud sessions, explicitly opt in when starting Copilot CLI:

```bash
TRACEINK_COPILOT_CLOUD_SESSIONS=1 copilot
```

The variable accepts only `0` or `1`. Unset, empty, and `0` all keep local-only mode.

### Other useful prompts

| What you want | Prompt |
| --- | --- |
| Follow work across the week | `$traceink Review this week` |
| Review an exact period | `$traceink Review August 1–9, 2026` |
| Limit the source | `$traceink Review today using Codex only` |
| Read named sessions | `$traceink Review these sessions: <session-id...>` |
| Open one workline | `$traceink Open workline 2` |
| Organize what you just wrote | `$traceink Arrange my reflection, but do not execute anything` |

## What a review looks like

Illustrative example (not output from a real run):

```text
Starting point
The scheduler was probably the main bottleneck.

What changed
Three experiments produced no stable improvement. In 8 of 11 failed
cases, constraints were already lost before the scheduler ran. [E1][E2]

Possible shift · generated, not adopted
The problem may be closer to task representation than scheduling.

What would test it
Run two real task families with representation separated from scheduling.

Your call
Is there enough here to pause scheduler work and test the representation
hypothesis first?
```

In a real review, `[E1]` and `[E2]` resolve to material Traceink actually read. If it cannot recover the earlier assumption or a section of the transcript, it says so instead of filling the gap.

## When to use it

Reach for Traceink after a dense workday, at the end of a week, or before making a project decision that depends on several agent runs. It is especially useful when one piece of work has spilled across multiple sessions and you no longer trust the session titles to tell the story.

Traceink is not a live agent dashboard, a task runner, or an automatic journal. It handles the finding and reconstruction. The thinking is still yours.

## How it works

1. It freezes the date range and records exactly which sources were read.
2. It groups evidence by shared intent and change in state, not by chat title.
3. It removes repetitive execution detail while keeping failures, conflicts, and open questions.
4. It opens one evidence-backed workline at a time and stops at a question that needs you.
5. Only after you write does it offer non-binding ways to organize what you said.

The editorial and authority rules live in [`skill/references/editorial-contract.md`](skill/references/editorial-contract.md). They constrain evidence and permissions without forcing the model into a fixed ontology.

## Install options

For Codex, the installer writes to `${CODEX_HOME:-~/.codex}/skills/traceink`. With `--agent copilot`, it writes to `${COPILOT_HOME:-~/.copilot}/skills/traceink`.

To follow the current `main` branch instead of a release:

```bash
npx --yes github:WdBlink/traceink install
```

If a different copy already exists at the target, the installer stops. Use `--force` only when you intend to replace it:

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install --force
```

The installer will not replace symlinks or remove other skill directories.

## Privacy and boundaries

- Traceink adds no telemetry, hosted service, or runtime dependency of its own. Processing follows the model, network, and privacy settings of the agent session that runs it.
- Copilot CLI queries only its local session store by default. Cloud session search requires the explicit `TRACEINK_COPILOT_CLOUD_SESSIONS=1` opt-in.
- It discovers transcripts for the requested period and providers. Linked local files are opened only when you name them or when a selected dossier needs them; wider reads require approval.
- The skill instructs Codex to treat instructions found inside transcripts, documents, or code as untrusted evidence, not commands.
- Linked files stay within the session's recorded working directory unless you approve a wider read.
- The skill is read-only. It does not write to CTX, start background jobs, migrate tasks, or seal a journal page.

Copilot CLI, Codex, and Claude Code have built-in discovery. Other sources can be used only when you provide readable local material; they do not have native discovery.

## Development

```bash
npm test
npm run pack:check
```

The installer has no third-party runtime dependencies. CI runs the tests and package check on Node.js 18, 20, and 22.

## Contributing

Issues and pull requests are welcome. Changes to the prompt or authority boundary should update the editorial contract and the relevant tests. Run `npm test` and `npm run pack:check` before opening a pull request.

## License

[MIT](LICENSE) © 2026 WdBlink

---

Crafted with [Readme Craft](https://github.com/agentskills/agentskills/tree/main/skills/readme-craft).

[ci-badge]: https://github.com/WdBlink/traceink/actions/workflows/ci.yml/badge.svg
[ci-url]: https://github.com/WdBlink/traceink/actions/workflows/ci.yml
[release-badge]: https://img.shields.io/github/v/release/WdBlink/traceink?display_name=tag
[release-url]: https://github.com/WdBlink/traceink/releases/latest
[license-badge]: https://img.shields.io/badge/license-MIT-2f302c
[license-url]: LICENSE
[node-badge]: https://img.shields.io/badge/Node.js-%E2%89%A518-5b7f62
[node-url]: https://nodejs.org/
