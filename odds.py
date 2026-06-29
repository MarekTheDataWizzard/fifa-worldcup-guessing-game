import os
from datetime import datetime, timedelta, timezone
from statistics import mean

import requests
import streamlit as st

from auth import get_connection

_API_BASE    = "https://api.the-odds-api.com/v4"
_SPORT_KEY   = "soccer_fifa_world_cup"

# Normalise known API name differences → our canonical names
_ALIASES: dict[str, str] = {
    "usa":                                    "United States",
    "united states of america":               "United States",
    "korea republic":                         "South Korea",
    "republic of korea":                      "South Korea",
    "ivory coast":                            "Ivory Coast",   # rezarahiminia uses "Ivory Coast"
    "cote d'ivoire":                          "Ivory Coast",
    "côte d'ivoire":                          "Ivory Coast",
    "ir iran":                                "Iran",           # rezarahiminia uses "Iran"
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


def _api_key() -> str:
    try:
        k = st.secrets.get("ODDS_API_KEY")
        if k:
            return k
    except Exception:
        pass
    return os.getenv("ODDS_API_KEY", "")


def _norm(name: str) -> str:
    return name.lower().strip()


def _canonical(api_name: str) -> str:
    return _ALIASES.get(_norm(api_name), api_name)


# ─────────────────────────────────────────────────────────────────────────────
# DB setup
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def init_odds_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS match_odds (
                    id             SERIAL PRIMARY KEY,
                    home_team      TEXT NOT NULL,
                    away_team      TEXT NOT NULL,
                    odds_type      TEXT NOT NULL CHECK (odds_type IN ('indicative','final')),
                    home_odds      NUMERIC(5,2),
                    draw_odds      NUMERIC(5,2),
                    away_odds      NUMERIC(5,2),
                    commence_time  TIMESTAMPTZ,
                    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (home_team, away_team, odds_type)
                );
                CREATE TABLE IF NOT EXISTS odds_fetch_log (
                    id                 SERIAL PRIMARY KEY,
                    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    odds_type          TEXT NOT NULL,
                    matches_stored     INT DEFAULT 0,
                    requests_remaining TEXT
                );
            """)
            # Migration: add commence_time if it doesn't exist yet
            cur.execute("""
                ALTER TABLE match_odds
                    ADD COLUMN IF NOT EXISTS commence_time TIMESTAMPTZ;
            """)
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Core fetch
# ─────────────────────────────────────────────────────────────────────────────

def _avg_h2h(match: dict) -> tuple[float | None, float | None, float | None]:
    """Average decimal h2h odds across all bookmakers. Returns (home, draw, away)."""
    home_norm = _norm(match["home_team"])
    away_norm = _norm(match["away_team"])
    h, d, a = [], [], []

    for bm in match.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market["key"] != "h2h":
                continue
            draw_p = home_p = away_p = None
            for outcome in market.get("outcomes", []):
                n = _norm(outcome["name"])
                p = outcome["price"]
                if n == "draw":
                    draw_p = p
                elif n == home_norm:
                    home_p = p
                elif n == away_norm:
                    away_p = p
            if draw_p and home_p and away_p:
                d.append(draw_p)
                h.append(home_p)
                a.append(away_p)

    if h and d and a:
        return round(mean(h), 2), round(mean(d), 2), round(mean(a), 2)
    return None, None, None


def fetch_and_store_odds(odds_type: str = "indicative") -> dict:
    """
    One API call → fetch all upcoming FIFA WC odds → store in DB.
    Returns {"stored": n, "remaining": str, "error": str | None}.
    """
    key = _api_key()
    if not key:
        return {"error": "ODDS_API_KEY not configured."}

    try:
        resp = requests.get(
            f"{_API_BASE}/sports/{_SPORT_KEY}/odds",
            params={
                "apiKey":     key,
                "regions":    "eu",
                "markets":    "h2h",
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
    except Exception as exc:
        return {"error": str(exc)}

    remaining = resp.headers.get("x-requests-remaining", "?")
    data      = resp.json()

    now_utc = datetime.now(tz=timezone.utc)
    stored = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for match in data:
                home = _canonical(match["home_team"])
                away = _canonical(match["away_team"])
                ho, dr, aw = _avg_h2h(match)
                if ho is None:
                    continue
                ct_raw = match.get("commence_time")
                commence_time = (
                    datetime.fromisoformat(ct_raw.replace("Z", "+00:00"))
                    if ct_raw else None
                )
                # Final odds only for matches kicking off within 6 hours
                if odds_type == "final" and commence_time:
                    delta_min = (commence_time - now_utc).total_seconds() / 60
                    if not (0 <= delta_min <= 360):
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
                """, (home, away, odds_type, ho, dr, aw, commence_time))
                stored += 1

            cur.execute("""
                INSERT INTO odds_fetch_log (odds_type, matches_stored, requests_remaining)
                VALUES (%s, %s, %s);
            """, (odds_type, stored, str(remaining)))
        conn.commit()

    # Clear cache so callers see fresh data immediately
    get_all_match_odds.clear()

    return {"stored": stored, "remaining": remaining, "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# Read odds
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_all_match_odds() -> dict:
    """
    Returns {(home_team, away_team): {"indicative": {...}, "final": {...}}}.
    Cached 5 min — cleared after every fetch_and_store_odds call.
    """
    result: dict = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT home_team, away_team, odds_type,
                           home_odds, draw_odds, away_odds, fetched_at
                    FROM match_odds ORDER BY fetched_at;
                """)
                for row in cur.fetchall():
                    key = (row[0], row[1])
                    result.setdefault(key, {})[row[2]] = {
                        "home": float(row[3]) if row[3] else None,
                        "draw": float(row[4]) if row[4] else None,
                        "away": float(row[5]) if row[5] else None,
                        "at":   row[6],
                    }
    except Exception:
        pass
    return result


def get_last_fetch_info() -> dict:
    """Returns info about the most recent indicative and final fetches."""
    info: dict = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (odds_type)
                        odds_type, fetched_at, matches_stored, requests_remaining
                    FROM odds_fetch_log
                    ORDER BY odds_type, fetched_at DESC;
                """)
                for row in cur.fetchall():
                    info[row[0]] = {
                        "at":        row[1],
                        "stored":    row[2],
                        "remaining": row[3],
                    }
    except Exception:
        pass
    return info


# ─────────────────────────────────────────────────────────────────────────────
# Auto final-odds fetch
# ─────────────────────────────────────────────────────────────────────────────

def maybe_fetch_final_odds(_matches: list[dict]) -> bool:
    """
    If any match kicks off within 90 min (by UTC commence_time stored in DB)
    and lacks final odds, fetch once. Throttled to once per 30 min.
    Returns True if a fetch was triggered.
    GitHub Actions is the primary driver; this is a best-effort in-app fallback.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Use UTC commence_time stored from the odds API — avoids
                # the venue-local-time vs server-UTC mismatch in match data
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
                if not cur.fetchone():
                    return False

                # Throttle: skip if we already fetched final odds in last 30 min
                cur.execute("""
                    SELECT fetched_at FROM odds_fetch_log
                    WHERE odds_type = 'final'
                    ORDER BY fetched_at DESC LIMIT 1;
                """)
                row = cur.fetchone()
                if row:
                    ts = row[0]
                    now_cmp = datetime.now(tz=ts.tzinfo) if ts.tzinfo else datetime.now()
                    if (now_cmp - ts) < timedelta(minutes=30):
                        return False
    except Exception:
        return False

    fetch_and_store_odds("final")
    return True
