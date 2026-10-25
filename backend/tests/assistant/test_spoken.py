"""OFFLINE: `spoken()` / `speakable()` turn screen text into text the TTS voice reads well."""

import pytest

from app.assistant.voice import speakable, spoken


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Walk about 21 m through the door.", "Walk about 21 metres through the door."),
        ("21m", "21 metres"),
        ("1 m", "1 metre"),
        ("0.9 m ahead", "0.9 metres ahead"),
        ("1.0 m", "1.0 metres"),
        ("A 12 m² room.", "A 12 square metres room."),
        ("12 m2.", "12 square metres."),
        ("1 m²", "1 square metre"),
        ("3 cm", "3 centimetres"),
        ("2.5mm", "2.5 millimetres"),
        ("2 km", "2 kilometres"),
        ("~21 m", "about 21 metres"),
        ("≈ 5 m", "about 5 metres"),
        ("ca. 10 m", "about 10 metres"),
        ("Turn 90°", "Turn 90 degrees"),
        ("1° off", "1 degree off"),
        ("50% done", "50 percent done"),
        ("21 m · 2 doors • stairs | lift", "21 metres, 2 doors, stairs, lift"),
        ("| A |", "A"),
        ("21 m · stairs · .", "21 metres, stairs."),
        ("Walk 2 m |.", "Walk 2 metres."),
        ("Office 0.38 -> Kitchen 0.28", "Office 0.38 to Kitchen 0.28"),
        ("Floor 0 → Floor 1", "Floor 0 to Floor 1"),
        ("2× speed", "2 times speed"),
        ("2x faster", "2 times faster"),
        ("3 x 4 m", "3 by 4 metres"),
    ],
)
def test_units_and_symbols_are_spelled_out(text: str, expected: str) -> None:
    assert speakable(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Office 0.38",
        "Go to Office 1.04, then Kitchen 0.28.",
        "Main aisle H-A1-A",
        "proc-factory-dev-003-F0-S038",
        "The room is 5 minutes away.",
        "about 5 min",
        "Room B2 m",
        "m",
        "Go to the meeting room.",
        "Box x is here.",
    ],
)
def test_labels_ids_and_words_stay(text: str) -> None:
    assert speakable(text) == text


def test_spoken_strips_markdown_and_expands_units() -> None:
    assert spoken("**Walk about 16 m**, then turn left into `Kitchen 0.28`.") == (
        "Walk about 16 metres, then turn left into Kitchen 0.28."
    )


def test_spoken_still_shortens_at_a_sentence_boundary() -> None:
    text = "Walk about 2 m. " * 80
    out = spoken(text, limit=300)
    assert len(out) <= 300 and out.endswith("metres.")
