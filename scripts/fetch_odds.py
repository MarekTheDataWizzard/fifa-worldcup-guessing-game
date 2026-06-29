"""
Fetch FIFA World Cup odds from the-odds-api.com and store in Supabase.

Usage (called by GitHub Actions):
    python scripts/fetch_odds.py --mode final        # 1 h before kickoff
    python scripts/fetch_odds.py --mode indicative   # manual / daily snapshot

Token strategy
--------------
* --mode final:  queries the DB first (no API call).
                 Only calls the API if a match with a stored commence_time
                 kicks off within the next 90 minutes AND has no final odds yet.
* --mode indicative: always calls the API (intended for occasional admin use).

One API request returns odds for ALL upcoming matches, so the cost is always
exactly 1 request regardless of how many matches are in the response.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean

import psycopg2
import requests

DATABASE_URL = os.environ.get("DATABASE_URL")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
API_BASE     = "https://api.the-odds-api.com/v4"
SPORT        = "soccer_fifa_world_cup"

# Known name mismatches between the odds API and our dataset
ALIASES: dict[str, str] = {
    "usa":                                    "United States",
    "united states of america":               "United States",
    "korea republic":                         "South Korea",
    "republic of korea":                      "South Korea",
    "ivory coast":                            "Ivory Coast",
    "cote d'ivoire":                          "Ivory Coast",
    "côte d'ivoire":                          "Ivory Coast",
    "ir iran":                                "Iran",
    "cape verde islands":                     "Cabo Verde",
    "guinea bissau":                          "Guinea-Bissau",
    "trinidad & tobago":                      "Trinidad & Tobago",
    "trinidad and tobago":                    "Trinidad & Tobago",
    "bosnia & herzegovina":                   "Bosnia and Herzegovina",
    "dr congo":                               "Democratic Republic of the Congo",
    "democratic republic of congo":           "Democratic Republic of the Congo",
    "democratic republic of the congo":       "Democratic Republic of the Congo",
    "czechia":                                "Czech Republic",
}


def norm(name: str) -> str:
    return name.lower().strip()


def canonical(name: str) -> str:
    return ALIASES.get(norm(name), name)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def avg_h2h(match: dict) -> tuple[float | None, float | None, float | None]:
    home_n = norm(match["home_team"])
    away_n = norm(match["away_team"])
    h, d, a = [], [], []
    for bm in match.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] != "h2h":
                continue
            draw_p = home_p = away_p = None
            for o in mkt.get("outcomes", []):
                n, p = norm(o["name"]), o["price"]
                if n == "draw":
                    draw_p = p
                elif n == home_n:
                    home_p = p
                elif n == away_n:
                    away_p = p
            if draw_p and home_p and away_p:
                d.append(draw_p); h.append(home_p); a.append(away_p)
    if h and d and a:
        return round(mean(h), 2), round(mean(d), 2), round(mean(a), 2)
    return None, None, None


def ensure_schema():
    """Add commence_time column if it was created before this migration."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE match_odds
                    ADD COLUMN IF NOT EXISTS commence_time TIMESTAMPTZ;
            """)
        conn.commit()


def needs_final_fetch() -> bool:
    """
    Return True if any match stored with commence_time kicks off within
    the next 90 minutes and still has no final odds row.
    Pure DB query — no API call.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM match_odds
                    WHERE odds_type = 'indicative'
                      AND commence_time IS NOT NULL
                      AND commence_time - NOW() BETWEEN INTERVAL '0 minutes'
                                                    AND INTERVAL '360 minutes'
                      AND NOT EXISTS (
                          SELECT 1 FROM match_odds f
                          WHERE f.home_team = match_odds.home_team
                            AND f.away_team = match_odds.away_team
                            AND f.odds_type = 'final'
                      )
                    LIMIT 1;
                """)
                return cur.fetchone() is not None
    except Exception as exc:
        print(f"DB pre-check failed: {exc}", file=sys.stderr)
        return False


def fetch_and_store(odds_type: str) -> int:
    """
    Call the odds API and upsert results.  Returns the number of rows stored.
    For 'final' odds, only rows whose commence_time is within 90 minutes are
    written (others are skipped to keep 'final' semantically correct).
    """
    resp = requests.get(
        f"{API_BASE}/sports/{SPORT}/odds",
        params={"apiKey": ODDS_API_KEY, "regions": "eu",
                "markets": "h2h", "oddsFormat": "decimal"},
        timeout=15,
    )
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining", "?")
    data      = resp.json()
    now_utc   = datetime.now(timezone.utc)

    stored = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for match in data:
                home = canonical(match["home_team"])
                away = canonical(match["away_team"])
                ho, dr, aw = avg_h2h(match)
                if ho is None:
                    continue

                ct_raw = match.get("commence_time")
                ct = datetime.fromisoformat(ct_raw.replace("Z", "+00:00")) if ct_raw else None

                # For final odds, only store matches kicking off within 90 min
                if odds_type == "final" and ct:
                    delta = (ct - now_utc).total_seconds() / 60
                    if not (0 <= delta <= 360):
                        continue

                cur.execute("""
                    INSERT INTO match_odds
                        (home_team, away_team, odds_type,
                         home_odds, draw_odds, away_odds, commence_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (home_team, away_team, odds_type) DO UPDATE SET
                        home_odds     = EXCLUDED.home_odds,
                        draw_odds     = EXCLUDED.draw_odds,
                        away_odds     = EXCLUDED.away_odds,
                        commence_time = EXCLUDED.commence_time,
                        fetched_at    = NOW();
                """, (home, away, odds_type, ho, dr, aw, ct))
                stored += 1

            cur.execute("""
                INSERT INTO odds_fetch_log (odds_type, matches_stored, requests_remaining)
                VALUES (%s, %s, %s);
            """, (odds_type, stored, str(remaining)))
        conn.commit()

    print(f"[{odds_type}] stored={stored}  requests_remaining={remaining}")
    return stored


def main():
    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL env var is not set.")
    if not ODDS_API_KEY:
        sys.exit("ERROR: ODDS_API_KEY env var is not set.")

    ensure_schema()

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["indicative", "final"], required=True)
    args = parser.parse_args()

    if args.mode == "final":
        if not needs_final_fetch():
            print("No match within 90 minutes that needs final odds — skipping API call.")
            return
        fetch_and_store("final")
    else:
        fetch_and_store("indicative")


if __name__ == "__main__":
    main()
