"""Prompt versions used in production. The eval runner imports these too, so evals always test what ships.
Bump the version (new prompts/<name>_vN.md) instead of editing a prompt in place: audit rows record it."""

EXTRACT = "extract_v2"
INTERPRET = "interpret_v1"
WELCOME = "welcome_v2"
JUDGE = "judge_v1"
HANDOFF = "handoff_v1"
