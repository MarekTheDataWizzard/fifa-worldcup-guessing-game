import re
from datetime import datetime, timedelta

import requests
import streamlit as st

from odds import get_all_match_odds, maybe_fetch_final_odds
from tips import cancel_tip, get_all_tips_with_names, get_user_tips, submit_tip

_LIVE_API = "https://worldcup26.ir/get"

# Summer 2026: Europe is on CEST (UTC+2). Displayed as "CET" per user preference.
_CET_OFFSET = 2

# FIFA 2026 venue keywords → local UTC offset during summer (DST) 2026
_STADIUM_TZ: dict[str, int] = {
    # Eastern (EDT = UTC-4)
    "metlife": -4, "gillette": -4, "hard rock": -4,
    "mercedes": -4, "lincoln": -4, "bmo": -4,
    # Central US (CDT = UTC-5)
    "at&t": -5, "nrg": -5, "arrowhead": -5,
    # Mexico abolished DST in 2023 — all Mexican venues are CST (UTC-6) year-round
    "azteca": -6, "bbva": -6, "akron": -6,
    # Pacific (PDT = UTC-7)
    "sofi": -7, "levi": -7, "lumen": -7, "bc place": -7,
}

_PAGE_SIZE = 18  # matches shown before "Show more"


def _local_utc_offset(stadium_name: str) -> int:
    n = stadium_name.lower()
    for kw, off in _STADIUM_TZ.items():
        if kw in n:
            return off
    return -5  # CDT default for unrecognised venues

_PHASE_LABELS = {
    "group": "Group Stage",
    "r32":   "Round of 32",
    "r16":   "Round of 16",
    "qf":    "Quarter-finals",
    "sf":    "Semi-finals",
    "third": "3rd Place",
    "final": "Final",
}
_PHASE_ORDER = ["group", "r32", "r16", "qf", "sf", "third", "final"]

_MULTIPLIERS = {
    "group": 1,
    "r32":   1,
    "r16":   2,
    "qf":    4,
    "sf":    8,
    "third": 12,
    "final": 16,
}

_BADGE_COLORS = {
    "group": "#c17f24",
    "r32":   "#2e7d9e",
    "r16":   "#1e6b56",
    "qf":    "#5c6bc0",
    "sf":    "#7b1fa2",
    "third": "#388e3c",
    "final": "#c62828",
}

# CSS that stitches the three parts of a split card (top HTML / tip buttons / bottom HTML)
# into a visually seamless unit, with mobile-responsive stacking and scroll perf hints.
_TIP_CSS = """
<style>
/* ── Split-card gap collapse ─────────────────────────────────────────────── */
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top) {
    gap: 0 !important;
    contain: layout style paint;
}
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top)
    > .stElementContainer {
    margin: 0 !important;
    padding: 0 !important;
}
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top)
    > [data-testid="stHorizontalBlock"] {
    border-left:  1px solid rgba(128,128,128,0.18);
    border-right: 1px solid rgba(128,128,128,0.18);
    padding: 6px 22px 8px !important;
    margin: 0 !important;
    gap: 6px !important;
}
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top)
    > [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
    padding: 0 !important;
    min-width: 0 !important;
}
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top)
    > [data-testid="stHorizontalBlock"] button p:first-child {
    font-size: 1.4rem !important;
    font-weight: 800 !important;
    margin: 0 0 2px !important;
    line-height: 1 !important;
}
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top)
    > [data-testid="stHorizontalBlock"] button p:last-child {
    font-size: 0.75rem !important;
    margin: 0 !important;
    line-height: 1 !important;
}

/* ── Scroll performance hints ───────────────────────────────────────────── */
section.main .block-container {
    will-change: scroll-position;
}
</style>
"""


def _subdivision_flag(bcp47_slug: str) -> str:
    slug = bcp47_slug.replace("-", "").lower()
    return chr(0x1F3F4) + "".join(chr(0xE0000 + ord(c)) for c in slug) + chr(0xE007F)


_SUBDIVISION_FLAGS = {
    "ENG": _subdivision_flag("gb-eng"),  # 🏴󠁧󠁢󠁥󠁮󠁧󠁿
    "SCO": _subdivision_flag("gb-sct"),  # 🏴󠁧󠁢󠁳󠁣󠁴󠁿
}


