from datetime import datetime, timedelta

import requests
import streamlit as st

from odds import get_all_match_odds, maybe_fetch_final_odds
from tips import cancel_tip, get_user_tips, submit_tip

_BASE = "https://raw.githubusercontent.com/rezarahiminia/worldcup2026/main"

# Summer 2026: Europe is on CEST (UTC+2). Displayed as "CET" per user preference.
_CET_OFFSET = 2

# FIFA 2026 venue keywords → local UTC offset during summer (DST) 2026
_STADIUM_TZ: dict[str, int] = {
    # Eastern (EDT = UTC-4)
    "metlife": -4, "gillette": -4, "hard rock": -4,
    "mercedes": -4, "lincoln": -4, "bmo": -4,
    # Central (CDT = UTC-5)
    "at&t": -5, "nrg": -5, "arrowhead": -5,
    "azteca": -5, "bbva": -5, "akron": -5,
    # Pacific (PDT = UTC-7)
    "sofi": -7, "levi": -7, "lumen": -7, "bc place": -7,
}

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
# into a visually seamless unit.  Only affects stVerticalBlocks that contain a .card-top
# element, so it is scoped to our match-card columns.
_TIP_CSS = """
<style>
[data-testid="stVerticalBlock"]:has(> .stElementContainer .card-top) {
    gap: 0 !important;
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


@st.cache_data(ttl=3600)
def fetch_matches() -> list[dict]:
    matches_raw  = requests.get(f"{_BASE}/football.matches.json",  timeout=10).json()
    teams_raw    = requests.get(f"{_BASE}/football.teams.json",    timeout=10).json()
    stadiums_raw = requests.get(f"{_BASE}/football.stadiums.json", timeout=10).json()

    teams    = {t["id"]: t for t in teams_raw}
    stadiums = {s["id"]: s for s in stadiums_raw}

    enriched = []
    for m in matches_raw:
        dt     = datetime.strptime(m["local_date"], "%m/%d/%Y %H:%M")
        home_t = teams.get(m["home_team_id"], {})
        away_t = teams.get(m["away_team_id"], {})
        stad   = stadiums.get(m["stadium_id"], {})

        cet_dt = dt + timedelta(hours=_CET_OFFSET - _local_utc_offset(stad.get("name_en", "")))
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
            "home_name":     home_t.get("name_en") or m.get("home_team_label", "TBD"),
            "home_flag":     _flag(home_t.get("iso2", "")),
            "home_flag_url": home_t.get("flag", ""),
            "away_name":     away_t.get("name_en") or m.get("away_team_label", "TBD"),
            "away_flag":     _flag(away_t.get("iso2", "")),
            "away_flag_url": away_t.get("flag", ""),
            "home_score":    m["home_score"],
            "away_score":    m["away_score"],
            "group":         m["group"],
            "matchday":      m.get("matchday", ""),
            "type":          m["type"],
            "finished":      m["finished"].upper() == "TRUE",
            "stadium":       stad.get("name_en", ""),
            "city":          stad.get("city_en", ""),
        })

    enriched.sort(key=lambda x: x["datetime"])
    return enriched


def _phase_label(match: dict) -> str:
    if match["type"] == "group":
        return f"Group {match['group']}"
    return _PHASE_LABELS.get(match["type"], match["type"])


def _flag_img(url: str, emoji_fallback: str) -> str:
    if url:
        return (
            f'<img src="{url}" style="width:56px;height:37px;'
            f'object-fit:cover;border-radius:4px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,.3);">'
        )
    return f'<span style="font-size:2.2rem;line-height:1;">{emoji_fallback}</span>'


def _odds_html(odds: dict | None, badge_color: str) -> str:
    if not odds:
        return ""
    data = odds.get("final") or odds.get("indicative")
    if not data or data["home"] is None:
        return ""
    label = "final" if "final" in odds else "indicative"
    return (
        f'<div style="display:flex;justify-content:space-around;align-items:center;'
        f'margin:6px 0 2px;padding:6px 4px 0;'
        f'border-top:1px solid rgba(128,128,128,0.12);">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:.6rem;opacity:.4;margin-bottom:2px;">1</div>'
        f'<div style="font-size:.9rem;font-weight:700;color:{badge_color};">{data["home"]}</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:.6rem;opacity:.4;margin-bottom:2px;">X</div>'
        f'<div style="font-size:.9rem;font-weight:700;color:{badge_color};">{data["draw"]}</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:.6rem;opacity:.4;margin-bottom:2px;">2</div>'
        f'<div style="font-size:.9rem;font-weight:700;color:{badge_color};">{data["away"]}</div>'
        f'</div>'
        f'<div style="font-size:.6rem;opacity:.35;font-style:italic;align-self:flex-end;padding-bottom:1px;">'
        f'{label}</div>'
        f'</div>'
    )


def _card_html(match: dict, odds: dict | None = None) -> str:
    """Full single-block card — used for admin view and finished matches."""
    badge_color = _BADGE_COLORS.get(match["type"], "#888")
    phase       = _phase_label(match)
    matchday    = f"Matchday {match['matchday']}" if match["matchday"] and match["type"] == "group" else ""
    home_img    = _flag_img(match["home_flag_url"], match["home_flag"])
    away_img    = _flag_img(match["away_flag_url"], match["away_flag"])

    if match["finished"]:
        centre = (
            f'<div style="font-size:1.55rem;font-weight:800;color:{badge_color};">'
            f'{match["home_score"]} - {match["away_score"]}</div>'
        )
    else:
        centre = (
            f'<div style="font-size:1rem;font-weight:700;opacity:.45;">VS</div>'
            f'<div style="font-size:.8rem;font-weight:600;opacity:.55;margin-top:3px;">'
            f'{match["cet_time_str"]}</div>'
        )

    venue    = f'🏟 {match["stadium"]}' if match["stadium"] else ""
    odds_row = _odds_html(odds, badge_color)

    return f"""
