import os
import secrets
from contextlib import contextmanager

import bcrypt
import psycopg2
import psycopg2.pool
import streamlit as st

_AUTH_CSS = """
<style>
/* ── Hide Streamlit chrome ───────────────────────────────── */
#MainMenu                    { visibility: hidden; }
footer                       { visibility: hidden; }
[data-testid="stHeader"]     { display: none !important; }
[data-testid="stToolbar"]    { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }

/* ── Constrain & centre the auth card ───────────────────── */
/* No explicit page background — inherits Streamlit's dark theme so the
   auth→app transition is dark-to-dark and the white card doesn't cause a
   full-page white flash on rerun. */
.block-container {
    max-width: 460px !important;
    padding-top:    0.75rem !important;
    padding-bottom: 2rem    !important;
    padding-left:   1.25rem !important;
    padding-right:  1.25rem !important;
    margin-left:  auto !important;
    margin-right: auto !important;
}

/* ── Card around the tab panel (white card on dark background) ─── */
[data-testid="stTabs"] {
    background: #ffffff;
    border-radius: 16px;
    padding: 1.25rem 1.5rem 2rem;
    box-shadow: 0 4px 32px rgba(0, 0, 0, 0.45);
    margin-top: 0.5rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
}

/* ── Segmented tab bar ───────────────────────────────────── */
[data-baseweb="tab-list"] {
    background: #f0f2f5 !important;
    border-radius: 10px !important;
    padding: 4px !important;
    gap: 4px !important;
    border-bottom: none !important;
    margin-bottom: 1rem !important;
}
[data-baseweb="tab"] {
    border-radius: 7px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    color: #666 !important;
    padding: 0.45rem 1.1rem !important;
    flex: 1 !important;
    justify-content: center !important;
    transition: background 0.15s, color 0.15s !important;
}
[aria-selected="true"][data-baseweb="tab"] {
    background: #ffffff !important;
    color: #145c2c !important;
    box-shadow: 0 1px 6px rgba(0, 0, 0, 0.13) !important;
}
[data-baseweb="tab-highlight"] {
    display: none !important;
}
[data-baseweb="tab-border"] {
    display: none !important;
}

/* ── Field labels ────────────────────────────────────────── */
.stTextInput label p {
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    color: #444 !important;
}

/* ── Text inputs — explicit light background ─────────────── */
.stTextInput > div > div > input {
    background-color: #ffffff !important;
    color: #111111 !important;
    border-radius: 8px !important;
    border: 1.5px solid #d4d4d4 !important;
    font-size: 0.95rem !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
.stTextInput > div > div > input::placeholder {
    color: #aaa !important;
}
.stTextInput > div > div > input:focus {
    border-color: #145c2c !important;
    box-shadow: 0 0 0 3px rgba(20, 92, 44, 0.15) !important;
}

/* ── Submit buttons ──────────────────────────────────────── */
[data-testid="stFormSubmitButton"] > button {
    background: #145c2c !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    width: 100% !important;
    padding: 0.6rem 1rem !important;
    letter-spacing: 0.03em !important;
    margin-top: 0.6rem !important;
    transition: background 0.2s !important;
}
[data-testid="stFormSubmitButton"] > button:hover  { background: #0e4420 !important; }
[data-testid="stFormSubmitButton"] > button:active { background: #0a3318 !important; }

/* ── Alert messages ──────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 8px !important;
    font-size: 0.88rem !important;
    margin-top: 0.5rem !important;
}

/* ── Suppress anchor links on all headings ───────────────── */
h1 a, h2 a, h3 a, h4 a, h5 a { display: none !important; }

/* ── Hide "Press Enter to submit form" helper text ───────── */
[data-testid="InputInstructions"] { display: none !important; }
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Database connection pool
# ─────────────────────────────────────────────────────────────────────────────

def _get_db_url() -> str:
    try:
        url = st.secrets.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    return os.getenv("DATABASE_URL", "")


@st.cache_resource
def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    url = _get_db_url()
    if not url:
        raise ValueError("DATABASE_URL is not set")
    return psycopg2.pool.ThreadedConnectionPool(1, 8, url)


@contextmanager
def get_connection():
    url = _get_db_url()
    if not url:
        st.error(
            "DATABASE_URL is missing. "
            "Add it to `.streamlit/secrets.toml` or your `.env` file."
        )
        st.stop()
        return

    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def init_auth_db():
    """Create the users table and run migrations. Cached — runs once per server start."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    first_name    TEXT NOT NULL DEFAULT '',
                    last_name     TEXT NOT NULL DEFAULT '',
                    nickname      TEXT UNIQUE NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            for stmt in (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT '';",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name  TEXT NOT NULL DEFAULT '';",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname   TEXT;",
            ):
                cur.execute(stmt)
            cur.execute("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'username'
                    ) THEN
                        ALTER TABLE users ALTER COLUMN username DROP NOT NULL;
                        ALTER TABLE users ALTER COLUMN username SET DEFAULT NULL;
                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name = 'users'
                              AND constraint_name = 'users_username_key'
                        ) THEN
                            ALTER TABLE users DROP CONSTRAINT users_username_key;
                        END IF;
                        UPDATE users SET username = NULL WHERE username = '';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'display_name'
                    ) THEN
                        ALTER TABLE users ALTER COLUMN display_name DROP NOT NULL;
                        ALTER TABLE users ALTER COLUMN display_name SET DEFAULT NULL;
                    END IF;
                END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'username'
                    ) THEN
                        UPDATE users SET nickname = username WHERE nickname IS NULL;
                    END IF;
                END $$;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token      TEXT PRIMARY KEY,
                    user_id    INT  NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days'
                );
            """)
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Auth logic
# ─────────────────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _get_user_by_nickname(nickname: str) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, first_name, last_name, nickname, password_hash, is_admin
                FROM users WHERE nickname = %s;
            """, (nickname,))
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "nickname": row[3],
        "password_hash": row[4],
        "is_admin": row[5],
    }


