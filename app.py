import os
from datetime import datetime, timezone

import pandas as pd
import psycopg2
import streamlit as st


st.set_page_config(
    page_title="Office Tips Demo",
    page_icon="⚽",
    layout="wide",
)


def get_database_url() -> str:
    """
    Works both locally and on Streamlit Cloud.

    Local:
    - use environment variable DATABASE_URL

    Streamlit Cloud:
    - use st.secrets["DATABASE_URL"]
    """
    if "DATABASE_URL" in st.secrets:
        return st.secrets["DATABASE_URL"]

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    st.error("DATABASE_URL is missing. Add it to Streamlit secrets or local environment variables.")
    st.stop()


def get_connection():
    return psycopg2.connect(get_database_url())


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id SERIAL PRIMARY KEY,
                    match_name TEXT NOT NULL,
                    home_odds NUMERIC NOT NULL,
                    draw_odds NUMERIC NOT NULL,
                    away_odds NUMERIC NOT NULL,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tips (
                    id SERIAL PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    match_name TEXT NOT NULL,
                    selected_outcome TEXT NOT NULL,
                    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

        conn.commit()


def load_odds() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            """
            SELECT
                id,
                match_name,
                home_odds,
                draw_odds,
                away_odds,
                fetched_at
            FROM odds_snapshots
            ORDER BY fetched_at DESC
            LIMIT 20;
            """,
            conn,
        )


def load_tips() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            """
            SELECT
                id,
                user_name,
                match_name,
                selected_outcome,
                submitted_at
            FROM tips
            ORDER BY submitted_at DESC
            LIMIT 20;
            """,
            conn,
        )


def insert_tip(user_name: str, match_name: str, selected_outcome: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tips (
                    user_name,
                    match_name,
                    selected_outcome,
                    submitted_at
                )
                VALUES (%s, %s, %s, %s);
                """,
                (
                    user_name,
                    match_name,
                    selected_outcome,
                    datetime.now(timezone.utc),
                ),
            )
        conn.commit()


st.title("⚽ Office Tips Demo")
st.caption("Dummy deployment proving Streamlit + Supabase + GitHub Actions works.")

init_db()

left, right = st.columns([2, 1])

with left:
    st.subheader("Latest dummy odds")

    odds_df = load_odds()

    if odds_df.empty:
        st.info("No odds yet. Run the GitHub Action once, or wait for the cron.")
    else:
        st.dataframe(odds_df, use_container_width=True)

with right:
    st.subheader("Submit a dummy tip")

    with st.form("tip_form"):
        user_name = st.text_input("Your name", value="Marek")
        match_name = st.text_input("Match", value="Prague FC vs Brno United")
        selected_outcome = st.selectbox(
            "Your tip",
            ["Home win", "Draw", "Away win"],
        )

        submitted = st.form_submit_button("Submit tip")

        if submitted:
            insert_tip(user_name, match_name, selected_outcome)
            st.success("Tip submitted.")
            st.rerun()

st.divider()

st.subheader("Latest submitted tips")

tips_df = load_tips()

if tips_df.empty:
    st.info("No tips submitted yet.")
else:
    st.dataframe(tips_df, use_container_width=True)