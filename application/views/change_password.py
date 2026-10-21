"""Shown instead of the app until a person with a temporary password has chosen their own."""
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import services

kit.theme()
u = ui.user()
left, mid, right = st.columns([1, 2.2, 1])
with mid:
    st.title("Choose your password")
    st.caption("Your administrator gave you a temporary password. Choose your own before you continue: at least 12 "
               "characters with 3 of lower-case, upper-case, digit and symbol, not containing your name or username.")
    with st.form("must_change"):
        cur = st.text_input("Temporary password", type="password")
        new = st.text_input("New password", type="password")
        new2 = st.text_input("New password again", type="password")
        go = st.form_submit_button("Save and continue", type="primary", width="stretch")
    if go:
        try:
            if new != new2:
                raise ValueError("The two new passwords differ.")
            services.change_own_password(ui.live_world(), u, cur, new)
            st.session_state.user.pop("must_change", None)
            st.rerun()
        except (ValueError, services.Denied) as e:
            st.error(str(e))
    if st.button("Sign out"):
        ui.sign_out()
