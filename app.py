import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from auth import get_connection, logout_user, require_login, update_account

load_dotenv()

st.set_page_config(
    page_title="FIFA 2026 Guessing Game",
    page_icon="⚽",
    layout="wide",
)

require_login()

# ─────────────────────────────────────────────────────────────────────────────
# Database helpers (app-level tables)
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id          SERIAL PRIMARY KEY,
                    match_name  TEXT NOT NULL,
                    home_odds   NUMERIC NOT NULL,
                    draw_odds   NUMERIC NOT NULL,
                    away_odds   NUMERIC NOT NULL,
                    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tips (
                    id               SERIAL PRIMARY KEY,
                    user_name        TEXT NOT NULL,
                    match_name       TEXT NOT NULL,
                    selected_outcome TEXT NOT NULL,
                    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
        conn.commit()


def load_odds() -> pd.DataFrame:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, match_name, home_odds, draw_odds, away_odds, fetched_at
                FROM odds_snapshots
                ORDER BY fetched_at DESC
                LIMIT 20;
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def load_tips() -> pd.DataFrame:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_name, match_name, selected_outcome, submitted_at
                FROM tips
                ORDER BY submitted_at DESC
                LIMIT 20;
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def insert_tip(user_name: str, match_name: str, selected_outcome: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tips (user_name, match_name, selected_outcome, submitted_at)
                VALUES (%s, %s, %s, %s);
            """, (user_name, match_name, selected_outcome, datetime.now(timezone.utc)))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main UI  (only reached when authenticated)
# ─────────────────────────────────────────────────────────────────────────────

current_user = st.session_state["user"]

with st.sidebar:
    st.markdown(f"**{current_user['display_name']}**")
    st.caption(f"@{current_user['nickname']}")
    st.divider()

    with st.expander("Edit Account"):
        with st.form("edit_account_form"):
            first_name = st.text_input("First Name", value=current_user["first_name"])
            last_name = st.text_input("Last Name", value=current_user["last_name"])
            nickname = st.text_input("Nickname", value=current_user["nickname"])
            st.markdown("**Change password** — leave blank to keep current")
            cur_pw = st.text_input("Current Password", type="password")
            new_pw = st.text_input("New Password", type="password")
            new_pw2 = st.text_input("Confirm New Password", type="password")
            save = st.form_submit_button("Save Changes", width="stretch")

        if save:
            errors = []
            if not first_name.strip():
                errors.append("First name is required.")
            if not last_name.strip():
                errors.append("Last name is required.")
            if not nickname.strip():
                errors.append("Nickname is required.")
            if new_pw and new_pw != new_pw2:
                errors.append("New passwords do not match.")
            if new_pw and not cur_pw:
                errors.append("Enter your current password to set a new one.")

            if errors:
                for e in errors:
                    st.error(e)
            else:
                ok, msg = update_account(
                    current_nickname=current_user["nickname"],
                    first_name=first_name.strip(),
                    last_name=last_name.strip(),
                    new_nickname=nickname.strip(),
                    current_password=cur_pw,
                    new_password=new_pw,
                )
                if ok:
                    # Keep session in sync with the saved values
                    st.session_state["user"]["first_name"] = first_name.strip()
                    st.session_state["user"]["last_name"] = last_name.strip()
                    st.session_state["user"]["nickname"] = nickname.strip().lower()
                    st.session_state["user"]["display_name"] = (
                        f"{first_name.strip()} {last_name.strip()}"
                    )
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()
    if st.button("Log Out", width="stretch"):
        logout_user()

st.title("⚽ FIFA 2026 — Office Guessing Game")
st.caption("Dummy deployment — Streamlit + Supabase + GitHub Actions confirmed working.")

init_db()

left, right = st.columns([2, 1])

with left:
    st.subheader("Latest dummy odds")
    odds_df = load_odds()
    if odds_df.empty:
        st.info("No odds yet — run the GitHub Action or wait for the cron job.")
    else:
        st.dataframe(odds_df, width="stretch")

with right:
    st.subheader("Submit a dummy tip")
    with st.form("tip_form"):
        st.text_input("Player", value=current_user["display_name"], disabled=True)
        match_name = st.text_input("Match", value="Prague FC vs Brno United")
        selected_outcome = st.selectbox("Your tip", ["Home win", "Draw", "Away win"])
        tip_submitted = st.form_submit_button("Submit tip", width="stretch")
    if tip_submitted:
        insert_tip(current_user["nickname"], match_name, selected_outcome)
        st.success("Tip submitted!")
        st.rerun()

st.divider()

st.subheader("Latest submitted tips")
tips_df = load_tips()
if tips_df.empty:
    st.info("No tips submitted yet.")
else:
    st.dataframe(tips_df, width="stretch")
