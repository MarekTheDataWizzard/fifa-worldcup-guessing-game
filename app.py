import streamlit as st
from dotenv import load_dotenv

from admin import render_admin_page
from auth import logout_user, require_login, update_account
from leaderboard import render_leaderboard_page
from matches import render_matches_page
from odds import init_odds_db
from tips import init_tips_db

load_dotenv()

st.set_page_config(
    page_title="FIFA 2026 Guessing Game",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_login()
init_odds_db()
init_tips_db()

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
footer                                     { visibility: hidden; }
[data-testid="stDecoration"]               { display: none !important; }
[data-testid="stToolbar"]                  { visibility: hidden !important; }
[data-testid="stExpandSidebarButton"],
[data-testid="stMainMenuButton"],
#MainMenu                                  { visibility: visible !important; }

/* Remove default top padding so page content starts near the top */
.block-container                           { padding-top: 1rem !important; }

/* Sidebar nav buttons — flat, link-like */
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    border-radius: 8px !important;
    text-align: left !important;
    padding: 0.5rem 0.75rem !important;
    font-size: 0.95rem !important;
    color: inherit !important;
    box-shadow: none !important;
    transition: background 0.15s, color 0.15s !important;
    width: 100% !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(128, 128, 128, 0.15) !important;
    color: inherit !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: rgba(128, 128, 128, 0.18) !important;
    color: inherit !important;
    font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
current_user = st.session_state["user"]
if "page" not in st.session_state:
    st.session_state["page"] = "Matches"

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"**{current_user['display_name']}**")
    st.caption(f"@{current_user['nickname']}")
    st.divider()

    _nav = [
        ("⚽", "Matches"),
        ("🏆", "Leaderboard"),
        ("📋", "Game Rules"),
        ("👤", "Edit Account"),
    ]
    if current_user.get("is_admin"):
        _nav.append(("👑", "Admin"))

    _cur = st.session_state["page"]
    for _icon, _label in _nav:
        if st.button(
            f"{_icon}  {_label}",
            key=f"nav_{_label}",
            use_container_width=True,
            type="primary" if _cur == _label else "secondary",
        ):
            st.session_state["page"] = _label
            st.rerun()

    st.divider()
    if st.button("Log Out", use_container_width=True):
        logout_user()


# ── Page functions ────────────────────────────────────────────────────────────



def _render_rules_page():
    st.title("📋 Game Rules")

    st.markdown("""
Every match in the **FIFA 2026 World Cup** gives you **100 GX tokens** to wager.
Pick the right outcome to turn them into GX points.
""")

    st.divider()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
<div style="border:1px solid rgba(128,128,128,0.2);border-radius:12px;padding:18px 16px;text-align:center;">
  <div style="font-size:2rem;">✅</div>
  <div style="font-weight:700;font-size:1rem;margin:8px 0 4px;">Correct tip</div>
  <div style="font-size:1.4rem;font-weight:800;color:#4caf50;">100 × odds</div>
  <div style="font-size:.78rem;opacity:.5;margin-top:4px;">GX points earned</div>
</div>
""", unsafe_allow_html=True)
    with c2:
        st.markdown("""
<div style="border:1px solid rgba(128,128,128,0.2);border-radius:12px;padding:18px 16px;text-align:center;">
  <div style="font-size:2rem;">❌</div>
  <div style="font-weight:700;font-size:1rem;margin:8px 0 4px;">Wrong tip</div>
  <div style="font-size:1.4rem;font-weight:800;color:#f44336;">0</div>
  <div style="font-size:.78rem;opacity:.5;margin-top:4px;">GX points earned</div>
</div>
""", unsafe_allow_html=True)
    with c3:
        st.markdown("""
<div style="border:1px solid rgba(128,128,128,0.2);border-radius:12px;padding:18px 16px;text-align:center;">
  <div style="font-size:2rem;">⏭️</div>
  <div style="font-weight:700;font-size:1rem;margin:8px 0 4px;">No tip placed</div>
  <div style="font-size:1.4rem;font-weight:800;color:#ff9800;">70</div>
  <div style="font-size:.78rem;opacity:.5;margin-top:4px;">GX points earned</div>
</div>
""", unsafe_allow_html=True)

    st.divider()

    st.markdown("### Betting options")
    st.markdown("""
| Symbol | Meaning |
|--------|---------|
| **1** | Home team wins |
| **X** | Draw |
| **2** | Away team wins |
""")

    st.divider()

    st.markdown("### How odds work")
    st.markdown("""
Odds represent the market's estimate of each outcome. Higher odds = rarer outcome = bigger reward.

**Example:** Canada vs Bosnia and Herzegovina — Canada wins at odds **1.81**

- You tip **1** (Canada wins) → Canada wins → **100 × 1.81 = 181 GX points** ✅
- You tip **1** (Canada wins) → Bosnia wins → **0 GX points** ❌
- You place no tip → result doesn't matter → **70 GX points** ⏭️
""")

    st.divider()

    st.markdown("### Rules")
    st.markdown("""
- You can **change or cancel** your tip at any time before the match kicks off.
- Once a match starts, your tip is **locked in** — no changes possible.
- Odds are market averages and are locked in at the time of kick-off.
- Players are ranked on the **Leaderboard** by total GX points.
""")


def _render_account_page():
    st.title("👤 Edit Account")

    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("edit_account_form"):
            st.markdown("#### Profile")
            first_name = st.text_input("First Name", value=current_user["first_name"])
            last_name  = st.text_input("Last Name",  value=current_user["last_name"])
            nickname   = st.text_input("Nickname",   value=current_user["nickname"])

            st.divider()
            st.markdown("#### Change Password")
            st.caption("Leave blank to keep your current password.")
            cur_pw  = st.text_input("Current Password",     type="password")
            new_pw  = st.text_input("New Password",         type="password")
            new_pw2 = st.text_input("Confirm New Password", type="password")

            save = st.form_submit_button("Save Changes", use_container_width=True)

        if save:
            errors = []
            if not first_name.strip(): errors.append("First name is required.")
            if not last_name.strip():  errors.append("Last name is required.")
            if not nickname.strip():   errors.append("Nickname is required.")
            if new_pw and new_pw != new_pw2: errors.append("New passwords do not match.")
            if new_pw and not cur_pw:  errors.append("Enter your current password to set a new one.")

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
                    st.session_state["user"]["first_name"]   = first_name.strip()
                    st.session_state["user"]["last_name"]    = last_name.strip()
                    st.session_state["user"]["nickname"]     = nickname.strip()
                    st.session_state["user"]["display_name"] = f"{first_name.strip()} {last_name.strip()}"
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)


# ── Routing ───────────────────────────────────────────────────────────────────
_page = st.session_state["page"]

if _page == "Matches":
    render_matches_page()
elif _page == "Leaderboard":
    render_leaderboard_page()
elif _page == "Game Rules":
    _render_rules_page()
elif _page == "Edit Account":
    _render_account_page()
elif _page == "Admin":
    render_admin_page()
