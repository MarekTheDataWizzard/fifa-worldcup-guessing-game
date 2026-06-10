import streamlit as st
from auth import get_all_users
from odds import fetch_and_store_odds, get_last_fetch_info


def render_admin_page():
    st.title("👑 Admin")

    tab_users, tab_odds = st.tabs(["Users", "Odds"])

    # ── Users tab ─────────────────────────────────────────────────────────────
    with tab_users:
        users = get_all_users()

        if not users:
            st.info("No users found.")
        else:
            st.caption(f"{len(users)} registered account{'s' if len(users) != 1 else ''}")
            for u in users:
                with st.container(border=True):
                    col_name, col_nick, col_joined, col_badge = st.columns([3, 2, 2, 1])
                    with col_name:
                        st.markdown(f"**{u['first_name']} {u['last_name']}**")
                    with col_nick:
                        st.caption(f"@{u['nickname']}")
                    with col_joined:
                        if u["created_at"]:
                            st.caption(u["created_at"].strftime("Joined %b %d, %Y"))
                    with col_badge:
                        if u["is_admin"]:
                            st.markdown(
                                "<span style='background:#145c2c;color:#fff;"
                                "border-radius:6px;padding:2px 8px;font-size:0.75rem;"
                                "font-weight:700;'>admin</span>",
                                unsafe_allow_html=True,
                            )

    # ── Odds tab ──────────────────────────────────────────────────────────────
    with tab_odds:
        st.subheader("Odds Management")

        info = get_last_fetch_info()

        col_ind, col_fin = st.columns(2)
        with col_ind:
            with st.container(border=True):
                st.markdown("**Indicative odds**")
                if "indicative" in info:
                    i = info["indicative"]
                    st.caption(
                        f"{i['stored']} matches stored\n\n"
                        f"Last fetch: {i['at'].strftime('%b %d %H:%M') if i['at'] else '—'}\n\n"
                        f"API requests left: {i['remaining']}"
                    )
                else:
                    st.caption("Not fetched yet")
                if st.button("Fetch Indicative Odds", width="stretch"):
                    with st.spinner("Fetching from the-odds-api…"):
                        result = fetch_and_store_odds("indicative")
                    if result.get("error"):
                        st.error(result["error"])
                    else:
                        st.success(
                            f"Stored {result['stored']} matches. "
                            f"API requests remaining: {result['remaining']}"
                        )
                        st.rerun()

        with col_fin:
            with st.container(border=True):
                st.markdown("**Final odds**")
                if "final" in info:
                    f = info["final"]
                    st.caption(
                        f"{f['stored']} matches stored\n\n"
                        f"Last fetch: {f['at'].strftime('%b %d %H:%M') if f['at'] else '—'}\n\n"
                        f"API requests left: {f['remaining']}"
                    )
                else:
                    st.caption("Auto-fetched 1 h before each match")
                if st.button("Fetch Final Odds Now", width="stretch"):
                    with st.spinner("Fetching from the-odds-api…"):
                        result = fetch_and_store_odds("final")
                    if result.get("error"):
                        st.error(result["error"])
                    else:
                        st.success(
                            f"Stored {result['stored']} matches. "
                            f"API requests remaining: {result['remaining']}"
                        )
                        st.rerun()
