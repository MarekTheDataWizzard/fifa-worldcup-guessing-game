import streamlit as st

from auth import get_all_users, get_connection
from matches import fetch_matches, _MULTIPLIERS
from odds import get_all_match_odds

# ─── Scoring parameters — edit here to recalculate everything ─────────────────
_STAKE      = 100  # GX allocated per match
_NO_BET_PTS = 70   # GX when no tip placed (before multiplier)
_LOSS_PTS   = 0    # GX on wrong tip
# Correct tip earns: round(_STAKE * odds * multiplier, 2)
# No tip earns:      round(_NO_BET_PTS * multiplier, 2)

_PHASE_LABELS = {
    "r32":   "Round of 32",
    "r16":   "Round of 16",
    "qf":    "Quarter-final",
    "sf":    "Semi-final",
    "third": "3rd Place",
    "final": "Final",
}
_PHASE_ORDER = ["group", "r32", "r16", "qf", "sf", "third", "final"]

_EUROPE_NICKNAMES = {
    "Esteban", "MarekTheFirst", "Katka", "Katerina", "Jirka", "ondrej",
    "Mauricio", "Rust", "sasha", "CzechMate", "gortibaldik", "ozcan", "Ozcan", "pacaklu",
}

def _location(nickname: str) -> str:
    return "Europe" if nickname in _EUROPE_NICKNAMES else "India"


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
def _compute_scores() -> tuple[list[dict], list[str]]:
    """
    Returns (user_scores, matchdays_ordered).
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

    # Collect ordered unique matchdays from finished matches only
    matchdays_ordered: list[str] = []
    seen_m: set[str] = set()
    for m in finished:
        _, md = _match_bucket(m)
        if md not in seen_m: seen_m.add(md); matchdays_ordered.append(md)

    user_scores = []
    for u in users:
        uid          = u["id"]
        display_name = f"{u['first_name']} {u['last_name']}".strip() or u["nickname"]
        total_gx     = 0.0
        net_gx       = 0.0
        bet_gx       = 0.0
        no_bet_gx    = 0.0
        stake_gx     = 0.0
        bets         = 0
        by_matchday: dict[str, float] = {}

        for m in finished:
            mid     = str(m["id"])
            _, md   = _match_bucket(m)
            outcome = _outcome(m.get("home_score_90", m["home_score"]),
                              m.get("away_score_90", m["away_score"]))
            tip     = tips_idx.get((uid, mid))
            mult    = _MULTIPLIERS.get(m["type"], 1)

            if tip is None:
                gx         = float(_NO_BET_PTS * mult)
                net        = 0.0
                no_bet_gx += gx
            elif tip["tip"] == outcome:
                rates      = match_rates.get(mid)
                rate       = (rates.get(tip["tip"]) if rates else None) or tip["odds"] or 1.0
                gx         = round(_STAKE * rate * mult, 2)
                net        = round(gx - _STAKE * mult, 2)
                bet_gx    += gx
                stake_gx  += _STAKE * mult
                bets      += 1
            else:
                gx         = float(_LOSS_PTS)
                net        = float(-_STAKE * mult)
                stake_gx  += _STAKE * mult
                bets      += 1

            total_gx        += gx
            net_gx          += net
            by_matchday[md]  = round(by_matchday.get(md, 0.0) + gx, 2)

        user_scores.append({
            "id":           uid,
            "display_name": display_name,
            "nickname":     u["nickname"],
            "total_gx":     round(total_gx, 2),
            "net_gx":       round(net_gx, 2),
            "bet_gx":       round(bet_gx, 2),
            "no_bet_gx":    round(no_bet_gx, 2),
            "stake_gx":     round(stake_gx, 2),
            "bets":         bets,
            "by_matchday":  by_matchday,
        })

    user_scores.sort(key=lambda x: -x["total_gx"])
    return user_scores, matchdays_ordered


# ─── Rendering helpers ─────────────────────────────────────────────────────────

def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")


def _net_html(net: float) -> str:
    if net > 0:
        return f'<span style="color:#4caf50;">+{net:,.0f}</span>'
    if net < 0:
        return f'<span style="color:#f44336;">{net:,.0f}</span>'
    return '<span style="opacity:.35;">0</span>'


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


def _detailed_table_html(rows: list[dict], current_user_id: int | None = None) -> str:
    trs = []
    for u in rows:
        is_me = u["id"] == current_user_id
        bg    = "background:rgba(255,215,0,0.06);" if is_me else ""
        trs.append(f"""
