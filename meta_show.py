"""Decode Mortal's mjai meta (q_values + mask_bits) into a `meta.show`
payload — a structured top-K list rendered by the AkagiV3 frontend's
BotShowTile.

Mirrors `reference/Akagi/akagi/libriichi_helper.py:meta_to_recommend`
and the `Recommendation.update_recommendation` widget logic from
`reference/Akagi/akagi/akagi.py`. State-aware tile resolution
(chi/pon/kan/hora) reads from a `libriichi.state.PlayerState` fed in
parallel with the bot.
"""
from __future__ import annotations

import math
from typing import Any

# Action label list: index = bit position in mask_bits.
# 0..33  tiles (1m..9m, 1p..9p, 1s..9s, E S W N P F C)
# 34..36 red fives (5mr 5pr 5sr)
# 37..45 calls (reach, chi_low, chi_mid, chi_high, pon, kan_select, hora, ryukyoku, none)
ACTION_LABELS_4P: list[str] = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
    "5mr", "5pr", "5sr",
    "reach", "chi_low", "chi_mid", "chi_high", "pon", "kan_select",
    "hora", "ryukyoku", "none",
]

# Tile labels (no calls) — used when expanding state.tehai into mjai strings.
_PAI_STR = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
]
_AKA_STR = ["5mr", "5pr", "5sr"]

_DEFAULT_TEMPERATURE = 0.3


def _mask_bits_to_bool_list(mask_bits: int, n: int = 46) -> list[bool]:
    """Bit i of `mask_bits` (LSB-first) → entry i of the returned list.

    Mirrors `libriichi_helper.mask_bits_to_bool_list` — see the comment
    at line 50 of that file. The value 46 covers both 4p (46 actions)
    and 3p (which leaves the unused bits zero).
    """
    return [(mask_bits >> i) & 1 == 1 for i in range(n)]


def _softmax(values: list[float], temperature: float) -> list[float]:
    if not values:
        return []
    if temperature != 1.0:
        values = [v / temperature for v in values]
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    s = sum(exps)
    if s <= 0.0:
        return [0.0] * len(values)
    return [e / s for e in exps]


def _state_tehai_mjai(state: Any) -> list[str]:
    """Expand `state.tehai` (count[34]) + `state.akas_in_hand` into a
    list of mjai tile strings — including red-five suffixes. Port of
    `reference/Akagi/akagi/libriichi_helper.py:_state_to_tehai`."""
    tehai34 = state.tehai
    akas = state.akas_in_hand
    tile_list: list[str] = []
    for tile_id, count in enumerate(tehai34):
        for _ in range(count):
            tile_list.append(_PAI_STR[tile_id])
    for idx, is_aka in enumerate(akas):
        if is_aka:
            five = "5" + ("m", "p", "s")[idx]
            try:
                tile_list[tile_list.index(five)] = _AKA_STR[idx]
            except ValueError:
                # Aka counted but the corresponding 5 isn't in the
                # expanded list — should not happen in practice.
                pass
    return tile_list


def _row_for_dahai(tile: str, score: float) -> dict:
    return {
        "label": f"Dahai {tile}",
        "pais": [tile],
        "value": f"{score * 100:.2f}%",
    }


def _row_for_simple(label: str, score: float, *, pais: list[str] | None = None) -> dict:
    item: dict = {"label": label, "value": f"{score * 100:.2f}%"}
    if pais:
        item["pais"] = pais
    return item


