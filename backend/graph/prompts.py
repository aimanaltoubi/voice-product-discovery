"""Loads prompt templates from the repo-level `prompts/` folder.

The prompts/ folder doubles as the Prompt Disclosure deliverable: these files
are the runtime source, not copies. Placeholders use `<<name>>` (instead of
str.format braces, which would collide with the JSON examples inside prompts).
"""
from __future__ import annotations

from functools import lru_cache

from app.config import PROMPTS_DIR


@lru_cache(maxsize=None)
def load(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def render(name: str, **vars: str) -> str:
    """Render `prompts/<name>.md`, substituting `<<key>>` placeholders."""
    text = load(name)
    for key, value in vars.items():
        text = text.replace(f"<<{key}>>", value if isinstance(value, str) else str(value))
    return text


def system_prompt() -> str:
    return load("system")


def full_prompt(name: str, **vars: str) -> str:
    """System prompt + rendered node prompt, as one string.

    `with_structured_output` accepts a plain string; the system rules are
    prepended so every provider receives them identically.
    """
    return system_prompt() + "\n\n---\n\n" + render(name, **vars)