<div style="border:1px solid rgba(128,128,128,0.18);border-radius:14px;
            padding:16px 14px 12px;margin-bottom:10px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="background:{badge_color};color:#fff;padding:2px 10px;
                 border-radius:20px;font-size:.72rem;font-weight:700;
                 letter-spacing:.03em;">{phase}</span>
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
    matchday    = f"Matchday {match['matchday']}" if match["matchday"] and match["type"] == "group" else ""
    home_img    = _flag_img(match["home_flag_url"], match["home_flag"])
    away_img    = _flag_img(match["away_flag_url"], match["away_flag"])
    centre = (
        f'<div style="font-size:1rem;font-weight:700;opacity:.45;">VS</div>'
        f'<div style="font-size:.8rem;font-weight:600;opacity:.55;margin-top:3px;">'
        f'{match["cet_time_str"]}</div>'
    )
    return f"""
<div class="card-top" style="border:1px solid rgba(128,128,128,0.18);
     border-radius:14px 14px 0 0;border-bottom:none;padding:16px 14px 10px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="background:{badge_color};color:#fff;padding:2px 10px;
                 border-radius:20px;font-size:.72rem;font-weight:700;
                 letter-spacing:.03em;">{phase}</span>
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


def _card_html_bottom(match: dict, potential_win_html: str = "") -> str:
    """Footer portion of a split card (potential win + date/venue)."""
    venue = f'🏟 {match["stadium"]}' if match["stadium"] else ""
    return f"""