def _create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_sessions (token, user_id) VALUES (%s, %s);",
                (token, user_id),
            )
        conn.commit()
    return token


def _delete_session(token: str) -> None:
    if not token:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions WHERE token = %s;", (token,))
        conn.commit()


def _restore_session(token: str) -> bool:
    """Return True and populate session_state if the token is valid and unexpired."""
    if not token:
        return False
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT u.id, u.first_name, u.last_name, u.nickname, u.is_admin
                    FROM user_sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token = %s AND s.expires_at > NOW();
                """, (token,))
                row = cur.fetchone()
    except Exception:
        return False
    if row is None:
        return False
    st.session_state["authenticated"] = True
    st.session_state["user"] = {
        "id":           row[0],
        "first_name":   row[1],
        "last_name":    row[2],
        "nickname":     row[3],
        "display_name": f"{row[1]} {row[2]}".strip(),
        "is_admin":     row[4],
    }
    return True


def register_user(
    first_name: str,
    last_name: str,
    nickname: str,
    password: str,
) -> tuple[bool, str]:
    if _get_user_by_nickname(nickname):
        return False, "This nickname is already taken. Please choose a different one."
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (first_name, last_name, nickname, password_hash)
                    VALUES (%s, %s, %s, %s);
                """, (first_name, last_name, nickname, _hash_password(password)))
            conn.commit()
        return True, "Account created successfully."
    except Exception as exc:
        return False, f"Registration failed: {exc}"


def login_user(nickname: str, password: str) -> bool:
    user = _get_user_by_nickname(nickname)
    if user is None or not _verify_password(password, user["password_hash"]):
        return False
    st.session_state["authenticated"] = True
    st.session_state["page"] = "Matches"
    st.session_state["user"] = {
        "id":           user["id"],
        "first_name":   user["first_name"],
        "last_name":    user["last_name"],
        "nickname":     user["nickname"],
        "display_name": f"{user['first_name']} {user['last_name']}".strip(),
        "is_admin":     user["is_admin"],
    }
    token = _create_session(user["id"])
    # Write the session token to the URL immediately, on the same run as the
    # form submission. The subsequent st.rerun() in the login handler will
    # then see authenticated=True and skip the auth page entirely.
    st.query_params["_sid"] = token
    return True


