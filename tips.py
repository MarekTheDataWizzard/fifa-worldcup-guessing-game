import streamlit as st

from auth import get_connection


@st.cache_resource
def init_tips_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Inspect current columns (empty set = table does not exist)
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'tips';
            """)
            cols = {row[0] for row in cur.fetchall()}

            if cols and ("user_name" in cols or "user_id" not in cols):
                # Legacy or corrupted schema — drop and rebuild
                cur.execute("DROP TABLE IF EXISTS tips;")
                cols = set()

            if not cols:
                # Fresh creation — includes the UNIQUE constraint inline
                cur.execute("""
                    CREATE TABLE tips (
                        id           SERIAL PRIMARY KEY,
                        user_id      INT  NOT NULL,
                        match_id     TEXT NOT NULL,
                        tip          TEXT NOT NULL CHECK (tip IN ('1','X','2')),
                        odds         NUMERIC(5,2),
                        submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (user_id, match_id)
                    );
                """)
            else:
                # Table already has user_id — just clean garbage rows and ensure index
                cur.execute("DELETE FROM tips WHERE user_id = 0 OR match_id = '';")
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS tips_user_match_idx
                    ON tips (user_id, match_id);
                """)
        conn.commit()


def submit_tip(user_id: int, match_id: str, tip: str, odds: float | None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tips (user_id, match_id, tip, odds)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, match_id) DO UPDATE SET
                    tip          = EXCLUDED.tip,
                    odds         = EXCLUDED.odds,
                    submitted_at = NOW();
            """, (user_id, match_id, tip, odds))
        conn.commit()


def cancel_tip(user_id: int, match_id: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tips WHERE user_id = %s AND match_id = %s;",
                (user_id, match_id),
            )
        conn.commit()


@st.cache_data(ttl=30)
def get_user_tips(user_id: int) -> dict:
    """Returns {match_id: {"tip": "1"/"X"/"2", "odds": float|None, "at": datetime}}"""
    result: dict = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT match_id, tip, odds, submitted_at FROM tips WHERE user_id = %s;",
                    (user_id,),
                )
                for row in cur.fetchall():
                    result[str(row[0])] = {
                        "tip":  row[1],
                        "odds": float(row[2]) if row[2] else None,
                        "at":   row[3],
                    }
    except Exception:
        pass
    return result
