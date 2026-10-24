---
name: traceink
description: Reconstruct evidence-linked worklines from GitHub Copilot CLI, Codex, Claude Code, and other agent sessions so the user can review a day, week, or custom period and write their own reflection.
---

# Traceink for GitHub Copilot CLI

Before doing any Traceink work:

1. Read `base/SKILL.md` completely and follow it as the primary workflow.
2. Run `node scripts/session-source.mjs` from this skill directory.
3. Apply the returned Copilot session policy in addition to the base workflow.

For GitHub Copilot CLI sessions:

- Use the built-in session store query tool when it is available.
- When the policy is `local`, every session store query must explicitly set `source` to `local`. Do not query or supplement results from the cloud store.
- When the policy is `cloud`, cloud session queries are allowed and must remain within the frozen review period.
- Always apply a time filter, select only needed columns, include a `LIMIT`, and follow the session store tool's dialect and performance rules.
- If the session store query tool is unavailable, report that Copilot sessions were skipped. Do not inspect undocumented Copilot state files or databases as a fallback.

The base skill's evidence, authority, read-only, coverage, and reflection rules remain unchanged.