def _flag(iso2: str) -> str:
    if not iso2:
        return "🏴"
    if iso2 in _SUBDIVISION_FLAGS:
        return _SUBDIVISION_FLAGS[iso2]
    if len(iso2) != 2:
        return "🏴"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())


def _count_et_goals(scorers_str: str) -> int:
    """
    Count goals scored in extra time (minute > 90) from the API scorers field.
    "90+X'" = regulation stoppage time (not ET). "92'", "111'", "125(P)'" = ET.
    """
    if not scorers_str or scorers_str.strip().lower() == "null":
        return 0
    count = 0
    for m in re.finditer(r"(\d+)(\+\d+)?(?:\([^)]*\))?\s*'", scorers_str):
        base = int(m.group(1))
        if m.group(2):          # "B+X" format → stoppage time at minute B
            if base > 90:       # "105+2'" = ET stoppage; "90+4'" = regulation
                count += 1
        else:
            if base > 90:       # plain "92'" / "111'" = ET
                count += 1
    return count


@st.cache_data(ttl=600)
def fetch_matches() -> list[dict]:
    matches_raw  = requests.get(f"{_LIVE_API}/games",    timeout=20).json()["games"]
    teams_raw    = requests.get(f"{_LIVE_API}/teams",    timeout=20).json()["teams"]
    stadiums_raw = requests.get(f"{_LIVE_API}/stadiums", timeout=20).json()["stadiums"]

    teams    = {t["id"]: t for t in teams_raw}
    stadiums = {s["id"]: s for s in stadiums_raw}

    enriched = []
    for m in matches_raw:
        dt     = datetime.strptime(m["local_date"], "%m/%d/%Y %H:%M")
        home_t = teams.get(m["home_team_id"], {})
        away_t = teams.get(m["away_team_id"], {})
        stad   = stadiums.get(m["stadium_id"], {})

        local_offset = _local_utc_offset(stad.get("name_en", ""))
        cet_dt   = dt + timedelta(hours=_CET_OFFSET - local_offset)
        utc_kick = dt - timedelta(hours=local_offset)
        # Detect penalty shootout and determine who advanced
        hps_raw = (m.get("home_penalty_score") or "").strip()
        aps_raw = (m.get("away_penalty_score") or "").strip()
        went_to_pen = hps_raw not in ("", "null")
        if went_to_pen:
            try:
                hpen, apen = int(hps_raw), int(aps_raw)
                if hpen > apen:
                    home_display_score = str(int(m["home_score"]) + 1)
                    away_display_score = m["away_score"]
                else:
                    home_display_score = m["home_score"]
                    away_display_score = str(int(m["away_score"]) + 1)
            except (TypeError, ValueError):
                home_display_score = m["home_score"]
                away_display_score = m["away_score"]
        else:
            home_display_score = m["home_score"]
            away_display_score = m["away_score"]

        # Compute 90-minute score by stripping extra-time goals from scorers
        home_scorers_raw = m.get("home_scorers") or "null"
        away_scorers_raw = m.get("away_scorers") or "null"
        home_et = _count_et_goals(home_scorers_raw)
        away_et = _count_et_goals(away_scorers_raw)
        went_to_et = (home_et + away_et) > 0
        try:
            home_score_90 = int(m["home_score"]) - home_et
            away_score_90 = int(m["away_score"]) - away_et
            if home_score_90 < 0 or away_score_90 < 0:
                raise ValueError("negative score")
        except (TypeError, ValueError):
            home_score_90 = m["home_score"]
            away_score_90 = m["away_score"]
            went_to_et    = False

        enriched.append({
            "id":            m["id"],
            "datetime":      dt,
            "date":          dt.date(),
            "time_str":      dt.strftime("%H:%M"),
            "date_str":      dt.strftime("%m/%d/%Y"),
            "cet_datetime":  cet_dt,
            "cet_date":      cet_dt.date(),
            "cet_date_str":  cet_dt.strftime("%d/%m/%Y"),
            "cet_time_str":  cet_dt.strftime("%H:%M"),
            "utc_kickoff":   utc_kick,
            "home_name":     home_t.get("name_en") or m.get("home_team_label", "TBD"),
            "home_flag":     _flag(home_t.get("iso2", "")),
            "home_flag_url": home_t.get("flag", ""),
            "away_name":     away_t.get("name_en") or m.get("away_team_label", "TBD"),
            "away_flag":     _flag(away_t.get("iso2", "")),
            "away_flag_url": away_t.get("flag", ""),
            "home_score":         m["home_score"],
            "away_score":         m["away_score"],
            "home_display_score": home_display_score,
            "away_display_score": away_display_score,
            "home_score_90":      home_score_90,
            "away_score_90":      away_score_90,
            "went_to_et":         went_to_et,
            "went_to_pen":        went_to_pen,
            "group":         m["group"],
            "matchday":      m.get("matchday", ""),
            "type":          m["type"],
            "finished":      m["finished"].upper() == "TRUE",
            "time_elapsed":  m.get("time_elapsed", "notstarted"),
            "stadium":       stad.get("name_en", ""),
            "city":          stad.get("city_en", ""),
        })

    enriched.sort(key=lambda x: x["datetime"])
    return enriched


