"""ShiftSense PoC — Streamlit entry point: sign-in, then role-based navigation."""
from pathlib import Path

import streamlit as st

from shiftsense import auth
from shiftsense import navigation as nav_model

st.set_page_config(page_title="ShiftSense", page_icon=":material/schedule:", layout="wide")
st.logo("assets/shiftsense_logo.svg", size="large")

def _area_page(script: str, key: str, area: str):
    def run():
        st.session_state[key] = area                  # the link selects the area; the script reads it
        exec(compile(Path(script).read_text(), script, "exec"), {"__name__": "__main__", "__file__": script})
    run.__name__ = f"{Path(script).stem}_{area}".replace(" ", "_").replace("&", "and").replace("-", "_").lower()
    return run


def navigation(allowed: list[str]) -> dict:
    """{group: [pages]} for the signed-in role only (see shiftsense/navigation.py)."""
    nav = {}
    for d in nav_model.spec(allowed):
        page = (st.Page(_area_page(d["script"], d["area_key"], d["area"]), title=d["title"], icon=d["icon"],
                        url_path=d["url"], default=d.get("default", False)) if d["area_key"]
                else st.Page(d["script"], title=d["title"], icon=d["icon"], default=d.get("default", False)))
        nav.setdefault(d["group"], []).append(page)
    return nav


from datetime import datetime

from shiftsense import security

raw = st.session_state.get("user")
user = auth.current(raw)            # re-checked each run: deactivated or changed accounts must sign in again
if raw and user is not raw:
    st.session_state.clear()
    user = None
if user and security.idle_expired(st.session_state.get("last_seen")):
    st.session_state.clear()        # idle timeout: sign in again
    st.session_state["idle_out"] = True
    user = None
if user:
    st.session_state["last_seen"] = datetime.now().isoformat(timespec="seconds")
if "checkin" in st.query_params:                      # retired SMS link: check-in now needs sign-in
    st.query_params.clear()
if not user:
    st.navigation([st.Page("views/login.py", title="Sign in", icon=":material/login:")], position="hidden").run()
elif user.get("must_change"):
    st.navigation([st.Page("views/change_password.py", title="Choose your password", icon=":material/key:")],
                  position="hidden").run()
else:
    st.navigation(navigation(auth.role(user)["pages"]), position="sidebar").run()