def _row_for_chi(variant: str, state: Any, score: float) -> dict:
    """Resolve which two tiles in hand make up the chi meld for the
    given variant (chi_low/chi_mid/chi_high) and render as a mahgen
    meld string (`{a}{b}|{called}`).

    Falls back to text-only if state doesn't disambiguate (e.g. the
    discarded tile isn't a number-suit tile, or the hand has been
    altered such that no candidate matches).
    """
    label_map = {"chi_low": "Chi (low)", "chi_mid": "Chi (mid)", "chi_high": "Chi (high)"}
    label = label_map.get(variant, "Chi")
    last = state.last_kawa_tile() if callable(getattr(state, "last_kawa_tile", None)) else None
    if not last or len(last) < 2 or not last[0].isdigit():
        return {"label": label, "value": f"{score * 100:.2f}%"}
    color = last[1]
    if color not in ("m", "p", "s"):
        return {"label": label, "value": f"{score * 100:.2f}%"}
    try:
        n = int(last[0])
    except ValueError:
        return {"label": label, "value": f"{score * 100:.2f}%"}
    tehai = _state_tehai_mjai(state)
    # offsets: chi_high consumes (n-2, n-1); chi_mid consumes (n-1, n+1);
    # chi_low consumes (n+1, n+2). Walk red+plain combinations; first
    # that matches the hand wins.
    if variant == "chi_high":
        off = (n - 2, n - 1)
    elif variant == "chi_mid":
        off = (n - 1, n + 1)
    elif variant == "chi_low":
        off = (n + 1, n + 2)
    else:
        return {"label": label, "value": f"{score * 100:.2f}%"}
    candidates = (
        (f"{off[0]}{color}r", f"{off[1]}{color}"),
        (f"{off[0]}{color}",  f"{off[1]}{color}r"),
        (f"{off[0]}{color}",  f"{off[1]}{color}"),
    )
    for a, b in candidates:
        if a in tehai and b in tehai:
            # mahgen DSL: consumed tiles followed by `|called` (the pipe
            # marks the called/rotated tile in mahgen renderings).
            return {
                "label": label,
                "tiles": _mjai_pair_to_mahgen(a, b) + "|" + _mjai_to_mahgen_one(last),
                "value": f"{score * 100:.2f}%",
            }
    return {"label": label, "value": f"{score * 100:.2f}%"}


def _row_for_pon(state: Any, score: float) -> dict:
    last = state.last_kawa_tile() if callable(getattr(state, "last_kawa_tile", None)) else None
    if not last:
        return {"label": "Pon", "value": f"{score * 100:.2f}%"}
    # Pon consumes two of the same plain tile, optionally one red.
    tehai = _state_tehai_mjai(state)
    plain = last[:2]  # strip an `r` suffix if present
    consumed: list[str] = []
    if last.endswith("r"):
        # The discard itself is the red five; we need two plain copies.
        if tehai.count(plain) >= 2:
            consumed = [plain, plain]
    else:
        # Prefer red+plain (more interesting display) when available.
        if plain[0] == "5" and plain[1] in ("m", "p", "s") and (plain + "r") in tehai and plain in tehai:
            consumed = [plain + "r", plain]
        elif tehai.count(plain) >= 2:
            consumed = [plain, plain]
    if not consumed:
        return {"label": "Pon", "pais": [last], "value": f"{score * 100:.2f}%"}
    return {
        "label": "Pon",
        "tiles": _mjai_pair_to_mahgen(consumed[0], consumed[1]) + "|" + _mjai_to_mahgen_one(last),
        "value": f"{score * 100:.2f}%",
    }


def _row_for_kan(state: Any, score: float) -> dict:
    cans = getattr(state, "last_cans", None)
    last = state.last_kawa_tile() if callable(getattr(state, "last_kawa_tile", None)) else None
    if cans is not None and getattr(cans, "can_daiminkan", False) and last:
        return {
            "label": "Daiminkan",
            "tiles": _mjai_pair_to_mahgen(last, last) + _mjai_to_mahgen_one(last) + "|" + _mjai_to_mahgen_one(last),
            "value": f"{score * 100:.2f}%",
        }
    ank = list(state.ankan_candidates()) if callable(getattr(state, "ankan_candidates", None)) else []
    if cans is not None and getattr(cans, "can_ankan", False) and ank:
        item: dict = {"label": "Ankan", "pais": [ank[0]], "value": f"{score * 100:.2f}%"}
        if len(ank) > 1:
            item["note"] = f"+{len(ank) - 1} more"
        return item
    kak = list(state.kakan_candidates()) if callable(getattr(state, "kakan_candidates", None)) else []
    if cans is not None and getattr(cans, "can_kakan", False) and kak:
        item = {"label": "Kakan", "pais": [kak[0]], "value": f"{score * 100:.2f}%"}
        if len(kak) > 1:
            item["note"] = f"+{len(kak) - 1} more"
        return item
    return {"label": "Kan", "value": f"{score * 100:.2f}%"}


def _row_for_hora(state: Any, score: float) -> dict:
    cans = getattr(state, "last_cans", None)
    if cans is not None and getattr(cans, "can_ron_agari", False):
        last = state.last_kawa_tile() if callable(getattr(state, "last_kawa_tile", None)) else None
        if last:
            return {"label": "Ron", "pais": [last], "value": f"{score * 100:.2f}%"}
    last_t = state.last_self_tsumo() if callable(getattr(state, "last_self_tsumo", None)) else None
    if last_t:
        return {"label": "Tsumo", "pais": [last_t], "value": f"{score * 100:.2f}%"}
    return {"label": "Hora", "value": f"{score * 100:.2f}%"}


