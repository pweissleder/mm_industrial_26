"""Deterministic first layer: normalise the input, then block high-confidence attacks.

The patterns only target unmistakable injection and exfiltration phrasing, so building
questions (including answer-format hints like "Finish with a line 'Answer: <number>'")
never match; tests/guardrails run every benchmark question through them.
"""

import re
import unicodedata

from app.guardrails.verdict import Category, Verdict

_SPACES = re.compile(r"[^\S\n]+")


def normalize(text: str) -> str:
    """NFKC-fold and drop invisible characters so obfuscated text matches the rules.

    Fullwidth letters become ASCII, and zero-width / bidi-override (category Cf) and
    control characters (Cc, except newlines and tabs) are removed.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cf", "Cc", "Co", "Cs")
    )
    return "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n")).strip()


def _rule(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# (category, reason, pattern); the first match wins.
_RULES: list[tuple[Category, str, re.Pattern[str]]] = [
    (
        "prompt_injection",
        "Tries to override the assistant's instructions.",
        _rule(
            r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,40}"
            r"\b(previous|above|prior|earlier|all|any|your|the|these|those|system|initial)\b"
            r"[^.\n]{0,30}\b(instructions?|rules|prompts?|guidelines|directives|constraints)\b"
        ),
    ),
    (
        "prompt_injection",
        "Tries to extract the assistant's hidden instructions.",
        _rule(
            r"\b(reveal|print|show|repeat|output|leak|display|dump|tell me|what (is|are|were))\b"
            r"[^.\n]{0,30}\b((system|hidden|initial|original|developer|secret)\s+"
            r"(prompt|message|instructions?)|your\s+(prompt|instructions|guidelines|rules))\b"
        ),
    ),
    (
        "prompt_injection",
        "Tries to switch the assistant into another persona or unrestricted mode.",
        _rule(
            r"\byou are now\b|\bdeveloper mode\b|\bjailbr(eak|oken)\b|\bgod mode\b"
            r"|\b(without|no) (restrictions|filters|guardrails|limitations)\b"
            r"|\bpretend (to be|you are|you're)\b"
            r"|\bact as (an? )?(unrestricted|unfiltered|uncensored|evil|different)\b"
        ),
    ),
    ("prompt_injection", "Uses the 'DAN' jailbreak.", _rule(r"\bDAN\b", 0)),
    (
        "prompt_injection",
        "Contains chat-template or role markers.",
        _rule(
            r"<\|\s*(im_start|im_end|system|user|assistant|endoftext)\s*\|>|\[/?INST\]"
            r"|<\s*/?\s*(system|instructions?|sys)\s*>|^\s*#{0,3}\s*(system|assistant)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "unsafe",
        "Asks for credentials or secrets.",
        _rule(
            r"\b(show|give|print|leak|send|tell|reveal|dump|list|display|read|cat|output|share"
            r"|what(?:'s| is| are))\b[^.\n]{0,40}"
            r"(\bapi[ _-]?keys?\b|\.env\b|\bpasswords?\b|\bcredentials?\b|\bsecret[ _-]?keys?\b"
            r"|\baccess[ _-]?tokens?\b|\bprivate[ _-]?keys?\b|\benvironment variables?\b)"
        ),
    ),
    (
        "unsafe",
        "Contains shell, SQL or script payloads.",
        _rule(
            r"\brm\s+-rf\b|\bsudo\b|/etc/passwd|\bdrop\s+table\b|<\s*script\b|\bos\.system\b"
            r"|\bsubprocess\.|\beval\s*\(|\bexec\s*\(|;\s*--\s*$|\bunion\s+select\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
]


def check_rules(text: str) -> Verdict | None:
    """Return a blocking verdict for normalised `text`, or None if no rule matches."""
    for category, reason, pattern in _RULES:
        if pattern.search(text):
            return Verdict(category=category, reason=reason, source="rules")
    return None