<tr style="border-bottom:1px solid rgba(128,128,128,0.1);{bg}">
  <td style="padding:10px 8px;font-weight:600;white-space:nowrap;">{u['display_name']}{"&nbsp;⭐" if is_me else ""}</td>
  <td style="padding:10px 8px;opacity:.42;font-size:.82rem;white-space:nowrap;">@{u['nickname']}</td>
  <td style="padding:10px 12px;text-align:right;font-size:.85rem;white-space:nowrap;">{_net_html(u['net_gx'])}</td>
  <td style="padding:10px 12px;text-align:right;font-size:.85rem;white-space:nowrap;color:#ffd700;">{u['bet_gx']:,.0f}</td>
  <td style="padding:10px 12px;text-align:right;font-size:.85rem;white-space:nowrap;opacity:.6;">{u['no_bet_gx']:,.0f}</td>
  <td style="padding:10px 12px;text-align:right;font-size:.85rem;opacity:.5;">{u['bets']}</td>
  <td style="padding:10px 12px;text-align:right;font-size:.85rem;opacity:.5;white-space:nowrap;">{u['stake_gx']:,.0f}</td>
</tr>""")
    return f"""
<table style="width:100%;border-collapse:collapse;">
<thead><tr style="border-bottom:1px solid rgba(128,128,128,0.25);">
  <th style="padding:8px 8px;opacity:.42;text-align:left;font-weight:500;font-size:.8rem;" colspan="2">Player</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">+/−</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">GX (bets)</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">GX (no bet)</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;"># Bets</th>
  <th style="padding:8px 12px;opacity:.42;text-align:right;font-weight:500;font-size:.8rem;">Staked</th>
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

    try:
        user_scores, matchdays = _compute_scores()
    except Exception:
        st.error("Could not load leaderboard — the data provider is temporarily unavailable. Please refresh in a moment.")
        return

    if not user_scores or not matchdays:
        st.info("No finished matches yet — check back after kick-off!")
        return

    # ── View toggle ───────────────────────────────────────────────────────────
    if "lb_view" not in st.session_state:
        st.session_state["lb_view"] = "Overall"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button(
            "🌍  Overall", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "Overall" else "secondary",
        ):
            st.session_state["lb_view"] = "Overall"
            st.rerun()
    with c2:
        if st.button(
            "🗺️  Geographic", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "Geographic" else "secondary",
        ):
            st.session_state["lb_view"] = "Geographic"
            st.rerun()
    with c3:
        if st.button(
            "📅  By Matchday", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "By Matchday" else "secondary",
        ):
            st.session_state["lb_view"] = "By Matchday"
            st.rerun()
    with c4:
        if st.button(
            "📊  Detailed", use_container_width=True,
            type="primary" if st.session_state["lb_view"] == "Detailed" else "secondary",
        ):
            st.session_state["lb_view"] = "Detailed"
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
            f"Stake: {_STAKE} GX · No bet: {_NO_BET_PTS} GX · Wrong: {_LOSS_PTS} GX · "
            f"Correct: {_STAKE} × odds GX · "
            f"Multipliers: R16 ×2 · QF ×4 · SF ×8 · 3rd ×12 · Final ×16"
        )

    # ── Geographic ────────────────────────────────────────────────────────────
    elif view == "Geographic":
        region = st.pills("Region", ["Europe", "India"], default="Europe", key="lb_geo")
        if region:
            filtered = [u for u in user_scores if _location(u["nickname"]) == region]
            filtered_sorted = sorted(filtered, key=lambda u: -u["total_gx"])
            top3    = filtered_sorted[:3]
            gx_vals = [u["total_gx"] for u in top3]
            st.markdown(_podium_html(top3, gx_vals), unsafe_allow_html=True)
            if len(filtered_sorted) > 3:
                st.markdown("---")
                st.markdown(
                    _overall_table_html(filtered_sorted, current_user_id),
                    unsafe_allow_html=True,
                )

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

    # ── Detailed ──────────────────────────────────────────────────────────────
    elif view == "Detailed":
        st.markdown(
            _detailed_table_html(user_scores, current_user_id),
            unsafe_allow_html=True,
        )
        st.caption(
            "+/− = GX earned from bets − GX staked (no-tip matches excluded) · "
            "Staked = 100 GX × round multiplier per bet placed"
        )
