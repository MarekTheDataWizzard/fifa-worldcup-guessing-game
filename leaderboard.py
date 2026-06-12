import streamlit as st

from auth import get_all_users, get_connection
from matches import fetch_matches
from odds import get_all_match_odds

# ─── Scoring parameters — edit here to recalculate everything ─────────────────
_STAKE      = 100  # GX allocated per match
_NO_BET_PTS = 70   # GX when no tip placed
_LOSS_PTS   = 0    # GX on wrong tip
# Correct tip earns: round(_STAKE * tip_odds, 2)

_PHASE_LABELS = {
    "r32":   "Round of 32",
    "r16":   "Round of 16",
    "qf":    "Quarter-final",
    "sf":    "Semi-final",
    "third": "3rd Place",
    "final": "Final",
}
_PHASE_ORDER = ["group", "r32", "r16", "qf", "sf", "third", "final"]


# ─── Data helpers ──────────────────────────────────────────────────────────────

def _get_all_tips() -> list[dict]:
    result = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, match_id, tip, odds FROM tips;")
                for row in cur.fetchall():
                    result.append({
                        "user_id":  row[0],
                        "match_id": str(row[1]),
                        "tip":      row[2],
                        "odds":     float(row[3]) if row[3] else None,
                    })
    except Exception:
        pass
    return result


def _outcome(home_score, away_score) -> str:
    try:
        h, a = int(home_score), int(away_score)
        if h > a:  return "1"
        if h == a: return "X"
        return "2"
    except (TypeError, ValueError):
        return ""


def _match_bucket(match: dict) -> tuple[str, str]:
    """(group_label, matchday_label) for a match."""
    if match["type"] == "group":
        return f"Group {match['group']}", f"Matchday {match['matchday']}"
    label = _PHASE_LABELS.get(match["type"], match["type"])
    return label, label


# ─── Score computation ─────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def _compute_scores() -> tuple[list[dict], list[str], list[str]]:
    """
    Returns (user_scores, groups_ordered, matchdays_ordered).
    Cached 2 min; every cache miss re-fetches matches, tips, and users.
    """
    matches      = fetch_matches()
    all_tips     = _get_all_tips()
    users        = [u for u in get_all_users() if not u.get("is_admin")]
    all_odds     = get_all_match_odds()

    finished = [
        m for m in matches
        if m["finished"] and _outcome(m["home_score"], m["away_score"])
    ]
    finished.sort(key=lambda m: (
        _PHASE_ORDER.index(m["type"]) if m["type"] in _PHASE_ORDER else 99,
        m.get("matchday", ""),
    ))

    # Build per-match odds lookup (final preferred, indicative fallback)
    _DIR = {"1": "home", "X": "draw", "2": "away"}
    match_rates: dict[str, dict[str, float | None]] = {}
    for m in finished:
        od = all_odds.get((m["home_name"], m["away_name"]))
        if od:
            src = od.get("final") or od.get("indicative")
            if src:
                match_rates[str(m["id"])] = {
                    "1": src.get("home"), "X": src.get("draw"), "2": src.get("away")
                }

    tips_idx = {(t["user_id"], t["match_id"]): t for t in all_tips}

    # Collect ordered unique groups and matchdays from finished matches only
    groups_ordered:    list[str] = []
    matchdays_ordered: list[str] = []
    seen_g: set[str] = set()
    seen_m: set[str] = set()
    for m in finished:
        grp, md = _match_bucket(m)
        if grp not in seen_g: seen_g.add(grp); groups_ordered.append(grp)
        if md  not in seen_m: seen_m.add(md);  matchdays_ordered.append(md)

    user_scores = []
    for u in users:
        uid          = u["id"]
        display_name = f"{u['first_name']} {u['last_name']}".strip() or u["nickname"]
        total_gx     = 0.0
        bets         = 0
        by_group:    dict[str, float] = {}
        by_matchday: dict[str, float] = {}

        for m in finished:
            mid     = str(m["id"])
            grp, md = _match_bucket(m)
            outcome = _outcome(m["home_score"], m["away_score"])
            tip     = tips_idx.get((uid, mid))

            if tip is None:
                gx = float(_NO_BET_PTS)
            elif tip["tip"] == outcome:
                rates = match_rates.get(mid)
                rate  = (rates.get(tip["tip"]) if rates else None) or tip["odds"] or 1.0
                gx    = round(_STAKE * rate, 2)
                bets += 1
            else:
                gx   = float(_LOSS_PTS)
                bets += 1

            total_gx       += gx
            by_group[grp]   = round(by_group.get(grp, 0.0) + gx, 2)
            by_matchday[md] = round(by_matchday.get(md, 0.0) + gx, 2)

        user_scores.append({
            "id":           uid,
            "display_name": display_name,
            "nickname":     u["nickname"],
            "total_gx":     round(total_gx, 2),
            "bets":         bets,
            "by_group":     by_group,
            "by_matchday":  by_matchday,
        })

    user_scores.sort(key=lambda x: -x["total_gx"])
    return user_scores, groups_ordered, matchdays_ordered


# ─── Rendering helpers ─────────────────────────────────────────────────────────

def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")