def _phase_label(match: dict) -> str:
    if match["type"] == "group":
        return f"Group {match['group']}"
    return _PHASE_LABELS.get(match["type"], match["type"])


def _mult_badge_html(match: dict, badge_color: str) -> str:
    mult = _MULTIPLIERS.get(match["type"], 1)
    if mult <= 1:
        return ""
    return (
        f'<span style="border:1px solid {badge_color};color:{badge_color};'
        f'padding:2px 8px;border-radius:20px;font-size:.72rem;font-weight:700;'
        f'margin-left:6px;letter-spacing:.03em;">×{mult}</span>'
    )


def _flag_img(url: str, emoji_fallback: str) -> str:
    if url:
        return (
            f'<img src="{url}" style="width:56px;height:37px;'
            f'object-fit:cover;border-radius:4px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,.3);">'
        )
    return f'<span style="font-size:2.2rem;line-height:1;">{emoji_fallback}</span>'


def _bettors_html(bettors: dict) -> str:
    """3-column list of bettor first-names, shown after kickoff."""
    def _col(names: list[str]) -> str:
        if not names:
            return '<div style="opacity:.22;font-size:.65rem;text-align:center;">—</div>'
        return "".join(
            f'<div style="font-size:.68rem;opacity:.72;text-align:center;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{n}</div>'
            for n in names
        )
    return (
        f'<div style="margin-top:6px;padding-top:6px;'
        f'border-top:1px solid rgba(128,128,128,0.1);">'
        f'<div style="display:flex;gap:4px;">'
        f'<div style="flex:1;min-width:0;">{_col(bettors.get("1", []))}</div>'
        f'<div style="flex:1;min-width:0;">{_col(bettors.get("X", []))}</div>'
        f'<div style="flex:1;min-width:0;">{_col(bettors.get("2", []))}</div>'
        f'</div>'
        f'</div>'
    )


def _odds_html(odds: dict | None, badge_color: str, winning_outcome: str | None = None) -> str:
    if not odds:
        return ""
    if odds.get("final") and odds["final"]["home"] is not None:
        data, label = odds["final"], "Final odds"
    elif odds.get("indicative") and odds["indicative"]["home"] is not None:
        data, label = odds["indicative"], "Indicative odds"
    else:
        return ""

    def _cell(key: str, value) -> str:
        if winning_outcome == key:
            wrap = (f'border:2px solid {badge_color};border-radius:8px;'
                    f'padding:4px 10px;background:rgba(128,128,128,0.08);')
        else:
            wrap = 'padding:4px 10px;'
        return (
            f'<div style="text-align:center;{wrap}">'
            f'<div style="font-size:.6rem;opacity:.4;margin-bottom:2px;">{key}</div>'
            f'<div style="font-size:.9rem;font-weight:700;color:{badge_color};">{value}</div>'
            f'</div>'
        )

    return (
        f'<div style="border-top:1px solid rgba(128,128,128,0.12);margin:6px 0 0;padding-top:6px;">'
        f'<div style="display:flex;justify-content:space-around;align-items:center;padding:0 4px;">'
        f'{_cell("1", data["home"])}'
        f'{_cell("X", data["draw"])}'
        f'{_cell("2", data["away"])}'
        f'</div>'
        f'<div style="text-align:center;font-size:.65rem;opacity:.38;margin-top:4px;">{label}</div>'
        f'</div>'
    )


