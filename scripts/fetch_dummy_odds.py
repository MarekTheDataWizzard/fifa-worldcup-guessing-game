import os
import random
from datetime import datetime, timezone

import psycopg2


DATABASE_URL = os.environ["DATABASE_URL"]


DUMMY_MATCHES = [
    "Prague FC vs Brno United",
    "Galytix City vs Data Science FC",
    "Python Athletic vs Streamlit Rovers",
    "Supabase Town vs GitHub Wanderers",
]


def get_connection():
    return psycopg2.connect(DATABASE_URL)


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
        conn.commit()


def insert_dummy_odds():
    match_name = random.choice(DUMMY_MATCHES)

    home_odds = round(random.uniform(1.4, 4.5), 2)
    draw_odds = round(random.uniform(2.8, 4.2), 2)
    away_odds = round(random.uniform(1.4, 4.5), 2)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO odds_snapshots (
                    match_name,
                    home_odds,
                    draw_odds,
                    away_odds,
                    fetched_at
                )
                VALUES (%s, %s, %s, %s, %s);
                """,
                (
                    match_name,
                    home_odds,
                    draw_odds,
                    away_odds,
                    datetime.now(timezone.utc),
                ),
            )
        conn.commit()

    print(
        f"Inserted odds for {match_name}: "
        f"home={home_odds}, draw={draw_odds}, away={away_odds}"
    )


if __name__ == "__main__":
    init_db()
    insert_dummy_odds()