def _overall_table_html(rows: list[dict], current_user_id: int | None = None) -> str:
    trs = []
    for i, u in enumerate(rows):
        rank  = i + 1
        is_me = u["id"] == current_user_id
        bg    = "background:rgba(255,215,0,0.06);" if is_me else ""
        trs.append(f"""
<tr style="border-bottom:1px solid rgba(128,128,128,0.1);{bg}">
  <td style="padding:10px 12px;white-space:nowrap;opacity:.7;">{_medal(rank)}</td>
  <td style="padding:10px 8px;font-weight:600;">{u['display_name']}{"&nbsp;⭐" if is_me else ""}</td>
  <td style="padding:10px 8px;opacity:.42;font-size:.82rem;">@{u['nickname']}</td>
  <td style="padding:10px 12px;font-weight:700;color:#ffd700;text-align:right;white-space:nowrap;">{u['total_gx']:,.0f}&nbsp;GX</td>
  <td style="padding:10px 12px;opacity:.5;text-align:right;font-size:.85rem;">{u['bets']}</td>
</tr>""")
    return f"""
<table style="width:100%;border-collapse:collapse;">
<thead><tr style="border-bottom:1px solid rgba(128,128,128,0.25);">
  <th style="padding:8px 12px;opacity:.42;text-align:left;font-weight:500;font-size:.8rem;">Rank</th>
  <th style="padding:8px;opacity:.42;text-align:left;font-weight:500;font-size:.8rem;" colspan="2">Player</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">GX</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">Bets</th>
</tr></thead>
<tbody>{''.join(trs)}</tbody>
</table>"""


def _podium_html(top3: list[dict], gx_values: list[float]) -> str:
    """
    top3: [rank1, rank2, rank3] (sorted highest first).
    gx_values: parallel GX scores (may differ from total_gx when scoped to group/matchday).
    Display order: 2nd (left) · 1st (centre, tallest) · 3rd (right).
    """
    if not top3:
        return "<p style='opacity:.5;text-align:center;padding:32px 0;'>No finished matches in this selection yet.</p>"

    display_order = [1, 0, 2]   # list indices: left=2nd, centre=1st, right=3rd
    bar_heights   = ["60px", "88px", "44px"]
    medals        = ["🥈", "🥇", "🥉"]
    bar_opacities = ["0.16", "0.26", "0.10"]

    cards = []
    for col_pos, (user_idx, bh, medal, opacity) in enumerate(
        zip(display_order, bar_heights, medals, bar_opacities)
    ):
        if user_idx >= len(top3):
            cards.append('<div style="flex:1;"></div>')
            continue
        u  = top3[user_idx]
        gx = gx_values[user_idx] if user_idx < len(gx_values) else 0.0
        cards.append(f"""
<div style="flex:1;text-align:center;padding:8px 4px;">
  <div style="font-size:1.9rem;line-height:1;">{medal}</div>
  <div style="font-weight:700;font-size:.92rem;margin:8px 0 2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{u['display_name']}</div>
  <div style="font-size:.74rem;opacity:.42;margin-bottom:6px;">@{u['nickname']}</div>
  <div style="font-size:1.1rem;font-weight:800;color:#ffd700;">{gx:,.0f}<span style="font-size:.68rem;font-weight:500;opacity:.6;margin-left:2px;">GX</span></div>
  <div style="height:{bh};background:rgba(255,215,0,{opacity});border-radius:6px 6px 0 0;margin-top:8px;"></div>
</div>""")

    return (
        '<div style="display:flex;align-items:flex-end;gap:8px;padding:20px 0 0;">'
        + "".join(cards)
        + "</div>"
    )


# ─── Page entry point ──────────────────────────────────────────────────────────

def render_leaderboard_page():
    st.title("🏆 Leaderboard")

    current_user_id = st.session_state.get("user", {}).get("id")

    user_scores, groups, matchdays = _compute_scores()

    if not user_scores or (not groups and not matchdays):
        st.info("No finished matches yet — check back after kick-off!")
        return

    # ── View toggle ───────────────────────────────────────────────────────────
    if "lb_view" not in st.session_state:
        st.session_state["lb_view"] = "Overall"

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(
            "🌍  Overall", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "Overall" else "secondary",
        ):
            st.session_state["lb_view"] = "Overall"
            st.rerun()
    with c2:
        if st.button(
            "🔤  By Group", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "By Group" else "secondary",
        ):
            st.session_state["lb_view"] = "By Group"
            st.rerun()
    with c3:
        if st.button(
            "📅  By Matchday", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "By Matchday" else "secondary",
        ):
            st.session_state["lb_view"] = "By Matchday"
            st.rerun()

    st.markdown("---")
    view = st.session_state["lb_view"]

    # ── Overall ───────────────────────────────────────────────────────────────
    if view == "Overall":
        st.markdown(
            _overall_table_html(user_scores, current_user_id),
            unsafe_allow_html=True,
        )
        st.caption(
            f"Stake: {_STAKE} GX per match · "
            f"No bet: {_NO_BET_PTS} GX · "
            f"Wrong: {_LOSS_PTS} GX · "
            f"Correct: {_STAKE} × odds GX"
        )

    # ── By Group ──────────────────────────────────────────────────────────────
    elif view == "By Group":
        if not groups:
            st.info("No finished group matches yet.")
            return
        sel = st.pills("Select group", groups, default=groups[0], key="lb_grp")
        if sel:
            top3 = sorted(user_scores, key=lambda u: -u["by_group"].get(sel, 0.0))[:3]
            gx_vals = [u["by_group"].get(sel, 0.0) for u in top3]
            st.markdown(_podium_html(top3, gx_vals), unsafe_allow_html=True)

    # ── By Matchday ───────────────────────────────────────────────────────────
    elif view == "By Matchday":
        if not matchdays:
            st.info("No finished matches yet.")
            return
        sel = st.pills("Select matchday", matchdays, default=matchdays[0], key="lb_md")
        if sel:
            top3 = sorted(user_scores, key=lambda u: -u["by_matchday"].get(sel, 0.0))[:3]
            gx_vals = [u["by_matchday"].get(sel, 0.0) for u in top3]
            st.markdown(_podium_html(top3, gx_vals), unsafe_allow_html=True)