def _card_html(match: dict, odds: dict | None = None, bettors: dict | None = None) -> str:
    """Full single-block card for finished matches."""
    badge_color = _BADGE_COLORS.get(match["type"], "#888")
    phase       = _phase_label(match)
    mult_badge  = _mult_badge_html(match, badge_color)
    matchday    = f"Matchday {match['matchday']}" if match["matchday"] and match["type"] == "group" else ""
    home_img    = _flag_img(match["home_flag_url"], match["home_flag"])
    away_img    = _flag_img(match["away_flag_url"], match["away_flag"])

    if match["finished"]:
        if match.get("went_to_pen"):
            ext = '<div style="font-size:.65rem;font-weight:600;opacity:.5;margin-top:2px;text-align:center;">PEN</div>'
        elif match.get("went_to_et"):
            ext = '<div style="font-size:.65rem;font-weight:600;opacity:.5;margin-top:2px;text-align:center;">AET</div>'
        else:
            ext = ""
        h_disp = match.get("home_display_score", match["home_score"])
        a_disp = match.get("away_display_score", match["away_score"])
        centre = (
            f'<div style="font-size:1.55rem;font-weight:800;color:{badge_color};">'
            f'{h_disp} - {a_disp}</div>'
            f'{ext}'
        )
        try:
            h90, a90 = int(match.get("home_score_90", match["home_score"])), int(match.get("away_score_90", match["away_score"]))
            winning_outcome = "1" if h90 > a90 else ("X" if h90 == a90 else "2")
        except (TypeError, ValueError):
            winning_outcome = None
    else:
        centre = (
            f'<div style="font-size:1rem;font-weight:700;opacity:.45;">-</div>'
            f'<div style="font-size:.8rem;font-weight:600;opacity:.55;margin-top:3px;">'
            f'{match["cet_time_str"]}</div>'
        )
        winning_outcome = None

    venue       = f'🏟 {match["stadium"]}' if match["stadium"] else ""
    odds_row    = _odds_html(odds, badge_color, winning_outcome)
    bettors_row = _bettors_html(bettors) if bettors is not None else ""

    return f"""
<div style="border:1px solid rgba(128,128,128,0.18);border-radius:14px;
            padding:16px 14px 12px;margin-bottom:10px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <div>
      <span style="background:{badge_color};color:#fff;padding:2px 10px;
                   border-radius:20px;font-size:.72rem;font-weight:700;
                   letter-spacing:.03em;">{phase}</span>{mult_badge}
    </div>
    <span style="font-size:.72rem;opacity:.45;">{matchday}</span>
  </div>
  <div style="display:flex;align-items:flex-start;justify-content:space-between;
              gap:4px;margin:4px 0 10px;">
    <div style="flex:1;text-align:center;">
      {home_img}
      <div style="font-weight:700;font-size:.82rem;margin-top:6px;line-height:1.3;height:2.6em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">
        {match["home_name"]}
      </div>
    </div>
    <div style="flex:0 0 52px;height:37px;display:flex;flex-direction:column;
                align-items:center;justify-content:center;">{centre}</div>
    <div style="flex:1;text-align:center;">
      {away_img}
      <div style="font-weight:700;font-size:.82rem;margin-top:6px;line-height:1.3;height:2.6em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">
        {match["away_name"]}
      </div>
    </div>
  </div>
  {odds_row}
  {bettors_row}
  <div style="border-top:1px solid rgba(128,128,128,0.12);padding-top:7px;
              margin-top:4px;display:flex;justify-content:space-between;
              font-size:.7rem;opacity:.45;gap:6px;">
    <span style="white-space:nowrap;">{match["cet_date_str"]} &nbsp;{match["cet_time_str"]} CET</span>
    <span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">{venue}</span>
  </div>
</div>
"""


