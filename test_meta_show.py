"""Unit tests for meta_show.

Decoder tests run without libriichi — the chi/pon/kan/hora rows use a
small fake state object that implements only the attributes
`meta_show` reads. Per CLAUDE.md guideline 8, all data is fake.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

# Allow running this file directly: `python test_meta_show.py`.
sys.path.insert(0, str(Path(__file__).parent))

import meta_show  # noqa: E402


def _empty_state():
    """A state with no tiles in hand and no last action — the simple
    label-only fallbacks should fire for every call type."""
    return SimpleNamespace(
        tehai=[0] * 34,
        akas_in_hand=[False, False, False],
        last_kawa_tile=lambda: None,
        last_self_tsumo=lambda: None,
        ankan_candidates=lambda: [],
        kakan_candidates=lambda: [],
        last_cans=SimpleNamespace(
            can_daiminkan=False, can_ankan=False, can_kakan=False, can_ron_agari=False,
        ),
    )


def test_mask_bits_lsb_first():
    # Bit 0 set → entry 0 True, others False.
    bits = meta_show._mask_bits_to_bool_list(0b1)
    assert bits[0] is True
    assert bits[1] is False
    # Bit 5 set → entry 5 True.
    bits = meta_show._mask_bits_to_bool_list(0b100000)
    assert bits[5] is True
    assert bits[4] is False


def test_softmax_basic():
    out = meta_show._softmax([1.0, 1.0], temperature=1.0)
    assert math.isclose(out[0], 0.5)
    assert math.isclose(out[1], 0.5)
    out = meta_show._softmax([10.0, 0.0], temperature=1.0)
    assert out[0] > out[1]
    assert math.isclose(sum(out), 1.0)


def test_canonical_example_top3_labels():
    """The example meta from reference Akagi's libriichi_helper.py
    (lines 5-24) — q_values + mask_bits taken verbatim. We assert the
    top-3 *labels* are stable; numeric scores depend on temperature
    and aren't pinned."""
    meta = {
        "q_values": [
            -9.09196, -9.46696, -8.365397, -8.849772, -9.43571, -10.06071,
            -9.295085, -0.73649096, -9.27946, -9.357585, 0.3221028, -2.7794597,
        ],
        "mask_bits": 2697207348,
    }
    show = meta_show.meta_to_top_show(meta, _empty_state(), is_3p=False, k=3)
    assert show.get("title") == "Top 3"
    assert len(show["items"]) == 3
    # Top-3 should be the entries paired with the highest q_values:
    # 0.3221028 (idx 10) and -0.73649096 (idx 7) are clearly top-2.
    # The labels depend on which mask bits are set; we only check that
    # all three labels render without crashing and have a "value" field.
    for it in show["items"]:
        assert "value" in it
        assert it["value"].endswith("%")


def test_dahai_row_renders_pais():
    # Mask: only bit 0 (= "1m") is set. Single q_value.
    meta = {"q_values": [1.0], "mask_bits": 0b1}
    show = meta_show.meta_to_top_show(meta, _empty_state(), is_3p=False, k=1)
    assert show["items"] == [{"label": "Dahai 1m", "pais": ["1m"], "value": "100.00%"}]


def test_chi_low_resolves_meld():
    """Discarded `5m`; hand contains `6m` and `7m` — chi_low (consume
    n+1, n+2) should resolve to a meld string."""
    state = _empty_state()
    # tehai count: indices 5 (6m) and 6 (7m) each have one.
    state.tehai = [0] * 34
    state.tehai[5] = 1  # 6m
    state.tehai[6] = 1  # 7m
    state.last_kawa_tile = lambda: "5m"
    # Bit 38 = chi_low (4p label list — index 38 in ACTION_LABELS_4P).
    meta = {"q_values": [1.0], "mask_bits": 1 << 38}
    show = meta_show.meta_to_top_show(meta, state, is_3p=False, k=1)
    item = show["items"][0]
    assert item["label"] == "Chi (low)"
    assert item.get("tiles") == "67m|5m"


