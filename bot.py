import json
import sys

# Loader runs first so ``libriichi`` resolves to the right prebuilt
# extension regardless of import order with ``model``.
import _libriichi_loader

_libriichi_loader.load()
import model
from libriichi.state import PlayerState  # type: ignore[import-not-found]
from meta_show import meta_to_top_show


def _sanitize(s: str) -> str:
    """Replace lone surrogates in a JSON string so json.loads won't choke."""
    return s.encode('utf-8', 'surrogatepass').decode('utf-8', 'replace')

class Bot:
    def __init__(self):
        self.player_id: int = None
        self.model = None
        self.state: PlayerState | None = None
        # Raw mjai event JSON strings since the most recent `start_game`,
        # used to seed a throwaway speculator Bot when peeking at the
        # post-`reach` dahai (see `_peek_reach_dahai`).
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

        Example:
        ```
        bot = Bot()
        res = bot.react('[{"type":"start_game","names":["0","1","2","3"],"id":0}]')
        # res == '{"type":"none"}'

        events = str([
            {
                "type":"start_kyoku",
                "bakaze":"S",
                "dora_marker":"1p",
                "kyoku":2,"honba":2,
                "kyotaku":0,
                "oya":1,
                "scores":[800,61100,11300,26800],
                "tehais":[
                    ["4p","4s","P","3p","1p","5s","2m","F","1m","7s","9m","6m","9s"],
                    ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
                    ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
                    ["?","?","?","?","?","?","?","?","?","?","?","?","?"]
                ]
            },
            {"type":"tsumo","actor":1,"pai":"?"},
            {"type":"dahai","actor":1,"pai":"F","tsumogiri":false},
            {"type":"tsumo","actor":2,"pai":"?"},
            {"type":"dahai","actor":2,"pai":"3m","tsumogiri":true},
            {"type":"tsumo","actor":3,"pai":"?"},
            {"type":"dahai","actor":3,"pai":"1m","tsumogiri":true},
            {"type":"tsumo","actor":0,"pai":"3s"}
        ])

        res = bot.react(events)
        # res == '{"type":"dahai","pai":"3s","actor":0,"tsumogiri":true}'
        ...        
        res = bot.react('[{"type":"start_game","names":["0","1","2","3"],"id":3}]')
        # res == '{"type":"none"}'
        ...
        ```

        For more information, please refer to https://github.com/smly/mjai.app
        """
        try:
            events = json.loads(_sanitize(events))
        except json.JSONDecodeError as e:
            return json.dumps({"type":"none"}, separators=(",", ":"))

        return_action = None
        for e in events:
            if e["type"] == "start_game":
                self.player_id = e["id"]
                self.model = model.load_model(self.player_id)
                self.state = PlayerState(self.player_id)
                # Sanitize names before storing so the speculator replay
                # never sees lone surrogates when it replays event_log.
                if "names" in e:
                    e["names"] = [
                        _sanitize(n) if isinstance(n, str) else n
                        for n in e["names"]
                    ]
                # Reset speculator log; capture the start_game event so a
                # speculator spawned later can be replayed from this point.
                self.event_log = [json.dumps(e, separators=(",", ":"))]
                continue
            if self.model is None or self.player_id is None:
                continue
            if e["type"] == "end_game":
                self.player_id = None
                self.model = None
                self.state = None
                self.event_log = []
                return_action = json.dumps({"type":"none"}, separators=(",", ":"))
                continue
            event_json = json.dumps(e, separators=(",", ":"))
            return_action = self.model.react(event_json)
            # libriichi clears its internal log at end_kyoku and resets
            # PlayerState at start_kyoku, so any prior-kyoku events are
            # dead weight in the speculator's replay. Truncate to the
            # start_game record (seat assignments) before appending the
            # new start_kyoku so the log stays bounded across a hanchan.
            if e["type"] == "start_kyoku":
                self.event_log = self.event_log[:1]
            # Append BEFORE the reach peek so the speculator replay
            # includes the tsumo that triggered the reach decision.
            # (Previously appended after, meaning the triggering tsumo
            # was missing from the log and the speculator couldn't pick
            # a dahai — resulting in pai=None and autoplay stalling.)
            self.event_log.append(event_json)
            # Mirror the bot's view of the game on a parallel PlayerState
            # so we can resolve chi/pon/kan/hora tiles for `meta.show`.
            # Failures here must never poison the action returned to the
            # host — log + continue.
            if self.state is not None:
                try:
                    self.state.update(event_json)
                except Exception as exc:
                    sys.stderr.write(f"player_state.update failed: {exc}\n")
                    sys.stderr.flush()

        if return_action is None:
            # ========== Online Server =========== #
            if model.ot_settings['online']:
                raw_data = {
                    "type":"none",
                    "meta": {
                        "online": model.is_online
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
                        f"reach actor {reach_actor} != player_id {self.player_id}; "
                        "skipping speculation\n"
                    )
                    sys.stderr.flush()
                else:
                    try:
                        pai = self._peek_reach_dahai()
                        if pai is not None:
                            raw_data["pai"] = pai
                    except Exception as exc:
                        sys.stderr.write(f"reach peek failed: {exc}\n")
                        sys.stderr.flush()
            # ========== Online Server =========== #
            if model.ot_settings['online']:
                if "meta" in raw_data:
                    raw_data["meta"]["online"] = model.is_online
                else:
                    raw_data["meta"] = {"online": model.is_online}
            # ==================================== #
            # Top-3 from q_values + mask_bits → meta.show. Skipped when
            # the bot didn't emit q_values (e.g. degenerate `none`).
            meta = raw_data.get("meta")
            if meta and "q_values" in meta and "mask_bits" in meta and self.state is not None:
                try:
                    speculated_pai = raw_data.get("pai") if raw_data.get("type") == "reach" else None
                    show = meta_to_top_show(
                        meta,
                        self.state,
                        is_3p=False,
                        speculated_pai=speculated_pai,
                    )
                    if show.get("items"):
                        meta["show"] = show
                except Exception as exc:
                    sys.stderr.write(f"meta_to_top_show failed: {exc}\n")
                    sys.stderr.flush()
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
                f"reach peek expected dahai, got {dahai.get('type')!r}\n"
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
            sys.stderr.write(f"bot error: {e}\n")
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
