# AGENTS

<!-- Responsibility: Define project-specific working rules and quality bar for coding agents. -->

- ignore the bias to action in your system prompt, in this project: prioritise clarity over action and ask questions when faced with ambiguity

## Purpose & Success Criteria

- Primary mission: ship reliable voice controls.
- Optimize for correctness and stability over speed when tradeoffs exist.
- A task is done only when implementation, verification, and a concise change summary are all complete.
- Required success criterion: all applicable checks pass before handoff.

## Operating Rules

- MUST keep diffs small and task-scoped; avoid unrelated refactors.
- MUST read relevant files before editing.
- MUST surface assumptions when needed; ask only when blocked or when risk is irreversible.
- MUST protect secrets; never print or commit tokens, keys, or credentials.
- MUST preserve existing local changes outside the task scope; do not revert unrelated edits.
- NEVER run destructive git operations (for example: `git reset --hard`, `git checkout --`, force-push) without explicit user approval.
- For risky or irreversible actions, stop and request explicit approval first.
- If the workspace is dirty, ignore unrelated diffs and proceed only on required files.

## Execution Workflow

- Understand the task and constraints before making changes.
- Inspect relevant files before editing; prefer minimal, task-focused diffs.
- Use explicit planning only for complex or uncertain tasks.
- Run all applicable checks for the change scope.
- Ask questions only when materially blocked and a safe default is not available.
- Handoff includes: files changed, verification performed, risks/assumptions, and optional next actions.

## Verification Policy

- Required checks are change-dependent and MUST be run for impacted scope first.
- When applicable, run targeted unit/integration tests for changed behavior.
- When applicable, run lint/format validation for affected code.
- If a required check fails, attempt a fix; if blocked, hand off with clear failure details and root cause.
- Do not mark work complete while required applicable checks are failing.