def update_account(
    current_nickname: str,
    first_name: str,
    last_name: str,
    new_nickname: str,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    user = _get_user_by_nickname(current_nickname)
    if user is None:
        return False, "User not found."

    if new_nickname != current_nickname:
        if _get_user_by_nickname(new_nickname):
            return False, "That nickname is already taken. Please choose another."

    new_hash = user["password_hash"]
    if new_password:
        if not _verify_password(current_password, user["password_hash"]):
            return False, "Current password is incorrect."
        if len(new_password) < 6:
            return False, "New password must be at least 6 characters."
        new_hash = _hash_password(new_password)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET first_name = %s, last_name = %s, nickname = %s, password_hash = %s
                    WHERE LOWER(nickname) = LOWER(%s);
                    """,
                    (first_name, last_name, new_nickname, new_hash, current_nickname),
                )
            conn.commit()
        return True, "Account updated successfully."
    except Exception as exc:
        return False, f"Update failed: {exc}"


def get_all_users() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, first_name, last_name, nickname, is_admin, created_at
                FROM users
                ORDER BY created_at;
            """)
            rows = cur.fetchall()
    return [
        {
            "id":         r[0],
            "first_name": r[1],
            "last_name":  r[2],
            "nickname":   r[3],
            "is_admin":   r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def logout_user():
    _delete_session(st.query_params.get("_sid", ""))
    st.session_state.pop("authenticated", None)
    st.session_state.pop("user", None)
    st.query_params.clear()
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Auth page UI
# ─────────────────────────────────────────────────────────────────────────────

def show_auth_page():
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)

    st.markdown("""
        <div style="text-align:center; padding:1.5rem 0 1.25rem;">
            <div style="font-size:3.25rem; line-height:1.1;">⚽</div>
            <div style="
                color: rgba(255,255,255,0.95);
                font-size:1.9rem;
                font-weight:800;
                margin:.3rem 0 0;
                letter-spacing:-.02em;
            ">FIFA 2026</div>
            <div style="
                color: rgba(255,255,255,0.5);
                margin:.2rem 0 0;
                font-size:.9rem;
            ">World Cup Guessing Game</div>
        </div>
    """, unsafe_allow_html=True)

    tab_login, tab_register = st.tabs(["🔐  Sign In", "✍️  Create Account"])

    # ── Login tab ─────────────────────────────────────────────────────────────
    with tab_login:
        st.markdown(
            '<p style="font-size:1.05rem;font-weight:700;color:#222;margin:.25rem 0 .75rem;">Welcome back</p>',
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            nickname = st.text_input("Nickname", placeholder="your_nickname")
            password = st.text_input("Password", type="password", placeholder="••••••••")
            login_submitted = st.form_submit_button("Log In", width="stretch")

        if login_submitted:
            if not nickname or not password:
                st.error("Please fill in all fields.")
            else:
                with st.spinner("Signing in…"):
                    success = login_user(nickname.strip(), password)
                if success:
                    st.rerun()
                else:
                    st.error("Invalid nickname or password.")

    # ── Register tab ──────────────────────────────────────────────────────────
    with tab_register:
        st.markdown(
            '<p style="font-size:1.05rem;font-weight:700;color:#222;margin:.25rem 0 .75rem;">Create your account</p>',
            unsafe_allow_html=True,
        )

        with st.form("register_form"):
            col_a, col_b = st.columns(2)
            with col_a:
                first_name = st.text_input("First Name *", placeholder="John")
            with col_b:
                last_name = st.text_input("Last Name *", placeholder="Doe")

            nickname_reg = st.text_input(
                "Nickname *",
                placeholder="johndoe",
                help="This is what you'll use to log in. Only letters, numbers and underscores.",
            )
            pw1 = st.text_input("Password *", type="password", placeholder="Min. 6 characters")
            pw2 = st.text_input("Confirm Password *", type="password", placeholder="Repeat your password")
            reg_submitted = st.form_submit_button("Create Account", width="stretch")

        if reg_submitted:
            errors: list[str] = []
            if not all([first_name, last_name, nickname_reg, pw1, pw2]):
                errors.append("All fields are required.")
            else:
                if len(nickname_reg.strip()) < 3:
                    errors.append("Nickname must be at least 3 characters.")
                if len(pw1) < 6:
                    errors.append("Password must be at least 6 characters.")
                if pw1 != pw2:
                    errors.append("Passwords do not match.")

            if errors:
                for err in errors:
                    st.error(err)
            else:
                with st.spinner("Creating account…"):
                    ok, msg = register_user(
                        first_name.strip(),
                        last_name.strip(),
                        nickname_reg.strip(),
                        pw1,
                    )
                    if ok:
                        login_user(nickname_reg.strip(), pw1)
                if ok:
                    st.rerun()
                else:
                    st.error(msg)


_AUTOCOMPLETE_JS = """
<script>
(function () {
    function applyAutocomplete() {
        var panels = document.querySelectorAll('[data-testid="stTabPanel"]');
        if (panels.length < 2) return false;

        // Login panel — nickname=username, password=current-password
        panels[0].querySelectorAll('[data-testid="stTextInput"]').forEach(function (wrap) {
            var label = wrap.querySelector('label');
            var input = wrap.querySelector('input');
            if (!label || !input) return;
            var t = label.textContent.toLowerCase();
            if (t.includes('nickname')) {
                input.setAttribute('name', 'username');
                input.setAttribute('autocomplete', 'username');
            } else if (t.includes('password')) {
                input.setAttribute('name', 'password');
                input.setAttribute('autocomplete', 'current-password');
            }
        });

        // Register panel — username + new-password
        panels[1].querySelectorAll('[data-testid="stTextInput"]').forEach(function (wrap) {
            var label = wrap.querySelector('label');
            var input = wrap.querySelector('input');
            if (!label || !input) return;
            var t = label.textContent.toLowerCase();
            if (t.includes('nickname')) {
                input.setAttribute('name', 'reg-username');
                input.setAttribute('autocomplete', 'username');
            } else if (t.includes('confirm')) {
                input.setAttribute('name', 'confirm-password');
                input.setAttribute('autocomplete', 'new-password');
            } else if (t.includes('password')) {
                input.setAttribute('name', 'new-password');
                input.setAttribute('autocomplete', 'new-password');
            }
        });

        return true;
    }

    var attempts = 0;
    var timer = setInterval(function () {
        if (applyAutocomplete() || ++attempts >= 15) clearInterval(timer);
    }, 200);
})();
</script>
"""


def require_login():
    init_auth_db()
    if st.session_state.get("authenticated"):
        return
    if _restore_session(st.query_params.get("_sid", "")):
        return
    show_auth_page()
    st.markdown(_AUTOCOMPLETE_JS, unsafe_allow_html=True)
    st.stop()
