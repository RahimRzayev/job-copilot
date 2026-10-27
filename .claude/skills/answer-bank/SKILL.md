---
name: answer-bank
description: View, add, edit or delete the user's stored application answers (Q&A memory). Use when the user wants to review what the system knows, pre-fill common questions, or correct an answer.
---

# Answer bank

All commands print JSON. Run from the project root.

- List: `uv run jobhunt qa list [--tag skill]`
- Find: `uv run jobhunt qa lookup "<question>"`
- Save/overwrite: `uv run jobhunt qa save "<question>" "<answer>" --type text|yesno|number|choice --tags a,b [--company X]`
- Delete: `uv run jobhunt qa delete <id>`

Never store passwords here. Site password rules (tag `password-rule`, e.g. "--max 12") are fine: they are rules,
not secrets.

## Seeding session
When the user asks to seed or warm up the bank, first `uv run jobhunt settings` (country, currency, salary_monthly,
languages) and read `profile/profile.yaml`; no profile → tell the user to run /setup first and stop. Then go through
`setup/starter-questions.yaml` exactly as step 7 of `/setup` does: fill `{country}` / `{city}` / `{currency}`, skip
questions already answered, ask the rest (AskUserQuestion for ones with options, max 4 per round), save each.
Add "years of experience per main skill" for the profile's top skills.

Show a compact table of the result at the end.