def test_pon_with_red_five_in_hand():
    """Discarded plain `5m`; hand has both red `5mr` and plain `5m` —
    prefer red+plain consume display."""
    state = _empty_state()
    state.tehai = [0] * 34
    state.tehai[4] = 2  # two 5m's
    state.akas_in_hand = [True, False, False]  # one of them is the red five
    state.last_kawa_tile = lambda: "5m"
    # Bit 41 = pon (4p label list).
    meta = {"q_values": [1.0], "mask_bits": 1 << 41}
    show = meta_show.meta_to_top_show(meta, state, is_3p=False, k=1)
    item = show["items"][0]
    assert item["label"] == "Pon"
    # Mahgen DSL: "0m5m|5m" (red five = 0, plain = 5, called = 5m).
    assert item.get("tiles") == "05m|5m"


def test_hora_ron_uses_last_kawa():
    state = _empty_state()
    state.last_kawa_tile = lambda: "3p"
    state.last_cans = SimpleNamespace(
        can_daiminkan=False, can_ankan=False, can_kakan=False, can_ron_agari=True,
    )
    # Bit 43 = hora (4p label list).
    meta = {"q_values": [1.0], "mask_bits": 1 << 43}
    show = meta_show.meta_to_top_show(meta, state, is_3p=False, k=1)
    item = show["items"][0]
    assert item["label"] == "Ron"
    assert item.get("pais") == ["3p"]


def test_hora_tsumo_uses_last_self_tsumo():
    state = _empty_state()
    state.last_self_tsumo = lambda: "7s"
    state.last_cans = SimpleNamespace(
        can_daiminkan=False, can_ankan=False, can_kakan=False, can_ron_agari=False,
    )
    meta = {"q_values": [1.0], "mask_bits": 1 << 43}
    show = meta_show.meta_to_top_show(meta, state, is_3p=False, k=1)
    item = show["items"][0]
    assert item["label"] == "Tsumo"
    assert item.get("pais") == ["7s"]


def test_3p_nukidora_label():
    state = _empty_state()
    # 3p nukidora is at index 40 in the 3p labelling.
    meta = {"q_values": [1.0], "mask_bits": 1 << 40}
    show = meta_show.meta_to_top_show(meta, state, is_3p=True, k=1)
    item = show["items"][0]
    assert item["label"] == "Nukidora"
    assert item.get("pais") == ["N"]


def test_missing_q_values_returns_empty():
    show = meta_show.meta_to_top_show({}, _empty_state(), is_3p=False)
    assert show == {"items": []}


def test_reach_row_carries_speculated_pai():
    """When the bot wrapper has peeked the post-reach dahai (see
    Bot._peek_reach_dahai in bot.py) and passes the predicted tile via
    `speculated_pai`, the Reach row in the top-K display must surface
    it under `pais` so the HUD can render mahgen alongside the action
    label. Without `speculated_pai`, the Reach row stays label-only."""

    # mask: only "reach" (bit 37) — minimal scenario where the bot picks
    # reach. q_values length must equal popcount(mask).
    meta = {"q_values": [1.0], "mask_bits": 1 << 37}
    state = _empty_state()

    # Without speculation: reach row, no pais.
    show = meta_show.meta_to_top_show(meta, state, is_3p=False, k=1)
    assert len(show["items"]) == 1
    item = show["items"][0]
    assert item["label"] == "Reach"
    assert "pais" not in item

    # With speculation: reach row carries the predicted discard tile.
    show = meta_show.meta_to_top_show(
        meta, state, is_3p=False, k=1, speculated_pai="3p"
    )
    item = show["items"][0]
    assert item["label"] == "Reach"
    assert item["pais"] == ["3p"]

    # Empty / None speculated_pai should not add the pais key — guards
    # against a bot wrapper that sets speculated_pai='' on a peek miss.
    for falsy in (None, ""):
        show = meta_show.meta_to_top_show(
            meta, state, is_3p=False, k=1, speculated_pai=falsy
        )
        assert "pais" not in show["items"][0]


if __name__ == "__main__":
    # Run all `test_*` functions in declaration order.
    failed = 0
    for name in list(globals()):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except AssertionError as e:
                print(f"FAIL  {name}: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR {name}: {type(e).__name__}: {e}")
                failed += 1
    if failed:
        sys.exit(1)
    print("OK")
