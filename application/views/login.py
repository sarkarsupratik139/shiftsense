import pandas as pd
import streamlit as st

import ui_kit as kit
from shiftsense import auth, security
from shiftsense.config import deploy_mode


def _sso_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:                                   # no secrets file
        return False


kit.theme()
# No navigation before sign-in: hide the (empty) sidebar drawer so it can never cover the form on a phone.
st.markdown("<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']{display:none}</style>",
            unsafe_allow_html=True)
st.markdown("<div style='height:2rem'></div>", unsafe_allow_html=True)
left, mid, right = st.columns([1, 2.2, 1])
with mid:
    st.title("ShiftSense")
    st.caption("Workforce operations decision layer · proof of concept on synthetic data")
    if st.session_state.pop("idle_out", False):
        st.info(f"You were signed out after {security.idle_minutes()} minutes without activity.")
    mode = security.auth_mode()
    sso = _sso_configured() and mode in ("sso", "both")
    if sso:
        if getattr(st.user, "is_logged_in", False):     # back from the identity provider
            u, msg = auth.sso_sign_in(getattr(st.user, "email", None))
            if u:
                st.session_state.user, st.session_state.sso = u, True
                st.rerun()
            st.error(msg)
            if st.button("Sign out of the company account"):
                st.logout()
        elif st.button("Sign in with your company account", type="primary", width="stretch"):
            st.login()
    elif mode == "sso":
        st.error("Single sign-on is required but not configured on this server. Ask the administrator.")
    if mode in ("password", "both") or not sso:
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", type="primary" if not sso else "secondary", width="stretch")
        if ok:
            u, msg = auth.sign_in(username, password)
            if u:
                st.session_state.user = u
                st.rerun()
            else:
                st.error(msg)

    if deploy_mode() == "demo":
        cfg = auth.config()
        with st.expander("Demo accounts", expanded=True):
            rows = []
            for name, rec in cfg["users"].items():
                scope = rec.get("team") or ""
                if rec.get("agent_id"):
                    scope = f"{rec['agent_id']} · team {scope}"
                rows.append({"Username": name, "Password": rec["demo_password"], "Who": rec["name"],
                             "Role": cfg["roles"][rec["role"]]["label"], "Team": scope})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("Demo-only accounts with published passwords; a real deployment uses single sign-on. "
                       "Use a private window to act as a second person.")
    else:
        st.caption("Sign in with the account your administrator gave you.")
