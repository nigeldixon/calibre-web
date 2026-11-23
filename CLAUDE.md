# Claude Code guidance

## Agent skills

Canonical agent skills live in `.agents/skills/`. The Claude-facing entries under `.claude/skills/` are relative symlinks to those directories so the same skill files can be shared with other agent tools.

Edit the canonical files in `.agents/skills/`. Do not create a second copy of a skill under `.claude/skills/`.

If a Claude skill symlink is missing after checkout, recreate it from the repository root:

```sh
mkdir -p .claude/skills
ln -s ../../.agents/skills/quality-workflow-review .claude/skills/quality-workflow-review
```