def _card_html_top(match: dict) -> str:
    """Top portion of a split card (header + teams). Has class='card-top' for CSS scoping."""
    badge_color = _BADGE_COLORS.get(match["type"], "#888")
    phase       = _phase_label(match)
    mult_badge  = _mult_badge_html(match, badge_color)
    matchday    = f"Matchday {match['matchday']}" if match["matchday"] and match["type"] == "group" else ""
    home_img    = _flag_img(match["home_flag_url"], match["home_flag"])
    away_img    = _flag_img(match["away_flag_url"], match["away_flag"])
    centre = (
        f'<div style="font-size:1rem;font-weight:700;opacity:.45;">-</div>'
        f'<div style="font-size:.8rem;font-weight:600;opacity:.55;margin-top:3px;">'
        f'{match["cet_time_str"]}</div>'
    )
    return f"""
<div class="card-top" style="border:1px solid rgba(128,128,128,0.18);
     border-radius:14px 14px 0 0;border-bottom:none;padding:16px 14px 10px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <div>
      <span style="background:{badge_color};color:#fff;padding:2px 10px;
                   border-radius:20px;font-size:.72rem;font-weight:700;
                   letter-spacing:.03em;">{phase}</span>{mult_badge}
    </div>
    <span style="font-size:.72rem;opacity:.45;">{matchday}</span>
  </div>
  <div style="display:flex;align-items:flex-start;justify-content:space-between;
              gap:4px;margin:4px 0 4px;">
    <div style="flex:1;text-align:center;">
      {home_img}
      <div style="font-weight:700;font-size:.82rem;margin-top:6px;line-height:1.3;height:2.6em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">
        {match["home_name"]}
      </div>
    </div>
    <div style="flex:0 0 52px;height:37px;display:flex;flex-direction:column;
                align-items:center;justify-content:center;">{centre}</div>
    <div style="flex:1;text-align:center;">
      {away_img}
      <div style="font-weight:700;font-size:.82rem;margin-top:6px;line-height:1.3;height:2.6em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">
        {match["away_name"]}
      </div>
    </div>
  </div>
</div>
"""


def _card_html_bottom(
    match: dict,
    potential_win_html: str = "",
    odds_label: str = "",
    bettors_html: str = "",
) -> str:
    """Footer portion of a split card (odds label + bettors + potential win + date/venue)."""
    venue = f'🏟 {match["stadium"]}' if match["stadium"] else ""
    odds_label_html = (
        f'<div style="text-align:center;font-size:.65rem;opacity:.38;padding:4px 0 2px;">{odds_label}</div>'
        if odds_label else ""
    )
    return f"""
<div class="card-bottom" style="border:1px solid rgba(128,128,128,0.18);
     border-radius:0 0 14px 14px;border-top:none;padding:4px 14px 10px;margin-bottom:10px;">
  {odds_label_html}
  {bettors_html}
  {potential_win_html}
  <div style="display:flex;justify-content:space-between;font-size:.7rem;opacity:.45;gap:6px;">
    <span style="white-space:nowrap;">{match["cet_date_str"]} &nbsp;{match["cet_time_str"]} CET</span>
    <span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">{venue}</span>
  </div>
</div>
"""