def _mjai_to_mahgen_one(tile: str) -> str:
    """Convert one mjai tile string to mahgen DSL.

    Honor tiles use `z` with index 1..7 (E S W N P F C). Red fives map
    to `0{m,p,s}`. Numbered tiles render as `{n}{m,p,s}`.
    """
    z = {"E": 1, "S": 2, "W": 3, "N": 4, "P": 5, "F": 6, "C": 7}
    if tile in z:
        return f"{z[tile]}z"
    if tile.endswith("r"):
        return f"0{tile[1]}"
    return tile  # already e.g. "5m"


def _mjai_pair_to_mahgen(a: str, b: str) -> str:
    """Two consumed tiles share a suit in chi melds; mahgen accepts
    `12m` for two manzu. For mixed suits we just concatenate (each
    `_mjai_to_mahgen_one` carries its own suit suffix)."""
    ma = _mjai_to_mahgen_one(a)
    mb = _mjai_to_mahgen_one(b)
    # If both end in same suit char, collapse: "12m" + "3m" → "123m".
    if len(ma) == 2 and len(mb) == 2 and ma[1] == mb[1]:
        return ma[0] + mb
    # Otherwise concat. (Mixed-suit pairs don't actually occur in chi
    # melds, but we stay defensive.)
    return ma + mb


def meta_to_top_show(
    meta: dict,
    state: Any,
    *,
    is_3p: bool = False,
    k: int = 3,
    temperature: float = _DEFAULT_TEMPERATURE,
    speculated_pai: str | None = None,
) -> dict:
    """Build the `meta.show` payload from Mortal's q_values + mask_bits.

    Returns `{"items": []}` when the meta is missing q_values/mask_bits.
    Otherwise returns `{"title", "items": [...]}` with up to `k` rows
    sorted by descending softmax-scaled score.

    When ``speculated_pai`` is given, the Reach row carries it under
    ``pais`` so the HUD can render the predicted riichi-discard tile
    next to the action label.
    """
    q_values = meta.get("q_values")
    mask_bits = meta.get("mask_bits")
    if q_values is None or mask_bits is None:
        return {"items": []}

    labels = ACTION_LABELS_4P  # 3p just leaves unused bits zero
    if is_3p:
        # 3p replaces chi_* and adds nukidora; reuse 4p indexing where
        # possible. The 3p bot's libriichi3p never sets chi bits, so we
        # only need to relabel the call section.
        labels = ACTION_LABELS_4P[:37] + [
            "reach", "pon", "kan_select", "nukidora", "hora", "ryukyoku", "none",
        ] + ["none"] * 2  # pad to length 46
    mask = _mask_bits_to_bool_list(int(mask_bits), n=len(labels))
    scaled = _softmax([float(v) for v in q_values], temperature)

    pairs: list[tuple[str, float]] = []
    qi = 0
    for i, allowed in enumerate(mask):
        if not allowed:
            continue
        if qi >= len(scaled):
            break
        pairs.append((labels[i], scaled[qi]))
        qi += 1
    pairs.sort(key=lambda p: p[1], reverse=True)
    pairs = pairs[:k]

    items: list[dict] = []
    for label, score in pairs:
        if label in ("reach",):
            row: dict = {"label": "Reach", "value": f"{score * 100:.2f}%"}
            if speculated_pai:
                row["pais"] = [speculated_pai]
            items.append(row)
        elif label in ("chi_low", "chi_mid", "chi_high"):
            items.append(_row_for_chi(label, state, score))
        elif label == "pon":
            items.append(_row_for_pon(state, score))
        elif label == "kan_select":
            items.append(_row_for_kan(state, score))
        elif label == "hora":
            items.append(_row_for_hora(state, score))
        elif label == "ryukyoku":
            items.append({"label": "Ryukyoku", "value": f"{score * 100:.2f}%"})
        elif label == "nukidora":
            items.append({"label": "Nukidora", "pais": ["N"], "value": f"{score * 100:.2f}%"})
        elif label == "none":
            items.append({"label": "Skip", "value": f"{score * 100:.2f}%"})
        else:
            # Tile labels — discard.
            items.append(_row_for_dahai(label, score))

    return {"title": "Top 3", "items": items}
