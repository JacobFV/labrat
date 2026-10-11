"""Approximate iOS portrait keyboard layout in screen-normalized (u,v) coordinates, used by
``/api/type`` to turn text into taps.  Tuned for 6.1" iPhones (393x852 pt); good enough for
letters, digits, space, return and common punctuation.  Characters it cannot type are reported
as skipped.
"""
from __future__ import annotations

ROW_V = (0.705, 0.767, 0.829, 0.893)   # key-row centres (v) incl. bottom row
KEY_W = 0.1                             # one letter key = 1/10 of the screen width

LETTER_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
NUM_ROWS = ("1234567890", "-/:;()$&@\"", ".,?!'")
SHIFT = (0.065, ROW_V[2])
MODE = (0.065, ROW_V[3])          # "123" / "ABC"
SPACE = (0.5, ROW_V[3])
RETURN = (0.875, ROW_V[3])


def _row_keys(row: str, ri: int, layout: str) -> dict[str, tuple[float, float]]:
    out = {}
    if layout == "num" and ri == 2:
        w, x0 = 0.14, 0.5 - 0.14 * len(row) / 2
    else:
        w, x0 = KEY_W, (1.0 - KEY_W * len(row)) / 2
    for i, ch in enumerate(row):
        out[ch] = (x0 + w * (i + 0.5), ROW_V[ri])
    return out


LETTERS: dict[str, tuple[float, float]] = {}
for _i, _r in enumerate(LETTER_ROWS):
    LETTERS.update(_row_keys(_r, _i, "abc"))
NUMS: dict[str, tuple[float, float]] = {}
for _i, _r in enumerate(NUM_ROWS):
    NUMS.update(_row_keys(_r, _i, "num"))


def plan_taps(text: str) -> tuple[list[tuple[float, float]], str]:
    """Return (list of (u,v) taps, skipped characters).  Assumes the keyboard starts in the
    lowercase letter layout and leaves it there; iOS auto-capitalisation is ignored (it may
    turn the first letter upper case)."""
    taps: list[tuple[float, float]] = []
    skipped = ""
    mode = "abc"
    for ch in text:
        if ch == " ":
            taps.append(SPACE)
            continue
        if ch == "\n":
            taps.append(RETURN)
            continue
        low = ch.lower()
        if low in LETTERS:
            if mode != "abc":
                taps.append(MODE)
                mode = "abc"
            if ch != low:
                taps.append(SHIFT)
            taps.append(LETTERS[low])
        elif ch in NUMS:
            if mode != "num":
                taps.append(MODE)
                mode = "num"
            taps.append(NUMS[ch])
        else:
            skipped += ch
    if mode != "abc":
        taps.append(MODE)
    return taps, skipped