@st.fragment
def _interactive_match_card(
    match: dict,
    odds_data: dict | None,
    user_tip_initial: dict | None,
    user_id: int,
    clickable: bool = True,
    bettors: dict | None = None,
) -> None:
    mid       = str(match["id"])
    tip_key   = f"_tip_{mid}"
    toast_key = f"_toast_{mid}"

    if clickable:
        # Toast set by the previous interaction is shown here, at the start of the
        # re-run, so it isn't discarded by st.rerun(scope="fragment") below.
        if toast_key in st.session_state:
            msg, icon = st.session_state.pop(toast_key)
            st.toast(msg, icon=icon)
        # Session state wins over the DB-loaded initial value for instant feedback
        user_tip = st.session_state.get(tip_key, user_tip_initial)
        current  = user_tip["tip"] if user_tip else None
    else:
        user_tip = None
        current  = None

    st.markdown(_card_html_top(match), unsafe_allow_html=True)

    if odds_data and odds_data.get("final") and odds_data["final"]["home"] is not None:
        best, odds_label = odds_data["final"], "Final odds"
    elif odds_data and odds_data.get("indicative") and odds_data["indicative"]["home"] is not None:
        best, odds_label = odds_data["indicative"], "Indicative odds"
    else:
        best, odds_label = None, ""
    h_o = best["home"] if best else None
    d_o = best["draw"] if best else None
    a_o = best["away"] if best else None

    c1, cx, c2 = st.columns(3)
    for col, tip_val, odds_val in ((c1, "1", h_o), (cx, "X", d_o), (c2, "2", a_o)):
        is_sel = current == tip_val
        lbl    = f"**{tip_val}**\n\n{odds_val}" if odds_val is not None else f"**{tip_val}**"
        with col:
            if st.button(
                lbl,
                key=f"tip_{tip_val}_{mid}",
                type="primary" if is_sel else "secondary",
                use_container_width=True,
                disabled=not clickable,
            ):
                if is_sel:
                    cancel_tip(user_id, mid)
                    st.session_state[tip_key] = None
                    st.session_state[toast_key] = ("Tip removed", "🗑️")
                else:
                    submit_tip(user_id, mid, tip_val, odds_val)
                    st.session_state[tip_key] = {"tip": tip_val, "odds": odds_val}
                    st.session_state[toast_key] = (f"Tip saved: {tip_val}", "✅")
                st.rerun(scope="fragment")

    # Potential win — use live odds for the current tip direction, not stored tip odds
    _live_odds = {"1": h_o, "X": d_o, "2": a_o}.get(current)
    mult        = _MULTIPLIERS.get(match["type"], 1)
    no_bet_gx   = 70 * mult
    if not clickable:
        pw_html = ""
    elif current is not None and _live_odds is not None:
        gx = round(100 * float(_live_odds) * mult)
        pw_html = (
            f'<div style="text-align:center;padding:5px 0 4px;font-size:.8rem;">'
            f'<span style="opacity:.45;">Correct → </span>'
            f'<span style="font-weight:800;color:#4caf50;">{gx} GX</span>'
            f'</div>'
        )
    elif current is not None:
        pw_html = (
            f'<div style="text-align:center;padding:5px 0 4px;font-size:.8rem;">'
            f'<span style="opacity:.45;">Correct → </span>'
            f'<span style="font-weight:700;">— GX</span>'
            f'</div>'
        )
    else:
        pw_html = (
            f'<div style="text-align:center;padding:5px 0 4px;font-size:.8rem;">'
            f'<span style="opacity:.45;">No tip · </span>'
            f'<span style="font-weight:700;opacity:.55;">{no_bet_gx} GX</span>'
            f'</div>'
        )

    bh = _bettors_html(bettors) if bettors is not None else ""
    st.markdown(_card_html_bottom(match, potential_win_html=pw_html, odds_label=odds_label, bettors_html=bh), unsafe_allow_html=True)


