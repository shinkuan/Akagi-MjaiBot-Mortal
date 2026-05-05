import json
import sys

# Loader runs first so ``libriichi3p`` resolves to the right prebuilt
# extension regardless of import order with ``model``.
import _libriichi_loader

_libriichi_loader.load()
import model
from libriichi3p.state import PlayerState  # type: ignore[import-not-found]
from meta_show import meta_to_top_show

class Bot:
    def __init__(self):
        self.player_id: int = None
        self.model = None
        self.state: PlayerState | None = None
        # Raw mjai event JSON strings (post-conversion to libriichi3p shape)
        # since the most recent `start_game`. Used to seed a throwaway
        # speculator Bot when peeking at the post-`reach` dahai (see
        # `_peek_reach_dahai`). The replay must mirror exactly what the
        # primary `self.model` consumed, so we capture events AFTER the
        # Akagi-V3-native → libriichi3p conversion at the top of `react`.
        self.event_log: list[str] = []
        # ========== Online Server =========== #
        model.online_settings_init()
        # ==================================== #

    def react(self, events: str) -> str:
        """
        # How to implement this function

        One `start_game` event must be sent before any other events.
        Once the bot receives a `start_game` event, it will reinitialize itself and set the player_id.

        `start_game` event can be sent any time to reset the bot.
        `end_game` event can be sent to set model to None.

        :param events: JSON string of events
        :return: JSON string of action

        For more information, please refer to https://github.com/smly/mjai.app

        # 3-player adaptation note (Akagi V3)
        Akagi V3 emits native 3p mjai (length-3 `scores` / `tehais` / `deltas`
        and `kita` events). The libriichi3p-mjai bot consumes the historical
        4-player-padded shape with `nukidora` instead of `kita`. We translate
        on the way in (length-3 → length-4 with dummy seat 3, kita → nukidora)
        and on the way out (nukidora → kita).
        """
        try:
            events = json.loads(events)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"mortal3p: failed to parse events: {e}\n")
            sys.stderr.flush()
            return json.dumps({"type":"none"}, separators=(",", ":"))

        return_action = None
        for e in events:
            # ========== Akagi V3 native 3p → libriichi3p convert ========== #
            if e['type'] == 'start_kyoku':
                # Pad scores/tehais to length 4 (libriichi3p expects the
                # legacy 4p-shaped event with seat 3 as a dummy).
                e['scores'].append(0)
                e['tehais'].append(["?","?","?","?","?","?","?","?","?","?","?","?","?"])
            if e['type'] == 'kita':
                e = {
                    'type': 'nukidora',
                    'actor': e['actor'],
                    'pai': 'N',
                }
            if 'deltas' in e and isinstance(e['deltas'], list) and len(e['deltas']) == 3:
                # hora / ryukyoku deltas — pad seat 3 with 0.
                e['deltas'].append(0)
            # ============================================================== #

            if e["type"] == "start_game":
                self.player_id = e["id"]
                self.model = model.load_model(self.player_id)
                self.state = PlayerState(self.player_id)
                # Reset speculator log; capture the start_game event so a
                # speculator spawned later can be replayed from this point.
                self.event_log = [json.dumps(e, separators=(",", ":"))]
                continue
            if self.model is None or self.player_id is None:
                sys.stderr.write("mortal3p: model not loaded; ignoring event\n")
                sys.stderr.flush()
                continue
            if e["type"] == "end_game":
                self.player_id = None
                self.model = None
                self.state = None
                self.event_log = []
                continue
            event_json = json.dumps(e, separators=(",", ":"))
            return_action = self.model.react(event_json)
            # libriichi3p clears its internal log at end_kyoku and resets
            # PlayerState at start_kyoku, so any prior-kyoku events are
            # dead weight in the speculator's replay. Truncate to the
            # start_game record (seat assignments) before appending the
            # new start_kyoku so the log stays bounded across a hanchan.
            if e["type"] == "start_kyoku":
                self.event_log = self.event_log[:1]
            # Append after primary react so the speculator replay sees
            # exactly the same event sequence the primary digested.
            self.event_log.append(event_json)
            # Feed the same (post-conversion) events to a parallel
            # PlayerState — needed to resolve chi/pon/kan/hora tiles for
            # `meta.show`. Failure must never block the action.
            if self.state is not None:
                try:
                    self.state.update(event_json)
                except Exception as exc:
                    sys.stderr.write(f"mortal3p: player_state.update failed: {exc}\n")
                    sys.stderr.flush()

        if return_action is None:
            # ========== Online Server =========== #
            if model.ot_settings['online']:
                raw_data = {
                    "type":"none",
                    "meta": {
                        "online": model.is_online,
                    }
                }
                return_action = json.dumps(raw_data, separators=(",", ":"))
            else:
                return_action = json.dumps({"type":"none"}, separators=(",", ":"))
            # ==================================== #
            return return_action
        else:
            raw_data = json.loads(return_action)
            # Reach in mjai is split across two round-trips: the bot first
            # emits `{"type":"reach"}`, then on the next call (after the
            # reach echo) it emits the riichi-discard `dahai`. Majsoul's
            # UI fuses declaring + discarding into one click, so the HUD
            # needs the discard tile up front. Spawn a throwaway Bot that
            # shares the cached engine, replay our event log into it
            # (cheap — `can_act=False` skips inference), feed it the
            # reach echo, and read off the dahai it would have picked.
            # The primary `self.model` is never fed reach here, so its
            # internal `PlayerState` does not diverge if the player
            # ultimately chooses not to riichi.
            if raw_data.get("type") == "reach" and self.player_id is not None:
                # Mjai reach is always a self-action — defend against an
                # upstream bug producing a wrong-seat reach.
                reach_actor = raw_data.get("actor", self.player_id)
                if reach_actor != self.player_id:
                    sys.stderr.write(
                        f"mortal3p: reach actor {reach_actor} != player_id "
                        f"{self.player_id}; skipping speculation\n"
                    )
                    sys.stderr.flush()
                else:
                    try:
                        pai = self._peek_reach_dahai()
                        if pai is not None:
                            raw_data["pai"] = pai
                    except Exception as exc:
                        sys.stderr.write(f"mortal3p: reach peek failed: {exc}\n")
                        sys.stderr.flush()
            # ========== Online Server =========== #
            if model.ot_settings['online']:
                if "meta" in raw_data:
                    raw_data["meta"]["online"] = model.is_online
                else:
                    raw_data["meta"] = {"online": model.is_online}
            # ==================================== #
            # Top-3 from q_values + mask_bits → meta.show. Compute
            # before the nukidora→kita rename so the meta survives the
            # conversion path.
            meta = raw_data.get("meta")
            if meta and "q_values" in meta and "mask_bits" in meta and self.state is not None:
                try:
                    speculated_pai = raw_data.get("pai") if raw_data.get("type") == "reach" else None
                    show = meta_to_top_show(
                        meta,
                        self.state,
                        is_3p=True,
                        speculated_pai=speculated_pai,
                    )
                    if show.get("items"):
                        meta["show"] = show
                except Exception as exc:
                    sys.stderr.write(f"mortal3p: meta_to_top_show failed: {exc}\n")
                    sys.stderr.flush()

            # ========== libriichi3p → Akagi V3 native 3p convert ========== #
            if raw_data.get('type') == 'nukidora':
                converted = {'type': 'kita', 'actor': raw_data['actor']}
                if 'meta' in raw_data:
                    converted['meta'] = raw_data['meta']
                raw_data = converted
            # ============================================================== #
            return json.dumps(raw_data, separators=(",", ":"))

    def _peek_reach_dahai(self) -> str | None:
        """Replay the current event log into a fresh `Bot` and ask it for
        the dahai it would pick after the reach echo. Returns the mjai
        tile string, or None if the speculator did not produce a usable
        dahai (e.g. it disagreed with the reach decision under inference
        non-determinism, or the engine returned an unexpected action).
        """
        if self.player_id is None:
            return None
        spec = model.make_speculator(self.player_id)
        for ev in self.event_log:
            spec.react(ev, can_act=False)
        peek_event = json.dumps(
            {"type": "reach", "actor": self.player_id},
            separators=(",", ":"),
        )
        peek = spec.react(peek_event, can_act=True)
        if not peek:
            return None
        dahai = json.loads(peek)
        if dahai.get("type") != "dahai":
            sys.stderr.write(
                f"mortal3p: reach peek expected dahai, got {dahai.get('type')!r}\n"
            )
            sys.stderr.flush()
            return None
        return dahai.get("pai")


def main() -> None:
    bot = Bot()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            resp = bot.react(line)
        except Exception as e:  # never crash the loop
            sys.stderr.write(f"mortal3p error: {e}\n")
            sys.stderr.flush()
            resp = json.dumps({"type": "none"}, separators=(",", ":"))
        sys.stdout.write(resp + "\n")
        sys.stdout.flush()
        try:
            evs = json.loads(line)
        except Exception:
            continue
        if any(ev.get("type") == "end_game" for ev in evs):
            break


if __name__ == "__main__":
    main()
