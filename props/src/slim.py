"""Slim an offers payload to the fields the pipeline consumes.

The raw BettingPros offers payload is ~70% marketing weight (deep links,
page meta, promo blocks). The committed archive stores this whitelist
instead; the payload keeps its {"offers": [...]} shape so build_props
parses raw and slim files identically (parity asserted on a dual capture
before the mass backfill — PLAN.md D1).

Whitelist rationale (what consumes each field):
  event_id/player_id/participants.player   build_props.parse_offer_file
  selections.selection/label/participant   parse_offer_file + parse_game_file
  selections.opening_line (whole dict)     open anchor incl. book_id/created
  books[].id + lines[] pricing fields      close quotes per book
"""

LINE_KEYS = ("line", "cost", "updated", "main", "best", "active", "is_off")
PLAYER_KEYS = ("first_name", "last_name", "team", "position")


def slim_payload(d):
    offers = []
    for o in d.get("offers") or []:
        parts = []
        for p in o.get("participants") or []:
            pl = p.get("player")
            parts.append({
                "id": p.get("id"), "name": p.get("name"),
                "player": ({k: pl.get(k) for k in PLAYER_KEYS}
                           if isinstance(pl, dict) else None),
            })
        sels = []
        for s in o.get("selections") or []:
            sels.append({
                "selection": s.get("selection"),
                "label": s.get("label"),
                "participant": s.get("participant"),
                "opening_line": s.get("opening_line"),
                "books": [
                    {"id": b.get("id"),
                     "lines": [{k: ln.get(k) for k in LINE_KEYS}
                               for ln in b.get("lines") or []]}
                    for b in s.get("books") or []
                ],
            })
        offers.append({
            "id": o.get("id"), "event_id": o.get("event_id"),
            "player_id": o.get("player_id"),
            "participants": parts, "selections": sels,
        })
    return {"offers": offers}