def render_matches_page():
    current_user = st.session_state.get("user", {})
    is_admin     = current_user.get("is_admin", False)
    user_id      = current_user.get("id")

    with st.spinner("Loading matches…"):
        try:
            matches = fetch_matches()
        except Exception:
            st.error("Could not load match data — the data provider is temporarily unavailable. Please refresh in a moment.")
            return

    # Throttle the final-odds check to once per 5 min in the UI;
    # GitHub Actions is the primary driver so this is just a fallback.
    now_utc = datetime.utcnow()
    last_check = st.session_state.get("_last_odds_check")
    if last_check is None or (now_utc - last_check).total_seconds() > 300:
        maybe_fetch_final_odds(matches)
        st.session_state["_last_odds_check"] = now_utc

    all_odds        = get_all_match_odds()
    user_tips       = get_user_tips(user_id) if user_id and not is_admin else {}
    all_tips_names  = get_all_tips_with_names()

    st.title("⚽ Matches")

    # Inject CSS once per render (idempotent via Streamlit's deduplication)
    st.markdown(_TIP_CSS, unsafe_allow_html=True)

    # ── Filter row 1: Phase ────────────────────────────────────────────────────
    sorted_groups  = sorted(set(m["group"] for m in matches if m["type"] == "group"))
    group_pills    = [f"Group {g}" for g in sorted_groups]
    phase_pills    = [_PHASE_LABELS[p] for p in _PHASE_ORDER if p != "group"]
    all_options    = ["All"] + group_pills + phase_pills
    group_pill_set = set(group_pills)

    phase_sel = st.pills("Phase", all_options, default="All", label_visibility="collapsed")
    if phase_sel is None:
        phase_sel = "All"

    # ── Filter row 2: Status + Quick date + Date range ────────────────────────
    cet_now      = datetime.utcnow() + timedelta(hours=_CET_OFFSET)
    today_cet    = cet_now.date()
    all_cet_dates = sorted(set(m["cet_date"] for m in matches))

    scol, qcol, tcol, dcol = st.columns([1, 1, 1, 2])

    with scol:
        status_sel = st.pills(
            "Status",
            ["Upcoming", "Finished"],
            default="Upcoming",
            label_visibility="collapsed",
        )

    with qcol:
        quick_date = st.pills(
            "Quick",
            ["Today", "Next 24 h"],
            default=None,
            label_visibility="collapsed",
        )

    with tcol:
        tip_sel = st.pills(
            "Tip",
            ["Guessed", "Not guessed"],
            default=None,
            label_visibility="collapsed",
        )

    with dcol:
        if not quick_date:
            date_range = st.date_input(
                "Dates",
                value=(all_cet_dates[0], all_cet_dates[-1]),
                min_value=all_cet_dates[0],
                max_value=all_cet_dates[-1],
                format="DD/MM/YYYY",
            )
        else:
            date_range = None

    # ── Apply filters ──────────────────────────────────────────────────────────
    label_to_phase = {v: k for k, v in _PHASE_LABELS.items()}

    # Phase
    if phase_sel == "All":
        filtered = matches
    elif phase_sel in group_pill_set:
        group_char = phase_sel.split(" ", 1)[1]
        filtered = [m for m in matches if m["type"] == "group" and m["group"] == group_char]
    else:
        phase_key = label_to_phase.get(phase_sel)
        filtered = [m for m in matches if m["type"] == phase_key] if phase_key else matches

    # Status (single-select: None = show all)
    if status_sel == "Upcoming":
        filtered = [m for m in filtered if not m["finished"]]
    elif status_sel == "Finished":
        filtered = [m for m in filtered if m["finished"]]

    # Date — quick shortcuts take priority over the range picker
    if quick_date == "Today":
        filtered = [m for m in filtered if m["cet_date"] == today_cet]
    elif quick_date == "Next 24 h":
        cutoff = cet_now + timedelta(hours=24)
        filtered = [m for m in filtered if cet_now <= m["cet_datetime"] <= cutoff]
    elif date_range is not None:
        if isinstance(date_range, tuple) and len(date_range) == 2:
            d_from, d_to = date_range
            filtered = [m for m in filtered if d_from <= m["cet_date"] <= d_to]
        elif hasattr(date_range, "year"):
            filtered = [m for m in filtered if m["cet_date"] == date_range]

    # Tip filter (single-select: None = show all)
    if tip_sel == "Guessed":
        filtered = [m for m in filtered if str(m["id"]) in user_tips]
    elif tip_sel == "Not guessed":
        filtered = [m for m in filtered if str(m["id"]) not in user_tips]

    if not filtered:
        st.info("No matches found for the selected filters.")
        return

    if status_sel == "Finished":
        filtered = sorted(filtered, key=lambda m: m["datetime"], reverse=True)

    # ── Pagination — reset when filters change ────────────────────────────────
    filter_key = (phase_sel, status_sel, quick_date, str(date_range), tip_sel)
    if st.session_state.get("_match_filter_key") != filter_key:
        st.session_state["_match_filter_key"] = filter_key
        st.session_state["_match_show_count"] = _PAGE_SIZE

    show_count = st.session_state.get("_match_show_count", _PAGE_SIZE)
    visible    = filtered[:show_count]

    # ── Card list — sequential render guarantees correct time order everywhere ──
    for match in visible:
        odds        = all_odds.get((match["home_name"], match["away_name"]))
        # Trust time_elapsed only when we're within 30 min of kickoff — the API
        # sometimes prematurely sets "live" for upcoming matches.
        kickoff_close = now_utc >= match["utc_kickoff"] - timedelta(minutes=30)
        started     = (
            now_utc >= match["utc_kickoff"]
            or (match.get("time_elapsed", "notstarted") != "notstarted" and kickoff_close)
        )
        interactive = not match["finished"]
        user_tip    = user_tips.get(str(match["id"]))
        revealed    = started or match["finished"]
        bettors     = all_tips_names.get(str(match["id"])) if revealed else None

        if interactive:
            clickable = not started and not is_admin
            _interactive_match_card(match, odds, user_tip, user_id, clickable=clickable, bettors=bettors)
        else:
            st.markdown(_card_html(match, odds, bettors=bettors), unsafe_allow_html=True)

    # ── Show more button ──────────────────────────────────────────────────────
    remaining = len(filtered) - show_count
    if remaining > 0:
        st.markdown("")
        if st.button(
            f"Show {min(_PAGE_SIZE, remaining)} more  ({remaining} remaining)",
            use_container_width=True,
        ):
            st.session_state["_match_show_count"] = show_count + _PAGE_SIZE
            st.rerun()

    st.caption("Data: worldcup26.ir · refreshed every 10 min")
