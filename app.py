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

# ── Hide Streamlit chrome (toolbar: Fork, GitHub, profile; menu; footer) ──────
st.markdown("""
<style>
#MainMenu                    { visibility: hidden; }
footer                       { visibility: hidden; }
[data-testid="stToolbar"]    { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
[data-testid="stHeader"]     { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
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

# ─────────────────────────────────────────────────────────────────────────────
# Main content
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 70vh;
    text-align: center;
    gap: 0.5rem;
">
    <div style="font-size: 5rem; line-height: 1;">⚽</div>
    <div style="font-size: 2rem; font-weight: 800; color: #1a1a1a; margin-top: 1rem;">
        More to come
    </div>
    <div style="font-size: 1rem; color: #888; max-width: 360px; margin-top: 0.25rem;">
        The game is on its way. Check back soon.
    </div>
</div>
""", unsafe_allow_html=True)