<div class="card-bottom" style="border:1px solid rgba(128,128,128,0.18);
     border-radius:0 0 14px 14px;border-top:none;padding:4px 14px 10px;margin-bottom:10px;">
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
) -> None:
    mid     = str(match["id"])
    tip_key = f"_tip_{mid}"

    # Session state wins over the DB-loaded initial value for instant feedback
    user_tip = st.session_state.get(tip_key, user_tip_initial)
    current  = user_tip["tip"] if user_tip else None

    st.markdown(_card_html_top(match), unsafe_allow_html=True)

    best = (odds_data.get("final") or odds_data.get("indicative")) if odds_data else None
    h_o  = best["home"] if best else None
    d_o  = best["draw"] if best else None
    a_o  = best["away"] if best else None

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
            ):
                if is_sel:
                    cancel_tip(user_id, mid)
                    st.session_state[tip_key] = None
                else:
                    submit_tip(user_id, mid, tip_val, odds_val)
                    st.session_state[tip_key] = {"tip": tip_val, "odds": odds_val}
                st.rerun(scope="fragment")

    # Potential win line — updates instantly on tip change (fragment scope)
    if user_tip and user_tip.get("odds") is not None:
        gx = round(100 * float(user_tip["odds"]))
        pw_html = (
            f'<div style="text-align:center;padding:5px 0 4px;font-size:.8rem;">'
            f'<span style="opacity:.45;">Correct → </span>'
            f'<span style="font-weight:800;color:#4caf50;">{gx} GX</span>'
            f'</div>'
        )
    elif user_tip:
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
            f'<span style="font-weight:700;opacity:.55;">70 GX</span>'
            f'</div>'
        )

    st.markdown(_card_html_bottom(match, potential_win_html=pw_html), unsafe_allow_html=True)


def render_matches_page():
    current_user = st.session_state.get("user", {})
    is_admin     = current_user.get("is_admin", False)
    user_id      = current_user.get("id")

    with st.spinner("Loading matches…"):
        matches = fetch_matches()

    maybe_fetch_final_odds(matches)
    all_odds  = get_all_match_odds()
    user_tips = get_user_tips(user_id) if user_id and not is_admin else {}

    st.title("⚽ Matches")

    # Inject gap-collapse CSS for split match cards (once per render, idempotent)
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

    if not is_admin:
        scol, qcol, tcol, dcol = st.columns([1, 1, 1, 2])
    else:
        scol, qcol, dcol = st.columns([1, 1, 2])
        tcol = None

    with scol:
        status_sel = st.pills(
            "Status",
            ["Upcoming", "Finished"],
            selection_mode="multi",
            default=["Upcoming"],
            label_visibility="collapsed",
        )
        if not status_sel:
            status_sel = ["Upcoming", "Finished"]

    with qcol:
        quick_date = st.pills(
            "Quick",
            ["Today", "Next 24 h"],
            default=None,
            label_visibility="collapsed",
        )

    if tcol is not None:
        with tcol:
            tip_sel = st.pills(
                "Tip",
                ["Guessed", "Not guessed"],
                selection_mode="multi",
                default=None,
                label_visibility="collapsed",
            )
    else:
        tip_sel = None

    with dcol:
        if not quick_date:
            default_from = max(all_cet_dates[0], today_cet)
            date_range = st.date_input(
                "Dates",
                value=(default_from, all_cet_dates[-1]),
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

    # Status
    show_upcoming = "Upcoming" in status_sel
    show_finished = "Finished" in status_sel
    filtered = [
        m for m in filtered
        if (show_upcoming and not m["finished"]) or (show_finished and m["finished"])
    ]

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

    # Tip filter — only when exactly one option selected (both = show all, none = show all)
    if tip_sel and set(tip_sel) != {"Guessed", "Not guessed"}:
        show_guessed   = "Guessed"     in tip_sel
        show_unguessed = "Not guessed" in tip_sel
        filtered = [
            m for m in filtered
            if (show_guessed   and     str(m["id"]) in user_tips)
            or (show_unguessed and not str(m["id"]) in user_tips)
        ]

    if not filtered:
        st.info("No matches found for the selected filters.")
        return

    # ── 3-column card grid ────────────────────────────────────────────────────
    cols = st.columns(3)
    for i, match in enumerate(filtered):
        odds        = all_odds.get((match["home_name"], match["away_name"]))
        interactive = not is_admin and not match["finished"]
        user_tip    = user_tips.get(str(match["id"]))

        with cols[i % 3]:
            if interactive:
                _interactive_match_card(match, odds, user_tip, user_id)
            else:
                st.markdown(_card_html(match, odds), unsafe_allow_html=True)

    st.caption("Data: github.com/rezarahiminia/worldcup2026 · refreshed every hour")
