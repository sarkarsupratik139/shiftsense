"""ShiftSense design system: theme, KPI cards, status pills, decision flow, standard charts.

One look across every role, in light and dark: every colour comes from TOKENS (CSS variables on the page,
the same values for charts). Status is always carried by words as well as colour.
Status vocabulary everywhere: Critical / Warning / Normal (+ Info for neutral states).
"""
from __future__ import annotations

import html

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# ------------------------------------------------------------------ design tokens (one source for every colour)
TOKENS = {
    # Semantic roles (documented in documentation/supporting/reference/UI_Redesign_Inventory.md):
    # bg = page, paper = surface, elev = raised surface (menus, dialogs), ink = text, muted = secondary text,
    # rule = borders, accent = primary blue (navigation, links, focus, primary actions), status pairs = text + tint.
    "light": {"bg": "#f4f6f8", "paper": "#ffffff", "elev": "#ffffff", "ink": "#16202b", "muted": "#526070",
              "rule": "#dbe1e8", "accent": "#1f5f8b", "accent-soft": "#e7eff6", "focus": "#1f5f8b",
              "crit": "#a3262a", "crit-bg": "#fbeaea", "warn": "#865800", "warn-bg": "#fcf2dc",
              "ok": "#1d6b3c", "ok-bg": "#e5f3ea", "info": "#1f5f8b", "info-bg": "#e7eff6",
              "neutral": "#4a5565", "neutral-bg": "#edf0f4", "code-bg": "#eef1f5"},
    "dark": {"bg": "#10151c", "paper": "#161d26", "elev": "#1c2530", "ink": "#e3e8ee", "muted": "#9ba7b5",
             "rule": "#2a3441", "accent": "#7cb3dc", "accent-soft": "#1a2a39", "focus": "#8cc0e6",
             "crit": "#f0a0a0", "crit-bg": "#3a1d21", "warn": "#e6bf6c", "warn-bg": "#352a15",
             "ok": "#8bd0a6", "ok-bg": "#173023", "info": "#7cb3dc", "info-bg": "#1a2a39",
             "neutral": "#b3bcc8", "neutral-bg": "#222b36", "code-bg": "#1f2833"},
}
_STATUS_KEYS = {"Critical": ("crit", "crit-bg"), "Warning": ("warn", "warn-bg"), "Normal": ("ok", "ok-bg"),
                "Info": ("info", "info-bg"), "Neutral": ("neutral", "neutral-bg")}


def status_colors(mode: str = "light") -> dict:
    t = TOKENS[mode]
    return {k: (t[f], t[b]) for k, (f, b) in _STATUS_KEYS.items()}


STATUS = status_colors("light")          # text, background (light theme; dark via status_colors("dark"))


def mode() -> str:
    """The theme the viewer is using right now (Streamlit's light/dark setting); light if unknown."""
    try:
        t = st.context.theme.type
    except Exception:
        t = None
    return "dark" if t == "dark" else "light"


def T() -> dict:
    return TOKENS[mode()]


_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
:root { __VARS__ }
html, body, [class*="css"], .stMarkdown, .stButton button, .stTextInput input {
  font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1360px; }
.stApp { background: var(--bg); }
[data-testid="stSidebar"] { border-right: 1px solid var(--rule); }
.stMarkdown, .stCaption, label, .stTextInput, .stSelectbox { font-size: 15px; }
[data-testid="stCaptionContainer"], .stCaption { color: var(--muted) !important; }
/* buttons: never truncate a label; wrap onto two lines instead */
.stButton button p, .stDownloadButton button p, [data-testid="stPopover"] button p {
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important; line-height: 1.25;
  word-break: normal !important; overflow-wrap: normal !important; hyphens: manual; }
.stButton button, .stDownloadButton button { min-height: 2.5rem; }
/* tables: calmer header, tabular numbers */
[data-testid="stDataFrame"] { border-radius: 10px; }
[data-testid="stDataFrame"] * { font-variant-numeric: tabular-nums; }
div[data-testid="stExpander"] details { border-radius: 10px; background: var(--paper); }
h1, h2, h3, h4 { color: var(--ink); letter-spacing: -0.01em; }
h1 { font-size: 1.6rem !important; font-weight: 650 !important; }
.ss-head { display:flex; justify-content:space-between; align-items:flex-end; gap:12px; flex-wrap:wrap;
  border-bottom:1px solid var(--rule); padding-bottom:10px; margin-bottom:14px; }
.ss-head .t { font-size:1.55rem; font-weight:700; color:var(--ink); line-height:1.2; margin:0; }
.ss-head .s { color:var(--muted); font-size:.9rem; }
.ss-label { font-size:1rem; font-weight:650; color:var(--ink); margin:22px 0 8px; letter-spacing:0;
  padding-top:14px; border-top:1px solid var(--rule); }
.ss-label:first-child { border-top:0; padding-top:0; }
.ss-kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(138px, 1fr)); gap:10px; margin:4px 0 10px; }
.ss-kpi { background:var(--paper); border:1px solid var(--rule); border-radius:8px; padding:10px 12px 9px; min-width:0; }
.ss-kpi .l { font-size:.78rem; font-weight:500; color:var(--muted); white-space:normal; overflow-wrap:anywhere;
  line-height:1.3; }
.ss-kpi .v { font-size:1.3rem; font-weight:650; color:var(--ink); font-variant-numeric:tabular-nums; line-height:1.3;
  overflow-wrap:anywhere; margin-top:2px; }
.ss-kpi .x { font-size:.75rem; color:var(--muted); line-height:1.3; }
.ss-kpi.Critical { border-left:4px solid var(--crit); }
.ss-kpi.Warning { border-left:4px solid var(--warn); }
.ss-kpi.Info { border-left:4px solid var(--info); }
.ss-kpi.Critical .v { color: var(--crit); }
.ss-pill { display:inline-block; font-size:.72rem; font-weight:600; padding:2px 9px; border-radius:999px;
  line-height:1.6; white-space:nowrap; border:1px solid transparent; }
.ss-pill.s-Critical { color:var(--crit); background:var(--crit-bg); border-color:var(--crit); }
.ss-pill.s-Warning { color:var(--warn); background:var(--warn-bg); border-color:var(--warn); }
.ss-pill.s-Normal { color:var(--ok); background:var(--ok-bg); }
.ss-pill.s-Info { color:var(--info); background:var(--info-bg); }
.ss-pill.s-Neutral { color:var(--neutral); background:var(--neutral-bg); }
.ss-flow { display:grid; grid-template-columns: 1fr 22px 1fr 22px 1fr; gap:6px; align-items:stretch; margin:6px 0 10px; }
.ss-flow .c { background:var(--bg); border:1px solid var(--rule); border-radius:10px; padding:10px 12px; min-width:0; }
.ss-flow .h { font-size:.78rem; font-weight:600; color:var(--muted); }
.ss-flow .b { font-size:.92rem; color:var(--ink); margin-top:4px; }
.ss-flow .a { align-self:center; text-align:center; color:var(--muted); font-size:1.1rem; }
@media (max-width: 640px) { .ss-flow { grid-template-columns:1fr; } .ss-flow .a { transform:rotate(90deg); } }
.ss-stats { display:flex; gap:10px 22px; flex-wrap:wrap; margin:4px 0 6px; padding:10px 14px; background:var(--paper);
  border:1px solid var(--rule); border-radius:10px; }
.ss-stats div { min-width:0; }
.ss-stats .l { font-size:.78rem; color:var(--muted); }
.ss-stats .v { font-size:1.05rem; font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums; }
.ss-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.ss-muted, .stMarkdown p.ss-muted { color:var(--muted); font-size:.85rem; line-height:1.45; }
.ss-sim { display:inline-block; font-size:.7rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  color:var(--warn); border:1px solid var(--warn); border-radius:4px; padding:1px 6px; }
/* accessibility: visible keyboard focus, no motion for users who ask for less, readable minimum sizes */
button:focus-visible, [role="button"]:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible,
[role="tab"]:focus-visible, [role="radio"]:focus-visible, [role="option"]:focus-visible, a:focus-visible,
[tabindex]:focus-visible, summary:focus-visible {
  outline: 3px solid var(--accent) !important; outline-offset: 2px !important; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important;
  scroll-behavior: auto !important; } }
.stMarkdown p, .stMarkdown li { font-size: 0.95rem; }
.ss-sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
.ss-banner { border:2px solid var(--warn); background:var(--warn-bg); color:var(--ink); border-radius:10px;
  padding:10px 14px; margin:0 0 12px; font-size:.92rem; }
/* sidebar: compact context + account blocks */
[data-testid="stSidebarContent"] { font-size: 14px; }
.ss-side { border:1px solid var(--rule); border-radius:10px; background:var(--paper); padding:10px 12px; margin:4px 0 8px; }
.ss-side-k { font-size:.72rem; color:var(--muted); margin-top:6px; }
.ss-side-k:first-child { margin-top:0; }
.ss-side-v { font-size:.85rem; color:var(--ink); font-variant-numeric:tabular-nums; }
.ss-side-tag { font-size:.72rem; color:var(--muted); }
.ss-side-acct { display:flex; gap:10px; align-items:center; }
.ss-avatar { width:32px; height:32px; border-radius:50%; background:var(--accent-soft); color:var(--accent);
  display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.8rem; flex:none; }
.ss-side-name { font-weight:650; font-size:.9rem; color:var(--ink); line-height:1.2; }
.ss-side-role { font-size:.78rem; color:var(--muted); }
.ss-side-upd { font-size:.72rem; color:var(--muted); margin-top:4px; }
[data-testid="stSidebar"] .stButton button { min-height:2.1rem; padding:2px 6px; font-size:.82rem; }

.ss-label[id] { scroll-margin-top: 4.5rem; }
.ss-onpage { margin:2px 0 10px; }
.ss-onpage-page { font-weight:650; font-size:.92rem; padding:6px 10px; border-radius:8px; background:var(--accent-soft);
  color:var(--accent); margin-bottom:6px; }
.ss-onpage ul { list-style:none; margin:2px 0 0 10px; padding:0 0 0 8px; border-left:2px solid var(--rule); }
.ss-onpage li { margin:0; }
.ss-onpage a { display:block; padding:3px 8px; border-radius:6px; color:var(--muted) !important; text-decoration:none;
  font-size:.84rem; }
.ss-onpage a:hover { color:var(--accent) !important; background:var(--accent-soft); }
.ss-onpage a.ss-active, .ss-onpage a[aria-current="location"] { color:var(--ink) !important; font-weight:650;
  box-shadow: inset 2px 0 0 var(--accent); }
.ss-skip { position:absolute; left:-9999px; top:0; z-index:1000; background:var(--paper); color:var(--accent);
  padding:8px 12px; border:2px solid var(--accent); border-radius:6px; }
.ss-skip:focus { left:12px; }
div[data-testid="stDialog"] div[role="dialog"] { border-radius:10px; }
/* ---- enterprise shell additions (Oct 2026 overhaul) ---------------------------------------------------- */
.stApp, [data-testid="stSidebar"], div[role="dialog"], [data-baseweb="popover"] { accent-color: var(--accent); }
[data-testid="stSidebar"] { background: var(--paper); }
.ss-head .t { font-size:1.5rem; font-weight:650; letter-spacing:-0.01em; }
.ss-crumb { font-size:.8rem; color:var(--muted); margin-bottom:2px; }
.ss-crumb b { color:var(--ink); font-weight:600; }
.ss-badges { display:flex; gap:6px; flex-wrap:wrap; align-items:center; justify-content:flex-end; }
/* data provenance labels: never present simulated or modelled numbers as measured results */
.ss-data { display:inline-flex; align-items:center; gap:4px; font-size:.68rem; font-weight:600; letter-spacing:.05em;
  text-transform:uppercase; border-radius:4px; padding:1px 6px; border:1px solid currentColor; white-space:nowrap; }
.ss-data.sim { color:var(--warn); } .ss-data.mod { color:var(--info); } .ss-data.act { color:var(--ok); }
.ss-data.na { color:var(--muted); }
/* avatars: photo, else initials, else a neutral person icon */
.ss-av { display:inline-flex; align-items:center; justify-content:center; flex:none; border-radius:50%;
  overflow:hidden; background:var(--accent-soft); color:var(--accent); font-weight:650; letter-spacing:.02em;
  border:1px solid var(--rule); vertical-align:middle; }
.ss-av img { width:100%; height:100%; object-fit:cover; display:block; }
.ss-av.sm { width:24px; height:24px; font-size:.62rem; } .ss-av.md { width:32px; height:32px; font-size:.75rem; }
.ss-av.lg { width:72px; height:72px; font-size:1.4rem; } .ss-av.xl { width:112px; height:112px; font-size:2.1rem; }
.ss-av svg { width:62%; height:62%; fill:currentColor; opacity:.75; }
.ss-person { display:flex; align-items:center; gap:10px; min-width:0; }
.ss-person .n { font-weight:600; color:var(--ink); line-height:1.25; overflow-wrap:anywhere; }
.ss-person .m { font-size:.8rem; color:var(--muted); line-height:1.3; }
.ss-people { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:8px; margin:6px 0 10px; }
.ss-people > div { background:var(--paper); border:1px solid var(--rule); border-radius:8px; padding:8px 10px; min-width:0; }
.ss-profile { display:flex; gap:18px; align-items:center; flex-wrap:wrap; padding:14px 16px; background:var(--paper);
  border:1px solid var(--rule); border-radius:10px; margin:4px 0 12px; }
.ss-profile .n { font-size:1.35rem; font-weight:650; color:var(--ink); line-height:1.2; }
.ss-profile .m { color:var(--muted); font-size:.9rem; }
/* process stepper: the publication lifecycle and other ordered workflows */
.ss-steps { display:flex; gap:0; margin:6px 0 14px; padding:0; list-style:none; flex-wrap:wrap; }
.ss-steps li { flex:1 1 120px; min-width:0; position:relative; padding:8px 10px 8px 34px; font-size:.84rem;
  color:var(--muted); border-top:3px solid var(--rule); }
.ss-steps li::before { content:attr(data-n); position:absolute; left:6px; top:7px; width:20px; height:20px;
  border-radius:50%; border:1px solid var(--rule); background:var(--paper); font-size:.72rem; text-align:center;
  line-height:19px; color:var(--muted); }
.ss-steps li.done { color:var(--ink); border-top-color:var(--ok); }
.ss-steps li.done::before { content:"\\2713"; color:var(--ok); border-color:var(--ok); }
.ss-steps li.now { color:var(--ink); font-weight:650; border-top-color:var(--accent); }
.ss-steps li.now::before { border-color:var(--accent); color:var(--accent); font-weight:700; }
/* empty states: say what will appear and how to start */
.ss-empty { display:flex; gap:14px; align-items:center; padding:16px; border:1px dashed var(--rule);
  border-radius:10px; color:var(--muted); background:var(--paper); margin:6px 0 10px; }
.ss-empty svg { width:40px; height:40px; flex:none; stroke:var(--muted); fill:none; stroke-width:1.5; }
.ss-empty b { color:var(--ink); display:block; font-weight:600; }
/* calendar patterns: week roster grid, agenda */
.ss-wg-wrap { overflow-x:auto; border:1px solid var(--rule); border-radius:8px; background:var(--paper); margin:6px 0 10px; }
.ss-wg { display:grid; min-width:720px; }
.ss-wg-h { font-size:.78rem; font-weight:600; color:var(--muted); padding:8px 8px; border-bottom:1px solid var(--rule);
  position:sticky; top:0; background:var(--paper); }
.ss-wg-h.now { color:var(--accent); box-shadow: inset 0 -2px 0 var(--accent); }
.ss-wg-p { padding:6px 8px; border-bottom:1px solid var(--rule); min-width:0; }
.ss-wg-c { padding:4px; border-bottom:1px solid var(--rule); border-left:1px solid var(--rule); min-width:0; }
.ss-wg-c.now { background:var(--accent-soft); }
.ss-blk { border-radius:6px; padding:4px 6px; font-size:.74rem; line-height:1.25; border-left:3px solid var(--accent);
  background:var(--info-bg); color:var(--ink); overflow-wrap:anywhere; }
.ss-blk .a { display:block; font-weight:600; } .ss-blk .b { display:block; color:var(--muted); }
.ss-blk.leave { border-left-color:var(--warn); background:var(--warn-bg); }
.ss-blk.pending { border-left-color:var(--warn); background:transparent; border:1px dashed var(--warn);
  border-left:3px solid var(--warn); }
.ss-blk.absent { border-left-color:var(--crit); background:var(--crit-bg); }
.ss-blk.off { border-left-color:var(--rule); background:transparent; color:var(--muted); }
.ss-agenda { border:1px solid var(--rule); border-radius:8px; background:var(--paper); margin:6px 0 10px; }
.ss-ag-r { display:grid; grid-template-columns:64px minmax(0,1fr) auto; gap:10px; align-items:center;
  padding:8px 12px; border-bottom:1px solid var(--rule); }
.ss-ag-r:last-child { border-bottom:0; }
.ss-ag-r .tm { font-variant-numeric:tabular-nums; font-weight:600; color:var(--ink); font-size:.85rem; }
.ss-ag-r .bd { display:flex; align-items:center; gap:10px; min-width:0; }
.ss-ag-r .tx { font-size:.86rem; min-width:0; overflow-wrap:anywhere; }
/* chart cards */
.ss-chart-h .t { font-size:.95rem; font-weight:650; color:var(--ink); line-height:1.3; }
.ss-chart-h .s { font-size:.8rem; color:var(--muted); margin-top:1px; }
.ss-chart-h .p { margin-top:4px; }
[data-testid="stPlotlyChart"] .modebar { opacity:.55; } [data-testid="stPlotlyChart"]:hover .modebar { opacity:1; }
/* Streamlit's toolbar keeps a 200 px minimum width for its menu group, which pushes the page sideways on phones */
[data-testid="stToolbar"] div:has(> [data-testid="stMainMenu"]) { min-width: 0 !important; }
</style>
"""


def css(m: str = "light") -> str:
    """Every token is light-dark(light, dark): it resolves against the colour scheme Streamlit sets on the app, so
    the whole page (our components included) follows a theme switch at once, without waiting for a rerun."""
    L, D = TOKENS["light"], TOKENS["dark"]
    return _CSS.replace("__VARS__", " ".join(f"--{k}: light-dark({L[k]}, {D[k]});" for k in L))


A11Y_JS = """<script>
(function () {
  // Streamlit's own sidebar toggles have no accessible name, and its Material icons are text ligatures that
  // screen readers would read aloud ("left_panel_close"). Name the toggles; hide the icons from assistive tech.
  function fix() {
    document.querySelectorAll('[data-testid="stExpandSidebarButton"]').forEach(function (b) {
      b.setAttribute('aria-label', 'Open navigation'); b.setAttribute('title', 'Open navigation'); });
    document.querySelectorAll('[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"]').forEach(function (b) {
      b.setAttribute('aria-label', 'Collapse navigation'); b.setAttribute('title', 'Collapse navigation'); });
    document.querySelectorAll('[data-testid="stIconMaterial"]').forEach(function (i) {
      if (i.getAttribute('aria-hidden') !== 'true') i.setAttribute('aria-hidden', 'true'); });
  }
  // in-page section links: highlight the section in view (aria-current="location")
  var seen = window.__ssSecSeen || (window.__ssSecSeen = new WeakSet());
  function setActive(id) {
    document.querySelectorAll('.ss-onpage a[data-sec]').forEach(function (a) {
      var on = a.getAttribute('data-sec') === id;
      a.classList.toggle('ss-active', on);
      if (on) { a.setAttribute('aria-current', 'location'); } else { a.removeAttribute('aria-current'); }
    });
  }
  if (!window.__ssSpy && 'IntersectionObserver' in window) {
    window.__ssSpy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { setActive(e.target.id); } });
    }, {rootMargin: '0px 0px -70% 0px'});
  }
  function spy() {
    document.querySelectorAll('.ss-label[id^="sec-"]').forEach(function (h) {
      if (!seen.has(h) && window.__ssSpy) { seen.add(h); window.__ssSpy.observe(h); } });
    document.querySelectorAll('.ss-onpage a[data-sec]').forEach(function (a) {
      if (!a.__ssClick) { a.__ssClick = true; a.addEventListener('click', function () {
        setActive(a.getAttribute('data-sec')); }); } });
  }
  // dialogs and menus are rendered outside the app container: give the page root the app's colour scheme so
  // the light-dark() tokens resolve the same everywhere
  function scheme() { var app = document.querySelector('.stApp'); if (!app) { return; }
    var cs = getComputedStyle(app).colorScheme;
    if (cs && document.documentElement.style.colorScheme !== cs) { document.documentElement.style.colorScheme = cs; } }
  // phones and small tablets: close the navigation drawer after a page is chosen, so it never covers the page
  if (!window.__ssNavClose) {
    window.__ssNavClose = true;
    document.addEventListener('click', function (ev) {
      var link = ev.target.closest && ev.target.closest('[data-testid="stSidebarNav"] a, [data-testid="stSidebarNavLink"]');
      if (!link || window.innerWidth >= 1024) { return; }
      setTimeout(function () {
        var b = document.querySelector('[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"]');
        var sb = document.querySelector('[data-testid="stSidebar"]');
        if (b && sb && sb.getAttribute('aria-expanded') !== 'false') { b.click(); }
      }, 350);
    }, true);
  }
  var pending = false;
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(function () { pending = false; fix(); spy(); scheme(); }); } }
  fix(); spy(); scheme();
  if (!window.__ssA11y) { window.__ssA11y = new MutationObserver(schedule);
    window.__ssA11y.observe(document.body, {childList: true, subtree: true, attributes: true,
                                            attributeFilter: ["class"]}); }
})();
</script>"""


def theme():
    st.markdown(css(mode()), unsafe_allow_html=True)
    st.html(A11Y_JS, unsafe_allow_javascript=True, width="content")   # static script, no user data


def esc(x) -> str:
    return html.escape(str(x))


def pill(text: str, status: str = "Neutral") -> str:
    status = status if status in STATUS else "Neutral"
    return f'<span class="ss-pill s-{status}">{esc(text)}</span>'


def header(title: str, subtitle: str = "", right: str = "", crumb: str = ""):
    nav = (f'<nav class="ss-crumb" aria-label="Breadcrumb">{esc(crumb.split(" › ")[0])} › '
           f'<b aria-current="page">{esc(crumb.split(" › ")[1])}</b></nav>') if " › " in crumb else ""
    st.markdown(f'<div class="ss-head"><div>{nav}<h1 class="t">{esc(title)}</h1>'
                f'<div class="s">{esc(subtitle)}</div></div><div class="ss-badges">{right}</div></div>',
                unsafe_allow_html=True)


def label(text: str, anchor: str | None = None):
    """Section heading. `anchor` makes it a jump target for the sidebar's 'On this page' list."""
    aid = f' id="sec-{esc(anchor)}"' if anchor else ""
    st.markdown(f'<div class="ss-label"{aid} role="heading" aria-level="2">{esc(text)}</div>', unsafe_allow_html=True)


def kpis(items: list[dict]):
    """items: {label, value, sub?, status?} — status adds a coloured edge."""
    cells = []
    for k in items:
        st_cls = k.get("status") or ""
        if st_cls == "Normal":
            st_cls = ""                     # colour is reserved for states that need attention
        sub = f'<div class="x">{esc(k["sub"])}</div>' if k.get("sub") else ""
        cells.append(f'<div class="ss-kpi {st_cls}" title="{esc(k.get("help", ""))}"><div class="l">{esc(k["label"])}</div>'
                     f'<div class="v">{esc(k["value"])}</div>{sub}</div>')
    st.markdown(f'<div class="ss-kpis">{"".join(cells)}</div>', unsafe_allow_html=True)


def stats(items: list[tuple[str, str]]):
    """Compact inline label/value pairs for small cards."""
    st.markdown('<div class="ss-stats">' + "".join(f'<div><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div></div>'
                                                   for l, v in items) + "</div>", unsafe_allow_html=True)


def flow(problem: str, action: str, outcome: str):
    """Problem -> Action -> Outcome, as three cards (HTML allowed inside, escape upstream)."""
    st.markdown(
        f'<div class="ss-flow"><div class="c"><div class="h">Problem</div><div class="b">{problem}</div></div>'
        f'<div class="a">→</div><div class="c"><div class="h">Recommended action</div><div class="b">{action}</div></div>'
        f'<div class="a">→</div><div class="c"><div class="h">Expected outcome</div><div class="b">{outcome}</div></div></div>',
        unsafe_allow_html=True)


def chart(figure, summary: str, key: str, data=None, provenance: str | None = None, title: str | None = None,
          subtitle: str | None = None, stats_: list | None = None, on_select: bool = False, zoom: bool = False):
    """One chart card: business title and optional subtitle (unit or scope), provenance label, an optional summary
    strip, the chart, and a compact 'Data & export' menu (summary, numbers, CSV). The plot toolbar keeps only
    'Download image' and 'Reset axes'; full screen comes from Streamlit's element menu. The text summary is always
    in the page for screen readers. on_select=True returns the selection event (for opening a record)."""
    if not title and isinstance(figure.layout.meta, dict):
        title = figure.layout.meta.get("title")
    if provenance:
        summary = f"{DATA_KINDS[provenance][1]}. {summary}"
    with st.container(border=True):
        h1, h2 = st.columns([5, 1.4], vertical_alignment="top")
        h1.markdown(f'<div class="ss-chart-h"><div class="t">{esc(title or "")}</div>'
                    + (f'<div class="s">{esc(subtitle)}</div>' if subtitle else "")
                    + (f'<div class="p">{data_label(provenance)}</div>' if provenance else "") + "</div>",
                    unsafe_allow_html=True)
        with h2.popover("Data & export", icon=":material/table_view:", width="stretch",
                        help="What this chart shows, its numbers and a CSV export"):
            st.markdown(esc(summary))
            if data is not None:
                st.dataframe(data, hide_index=True, width="stretch")
                st.download_button("Export data (CSV)", data.to_csv(index=False).encode(), f"{key}.csv", "text/csv",
                                   key=f"{key}_csv", icon=":material/download:")
        if stats_:
            stats(stats_)
        st.markdown(f'<p class="ss-sr" role="note">{esc(summary)}</p>', unsafe_allow_html=True)
        if zoom:
            figure.update_layout(dragmode="zoom")
        cfg = {"displaylogo": False, "responsive": True,
               "modeBarButtons": [["toImage"] + (["zoomIn2d", "zoomOut2d", "resetScale2d"] if zoom else [])],
               "toImageButtonOptions": {"format": "png", "filename": key, "scale": 2}}
        if on_select:
            return st.plotly_chart(figure, width="stretch", key=key, config=cfg, on_select="rerun",
                                   selection_mode="points")
        st.plotly_chart(figure, width="stretch", key=key, config=cfg)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    def lum(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def coverage_status(pct: float) -> str:
    return "Normal" if pct >= 95 else "Warning" if pct >= 85 else "Critical"


def money(x: float) -> str:
    return f"₹{x:,.0f}"


# ------------------------------------------------------------------ charts: one enterprise chart system
# Chart tokens per theme, tuned separately (not inverted): surfaces stay transparent so a chart sits on its card.
CHART = {
    "light": {"grid": "#e9edf2", "axis": "#cfd6de", "tick": "#5d6a78", "series": "#1f5f8b", "series2": "#8a97a6",
              "reference": "#2a3644", "band": "rgba(31,95,139,0.07)", "short": "rgba(163,38,42,0.16)",
              "after": "#1d6b3c", "present": "rgba(29,107,60,0.55)", "unknown": "rgba(93,106,120,0.28)",
              "div_neg": "#b3403f", "div_neg2": "#e8b2a5", "div_mid": "#f3f5f7", "div_pos2": "#b9d0e4",
              "div_pos": "#2d6d9c"},
    "dark": {"grid": "#253040", "axis": "#334155", "tick": "#a3afbd", "series": "#7cb3dc", "series2": "#8794a3",
             "reference": "#d9e0e8", "band": "rgba(124,179,220,0.10)", "short": "rgba(240,128,128,0.20)",
             "after": "#8bd0a6", "present": "rgba(139,208,166,0.45)", "unknown": "rgba(163,175,189,0.25)",
             "div_neg": "#e07a76", "div_neg2": "#6b3b39", "div_mid": "#1c2530", "div_pos2": "#2c4a63",
             "div_pos": "#7cb3dc"},
}

pio.templates["shiftsense"] = go.layout.Template(layout=dict(
    font=dict(family="IBM Plex Sans, system-ui, sans-serif", size=12),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(showgrid=False, ticks="", zeroline=False, automargin=True),
    yaxis=dict(zeroline=False, ticks="", automargin=True),
    legend=dict(orientation="h", title=None),
))


def C() -> dict:
    return CHART[mode()]


def fig(height=280, title=None, **kw) -> go.Figure:
    """Shared layout: no plot title (the chart card carries it), compact legend at the top-left, thin axis, faint
    horizontal grid, themed tooltip, tight margins. `title` is kept on the figure only as metadata for chart()."""
    t, c = T(), C()
    f = go.Figure()
    f.update_layout(template="shiftsense", height=height, font_color=t["ink"],
                    colorway=series_palette(), margin=dict(l=4, r=8, t=30, b=4),
                    legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom",
                                font=dict(size=11, color=t["muted"]), itemsizing="constant",
                                bgcolor="rgba(0,0,0,0)", traceorder="normal"),
                    hovermode="x unified", dragmode=False,
                    hoverlabel=dict(bgcolor=t["elev"], bordercolor=c["axis"], align="left",
                                    font=dict(color=t["ink"], size=12)),
                    meta={"title": title}, **kw)
    f.update_xaxes(showline=True, linecolor=c["axis"], linewidth=1, tickfont=dict(color=c["tick"], size=11),
                   title_font=dict(color=c["tick"], size=11))
    f.update_yaxes(showgrid=True, gridcolor=c["grid"], gridwidth=1, showline=False, zeroline=False,
                   tickfont=dict(color=c["tick"], size=11), title_font=dict(color=c["tick"], size=11))
    return f


def series_palette() -> list[str]:
    """Categories (not status): the brand blue, then neutrals. Status colours are reserved for meaning."""
    c = C()
    return [c["series"], c["series2"], T()["muted"], T()["info"]]


def gap_stats(x, required, scheduled) -> list[tuple[str, str]]:
    """Summary of the existing shortage measure (required − scheduled people, never below 0) for the strip above
    a coverage chart: peak gap, half-hours short, and when the peak occurs. No new metric."""
    import numpy as np
    short = np.clip(np.asarray(required, float) - np.asarray(scheduled, float), 0, None)
    if not short.any():
        return [("Peak gap", "None"), ("Half-hours short", "0"), ("Highest risk", "—")]
    i = int(short.argmax())
    return [("Peak gap", f"{short[i]:g} people"), ("Half-hours short", str(int((short > 0).sum()))),
            ("Highest risk", str(list(x)[i]))]


def _shortage_fill(f, x, required, scheduled, name="Shortage (required − scheduled)"):
    """Shade only where scheduled is below required: a subtle tint between the two lines, no hatching."""
    import numpy as np
    c = C()
    req = np.asarray(required, float)
    low = np.minimum(np.asarray(scheduled, float), req)
    f.add_trace(go.Scatter(x=x, y=req, line=dict(width=0, shape="hv"), showlegend=False, hoverinfo="skip"))
    f.add_trace(go.Scatter(x=x, y=low, fill="tonexty", fillcolor=c["short"], line=dict(width=0, shape="hv"),
                           name=name, hoverinfo="skip"))


def supply_demand(x, required, scheduled, productive=None, after=None, band=None, title=None, height=280):
    """Coverage vs required staffing over 30-minute intervals: required = dark dashed reference step line,
    scheduled = brand-blue solid step line, shortage = subtle tint where scheduled < required."""
    f, c = fig(height, title), C()
    if band is not None:
        lo, hi = band
        f.add_trace(go.Scatter(x=x, y=hi, line=dict(width=0, shape="hv"), showlegend=False, hoverinfo="skip"))
        f.add_trace(go.Scatter(x=x, y=lo, fill="tonexty", fillcolor=c["band"], line=dict(width=0, shape="hv"),
                               name="Forecast range", hoverinfo="skip"))
    _shortage_fill(f, x, required, scheduled)
    f.add_trace(go.Scatter(x=x, y=required, name="Required", line=dict(color=c["reference"], width=1.6, dash="dash",
                                                                         shape="hv"),
                           hovertemplate="%{y:.1f} people<extra>Required</extra>"))
    f.add_trace(go.Scatter(x=x, y=scheduled, name="Scheduled", line=dict(color=c["series"], width=2.2, shape="hv"),
                           hovertemplate="%{y:.1f} people<extra>Scheduled</extra>"))
    if productive is not None:
        f.add_trace(go.Scatter(x=x, y=productive, name="Productive (after shrinkage)",
                               line=dict(color=c["series2"], width=1.4, dash="dot", shape="hv"),
                               hovertemplate="%{y:.1f} people<extra>Productive</extra>"))
    if after is not None:
        f.add_trace(go.Scatter(x=x, y=after, name="After proposed fix", line=dict(color=c["after"], width=2.2, shape="hv"),
                               hovertemplate="%{y:.1f} people<extra>After fix</extra>"))
    f.update_xaxes(nticks=10)
    f.update_yaxes(rangemode="tozero", ticksuffix=" ")
    return f


def gap_heatmap(z, x, y, title=None, height=None, unit="people (scheduled − required)", mode_="diverging",
                fmt=".0f", show_text=None):
    """Operations heatmap.
    mode_ 'diverging': negative = short (warm), 0 = neutral midpoint, positive = surplus (blue); symmetric scale.
    mode_ 'shortfall': values ≤ 0 mean short; shown as the size of the shortfall (0 = neutral, larger = warmer).
    Exact values in each cell when there is room, and in the tooltip; legend states the unit."""
    import numpy as np
    c, t = C(), T()
    z = np.asarray(z, float)
    rows, cols = z.shape if z.ndim == 2 else (1, len(z))
    height = height or max(150, 46 + 30 * rows)
    show_text = (rows * cols <= 120) if show_text is None else show_text
    f = fig(height, title)
    if mode_ == "shortfall":
        mag = np.clip(-z, 0, None)
        m = max(float(mag.max()), 1e-9)
        scale = [[0, c["div_mid"]], [0.35, c["div_neg2"]], [1, c["div_neg"]]]
        zz, zmin, zmax = mag, 0, m
        ticks = dict(tickvals=[0, m / 2, m], ticktext=["0", f"{m / 2:{fmt}}", f"{m:{fmt}}"])
        legend = f"{unit}"
        hov = "%{y} · %{x}<br><b>%{z:" + fmt + "}</b> " + unit + "<extra></extra>"
    else:
        m = max(float(np.abs(z).max()), 1.0)
        scale = [[0, c["div_neg"]], [0.3, c["div_neg2"]], [0.5, c["div_mid"]], [0.7, c["div_pos2"]], [1, c["div_pos"]]]
        zz, zmin, zmax = z, -m, m
        ticks = dict(tickvals=[-m, 0, m], ticktext=[f"−{m:{fmt}} short", "0 on target", f"+{m:{fmt}} surplus"])
        legend = unit
        hov = "%{y} · %{x}<br><b>%{z:+" + fmt + "}</b> " + unit + "<extra></extra>"
    f.add_trace(go.Heatmap(z=zz, x=x, y=y, zmin=zmin, zmax=zmax, colorscale=scale, xgap=2, ygap=2,
                           hovertemplate=hov, colorbar=dict(thickness=8, len=0.9, outlinewidth=0,
                                                            tickfont=dict(size=10, color=c["tick"]),
                                                            title=dict(text=legend, side="right",
                                                                       font=dict(size=10, color=c["tick"])),
                                                            **ticks)))
    if show_text:                                   # value in each cell; light text on the strongest cells only
        span = max(abs(zmin), abs(zmax), 1e-9)
        for i, row in enumerate(np.atleast_2d(zz)):
            for j, v in enumerate(row):
                if abs(v) < 1e-9:
                    continue
                strong = abs(v) / span > 0.6
                f.add_annotation(x=list(x)[j], y=list(y)[i], text=f"{v:{fmt}}", showarrow=False,
                                 font=dict(size=10, color=t["paper"] if strong else t["ink"]))
    f.update_layout(hovermode="closest", margin=dict(l=4, r=4, t=8, b=4))
    f.update_xaxes(showline=False, showgrid=False, side="bottom")
    f.update_yaxes(autorange="reversed", showgrid=False)
    return f


def bars(categories, series: dict, title=None, height=260, horizontal=False, money_axis=False, stacked=False):
    """Grouped bars (or stacked when the parts add up to a meaningful total). Brand blue first, neutral second."""
    f = fig(height, title)
    palette = series_palette()
    for i, (name, vals) in enumerate(series.items()):
        kw = dict(y=categories, x=vals, orientation="h") if horizontal else dict(x=categories, y=vals)
        vfmt = ("₹%{x:,.0f}" if horizontal else "₹%{y:,.0f}") if money_axis else ("%{x:,.4~g}" if horizontal
                                                                                else "%{y:,.4~g}")
        f.add_trace(go.Bar(name=name, marker_color=palette[i % len(palette)], marker_line_width=0,
                           hovertemplate=vfmt + f"<extra>{esc(name)}</extra>", **kw))
    f.update_layout(barmode="stack" if stacked else "group", hovermode="x unified" if not horizontal else "y unified",
                    bargap=0.38, bargroupgap=0.08, showlegend=len(series) > 1)
    if money_axis:
        (f.update_xaxes if horizontal else f.update_yaxes)(tickprefix="₹", separatethousands=True, tickformat=",.3~s")
    if horizontal:
        f.update_xaxes(showgrid=True, gridcolor=C()["grid"], showline=False)
        f.update_yaxes(showgrid=False, showline=True, linecolor=C()["axis"])
    return f


def week_timeline(rows: list[dict], title=None, height=None):
    """rows: {day_label, start (h), end (h, may exceed 24), text, status} -> horizontal shift bars on a 0-31 h axis."""
    t, c = T(), C()
    colors = {"Normal": t["ok"], "Warning": t["warn"], "Critical": t["crit"], "Info": c["series"], "Neutral": t["neutral"]}
    f = fig(height or 50 + 34 * max(len(rows), 1), title)
    for r in rows:
        if r.get("start") is None:
            f.add_trace(go.Bar(y=[r["day_label"]], x=[0.01], base=[0], orientation="h", marker_color="rgba(0,0,0,0)",
                               showlegend=False, hovertemplate=f"{esc(r['text'])}<extra></extra>",
                               text=[r["text"]], textposition="outside", textfont=dict(color=t["muted"])))
            continue
        f.add_trace(go.Bar(y=[r["day_label"]], x=[r["end"] - r["start"]], base=[r["start"]], orientation="h",
                           marker_color=colors.get(r.get("status", "Normal"), c["series"]), marker_line_width=0,
                           showlegend=False, text=[r["text"]], textposition="inside", insidetextanchor="middle",
                           textfont=dict(color=t["paper"], size=11),
                           hovertemplate=f"{esc(r['text'])}<extra></extra>"))
    f.update_layout(barmode="overlay", hovermode="closest", bargap=0.38, margin=dict(l=4, r=8, t=8, b=4))
    f.update_xaxes(range=[0, 31], tickvals=[0, 6, 9, 12, 15, 18, 21, 24, 30],
                   ticktext=["00", "06", "09", "12", "15", "18", "21", "24", "06+1"], showgrid=True, gridcolor=c["grid"])
    f.update_yaxes(autorange="reversed", showgrid=False)
    return f


def risk_matrix(points: list[dict], title=None, height=None):
    """points: {name, x: hours until the gap starts, y: SLA exposure, size: short people-hours, status}.
    Sooner is to the right (reversed time axis), more exposed is higher. Height adapts to the number of points."""
    n = len(points)
    f, sc, c = fig(height or (220 if n <= 2 else 300), title), status_colors(mode()), C()
    marks = {"Critical": "diamond", "Warning": "triangle-up", "Normal": "circle"}   # shape as well as colour
    for st_name in ("Critical", "Warning", "Normal"):
        pts = [p for p in points if p["status"] == st_name]
        if not pts:
            continue
        f.add_trace(go.Scatter(
            x=[p["x"] for p in pts], y=[p["y"] for p in pts], mode="markers+text", name=st_name,
            text=[p["name"] for p in pts], textposition="top center", textfont=dict(size=10, color=T()["muted"]),
            customdata=[[p["name"], p["size"]] for p in pts], cliponaxis=False,
            marker=dict(size=[10 + 3 * min(p["size"], 10) for p in pts], color=sc[st_name][0], opacity=0.9,
                        symbol=marks[st_name], line=dict(width=1, color=T()["paper"])),
            hovertemplate="<b>%{customdata[0]}</b><br>Starts in %{x:.1f} h<br>SLA exposure %{y:.1f}"
                          "<br>Gap %{customdata[1]:g} people-h<extra>" + st_name + "</extra>"))
    f.update_layout(hovermode="closest", showlegend=n > 1)
    xs, ys = [p["x"] for p in points] or [0], [p["y"] for p in points] or [0]
    f.update_xaxes(title="Hours until the gap starts (sooner →)", autorange=False,
                   range=[max(xs) * 1.15 + 1, min(0, min(xs)) - 0.5], showgrid=True, gridcolor=c["grid"])
    f.update_yaxes(title="SLA exposure", range=[0, max(ys) * 1.25 + 1])
    return f


def variance_chart(x, required, scheduled, confirmed, unknown, title=None, height=280,
                   confirmed_label="Checked in (recorded)"):
    """Staffing variance: required (dashed reference) and scheduled (blue) step lines with the shortage tinted;
    recorded presence as soft green bars, and 'started, no record' as light grey bars stacked on top: unknown,
    never counted as absent. Future intervals have no bars (no actual exists yet)."""
    f, c = fig(height, title), C()
    f.add_trace(go.Bar(x=x, y=confirmed, name=confirmed_label, marker_color=c["present"], marker_line_width=0,
                       hovertemplate="%{y:g} people<extra>" + esc(confirmed_label) + "</extra>"))
    f.add_trace(go.Bar(x=x, y=unknown, name="Started, no record (unknown)", marker_color=c["unknown"],
                       marker_line_width=0, hovertemplate="%{y:g} people<extra>No record (unknown)</extra>"))
    _shortage_fill(f, x, required, scheduled)
    f.add_trace(go.Scatter(x=x, y=scheduled, name="Scheduled", line=dict(color=c["series"], width=2.2, shape="hv"),
                           hovertemplate="%{y:g} people<extra>Scheduled</extra>"))
    f.add_trace(go.Scatter(x=x, y=required, name="Required (forecast)",
                           line=dict(color=c["reference"], width=1.6, dash="dash", shape="hv"),
                           hovertemplate="%{y:.1f} people<extra>Required (forecast)</extra>"))
    f.update_layout(barmode="stack", bargap=0.25)
    f.update_xaxes(nticks=10)
    f.update_yaxes(rangemode="tozero")
    return f


# ------------------------------------------------------------------ people: avatars and profile headers
PERSON_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/>'
               '<path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7z"/></svg>')


def initials(name: str | None) -> str:
    """'Rohit Verma' -> 'RV', 'Aarav K.' -> 'AK', 'Deepak' -> 'D'. '' when no usable name."""
    words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in (name or "")).split() if w]
    if not words:
        return ""
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


def avatar(name: str | None, photo_uri: str | None = None, size: str = "md") -> str:
    """Photo if one is supplied, else initials, else a neutral person icon. photo_uri is a data: URI produced by
    shiftsense.photos (already validated and resized on the server), never an outside URL."""
    label = esc(name or "Person")
    if photo_uri and photo_uri.startswith("data:image/"):
        return f'<span class="ss-av {size}" role="img" aria-label="{label}"><img src="{photo_uri}" alt=""></span>'
    ini = initials(name)
    inner = esc(ini) if ini else PERSON_ICON
    return f'<span class="ss-av {size}" role="img" aria-label="{label}">{inner}</span>'


def person(name: str, meta: str = "", photo_uri: str | None = None, size: str = "md") -> str:
    return (f'<div class="ss-person">{avatar(name, photo_uri, size)}<div><div class="n">{esc(name)}</div>'
            + (f'<div class="m">{esc(meta)}</div>' if meta else "") + "</div></div>")


def people(items: list[tuple[str, str, str | None]]):
    """A compact grid of people: (name, meta, photo_uri)."""
    st.markdown('<div class="ss-people">' + "".join(f"<div>{person(n, m, p)}</div>" for n, m, p in items)
                + "</div>", unsafe_allow_html=True)


def profile_header(name: str, lines: list[str], photo_uri: str | None = None, pills: str = ""):
    st.markdown(f'<div class="ss-profile">{avatar(name, photo_uri, "xl")}<div style="min-width:0">'
                f'<div class="n">{esc(name)}</div>' + "".join(f'<div class="m">{esc(x)}</div>' for x in lines)
                + (f'<div class="ss-row" style="margin-top:6px">{pills}</div>' if pills else "")
                + "</div></div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ workflow and provenance
def steps(labels: list[str], current: int):
    """Ordered workflow: steps before `current` are done, `current` is in progress (0-based)."""
    items = []
    for i, lbl in enumerate(labels):
        cls = "done" if i < current else "now" if i == current else ""
        cur = ' aria-current="step"' if i == current else ""
        items.append(f'<li class="{cls}" data-n="{i + 1}"{cur}>{esc(lbl)}</li>')
    st.markdown(f'<ol class="ss-steps" aria-label="Progress">{"".join(items)}</ol>', unsafe_allow_html=True)


EMPTY_ICON = ('<svg viewBox="0 0 40 40" aria-hidden="true"><rect x="6" y="9" width="28" height="24" rx="3"/>'
              '<path d="M6 16h28M13 6v6M27 6v6M14 23h12M14 28h7"/></svg>')


def empty(title: str, hint: str = ""):
    st.markdown(f'<div class="ss-empty" role="note">{EMPTY_ICON}<div><b>{esc(title)}</b>{esc(hint)}</div></div>',
                unsafe_allow_html=True)


DATA_KINDS = {"simulated": ("sim", "Simulated"), "synthetic": ("sim", "Synthetic data"),
              "modelled": ("mod", "Modelled estimate"), "actual": ("act", "Actual"),
              "unavailable": ("na", "Unavailable"), "not_configured": ("na", "Not configured")}


def data_label(kind: str) -> str:
    cls, text = DATA_KINDS[kind]
    return f'<span class="ss-data {cls}">{esc(text)}</span>'



THEME_JS = """<script>
(function () {
  // Apply a theme through Streamlit's own menu (System / Light / Dark), then refresh once so server-drawn charts
  // pick up the new colours. Our CSS follows instantly (light-dark tokens).
  var want = "__WANT__", tries = 0;
  function done() { var r = [...document.querySelectorAll('[data-testid="stSidebar"] button')]
      .find(function (b) { return b.innerText.trim() === "Refresh"; }); if (r) { r.click(); } }
  function go() {
    var b = document.querySelector('[data-testid="stMainMenuButton"]');
    if (!b) { if (tries++ < 40) { setTimeout(go, 150); } return; }
    var item = document.querySelector('[data-testid="stMainMenuItem-theme-' + want + '"]');
    if (!item) { b.click(); }
    setTimeout(function () {
      var it = document.querySelector('[data-testid="stMainMenuItem-theme-' + want + '"]');
      if (it) { it.click(); }
      setTimeout(function () {
        if (document.querySelector('[data-testid="stMainMenuItem-theme-' + want + '"]')) { b.click(); }
        if (document.activeElement) { document.activeElement.blur(); }      // no focus-driven sideways scroll
        var c = document.querySelector('[data-testid="stAppViewContainer"]'); if (c) { c.scrollLeft = 0; }
        setTimeout(done, 150);
      }, 150);
    }, 200);
  }
  go();
})();
</script>"""


def apply_theme(want: str, nonce: str):
    """Switch the viewer's theme to System, Light or Dark (once per call; nonce keeps the script unique)."""
    if want in ("System", "Light", "Dark"):
        st.html(THEME_JS.replace("__WANT__", want) + f"<!-- {esc(nonce)} -->", unsafe_allow_javascript=True,
                width="content")


def avatar_uri(name: str | None, photo_uri: str | None = None) -> str:
    """For image columns in tables: the photo, else an initials badge (SVG), else a person icon."""
    import base64
    if photo_uri and photo_uri.startswith("data:image/"):
        return photo_uri
    t = TOKENS[mode()]
    ini = initials(name)
    body = (f'<text x="32" y="40" text-anchor="middle" font-family="IBM Plex Sans,Arial,sans-serif" font-size="24" '
            f'font-weight="600" fill="{t["accent"]}">{esc(ini)}</text>') if ini else \
        (f'<g fill="{t["accent"]}" opacity=".75"><circle cx="32" cy="24" r="10"/>'
         f'<path d="M14 54c0-11 8-17 18-17s18 6 18 17z"/></g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="31" '
           f'fill="{t["accent-soft"]}" stroke="{t["rule"]}"/>{body}</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


# ------------------------------------------------------------------ calendar patterns (time-based views only)
def week_grid(rows: list[dict], days: list[tuple[int, str]], today: int | None = None, caption: str = ""):
    """A Google-Calendar-like week roster: one row per person, one column per day, compact blocks.
    rows: {name, photo, meta, cells: {day: {"text", "sub", "kind"}}}; kind: shift | leave | off | absent | pending.
    Colour is never the only signal: every block carries its text."""
    head = "".join(f'<div class="ss-wg-h{" now" if d == today else ""}" role="columnheader">{esc(lbl)}</div>'
                   for d, lbl in days)
    body = []
    for r in rows:
        cells = []
        for d, _ in days:
            c = r["cells"].get(d) or {"text": "", "kind": "off"}
            sub = f'<span class="b">{esc(c["sub"])}</span>' if c.get("sub") else ""
            cells.append(f'<div class="ss-wg-c{" now" if d == today else ""}" role="cell">'
                         + (f'<div class="ss-blk {esc(c["kind"])}"><span class="a">{esc(c["text"])}</span>{sub}</div>'
                            if c.get("text") else "") + "</div>")
        body.append(f'<div class="ss-wg-p" role="rowheader">{person(r["name"], r.get("meta", ""), r.get("photo"), "sm")}'
                    f'</div>{"".join(cells)}')
    st.markdown(f'<div class="ss-wg-wrap"><div class="ss-wg" role="table" aria-label="{esc(caption or "Week roster")}" '
                f'style="grid-template-columns: minmax(150px,1.4fr) repeat({len(days)}, minmax(88px,1fr))">'
                f'<div class="ss-wg-h" role="columnheader">Person</div>{head}{"".join(body)}</div></div>',
                unsafe_allow_html=True)


def timeline(items: list[dict], rows: list[str], x_range: tuple, ticks: list[tuple[float, str]], now: float | None = None,
             height=None):
    """Day or multi-day timeline: items {row, start, end, label, kind, detail}; x in hours (numeric).
    kind: shift | gap | leave | absent | after. A 'now' line marks the current time."""
    t, c = T(), C()
    colors = {"shift": c["series"], "gap": t["crit"], "leave": t["warn"], "absent": t["crit"], "after": t["ok"],
              "unknown": t["neutral"]}
    f = fig(height or max(160, 40 + 30 * len(rows)))
    for it in items:
        f.add_trace(go.Bar(y=[it["row"]], x=[it["end"] - it["start"]], base=[it["start"]], orientation="h",
                           marker_color=colors.get(it.get("kind", "shift"), c["series"]), marker_line_width=0,
                           opacity=0.85 if it.get("kind") != "gap" else 0.75, showlegend=False,
                           text=[it.get("label", "")], textposition="inside", insidetextanchor="start",
                           textfont=dict(color=t["paper"], size=10), customdata=[[it.get("ref", "")]],
                           hovertemplate=f"<b>{esc(it['row'])}</b><br>{esc(it.get('detail') or it.get('label', ''))}"
                                         "<extra></extra>"))
    if now is not None:
        f.add_vline(x=now, line_width=1.5, line_dash="dot", line_color=t["crit"])
        f.add_annotation(x=now, y=1.0, yref="paper", text="now", showarrow=False, yanchor="bottom",
                         font=dict(size=10, color=t["crit"]))
    f.update_layout(barmode="overlay", hovermode="closest", bargap=0.35, margin=dict(l=4, r=8, t=18, b=4))
    f.update_xaxes(range=list(x_range), tickvals=[v for v, _ in ticks], ticktext=[l for _, l in ticks],
                   showgrid=True, gridcolor=c["grid"])
    f.update_yaxes(autorange="reversed", showgrid=False, categoryorder="array", categoryarray=rows)
    return f


def agenda(items: list[dict]):
    """Chronological list: {time, title, meta, status, pill} -> compact agenda rows."""
    if not items:
        empty("Nothing scheduled", "Events appear here in time order.")
        return
    st.markdown('<div class="ss-agenda" role="list">' + "".join(
        f'<div class="ss-ag-r" role="listitem"><div class="tm">{esc(i["time"])}</div><div class="bd">'
        f'{i.get("who", "")}<div class="tx"><b>{esc(i["title"])}</b>'
        + (f' <span class="ss-muted">{esc(i["meta"])}</span>' if i.get("meta") else "")
        + f'</div></div><div class="st">{pill(i["pill"], i["status"])}</div></div>' for i in items) + "</div>",
        unsafe_allow_html=True)


# ------------------------------------------------------------------ staffing coverage matrix
MATRIX_STATES = [("Covered", "scheduled ≥ required"), ("Tight", "95–99% of required"),
                 ("Short", "85–94% of required"), ("Critical", "below 85% of required"), ("No demand", "nothing required")]


def matrix_bins(req, sch, bin_size: int):
    """Aggregate per-30-minute heads into bins of `bin_size` intervals. Per bin: mean required, mean scheduled
    (people), coverage % = Σ min(scheduled, required) / Σ required (the app's coverage definition), peak shortfall
    = max(required − scheduled) over the bin's half-hours."""
    import numpy as np
    req, sch = np.asarray(req, float), np.asarray(sch, float)
    q, n = req.shape
    nb = int(np.ceil(n / bin_size))
    out = {k: np.zeros((q, nb)) for k in ("req", "sch", "cov", "peak")}
    for b in range(nb):
        sl = slice(b * bin_size, min(n, (b + 1) * bin_size))
        r, s = req[:, sl], sch[:, sl]
        out["req"][:, b], out["sch"][:, b] = r.mean(axis=1), s.mean(axis=1)
        need = r.sum(axis=1)
        out["cov"][:, b] = np.where(need > 0, np.minimum(r, s).sum(axis=1) / np.where(need > 0, need, 1) * 100, np.nan)
        out["peak"][:, b] = np.clip(r - s, 0, None).max(axis=1)
    return out


def matrix_state(cov: float) -> int:
    """0 Covered (100%), 1 Tight (95–99.9%), 2 Short (85–94.9%), 3 Critical (<85%), 4 No demand. Thresholds are the
    app's existing coverage bands (coverage_status: ≥95 normal, ≥85 warning)."""
    import math
    if cov is None or (isinstance(cov, float) and math.isnan(cov)):
        return 4
    return 0 if cov >= 99.95 else 1 if cov >= 95 else 2 if cov >= 85 else 3


def coverage_matrix(rows: list[str], cols: list[str], agg: dict, height=None):
    """Rows = queues, columns = time bins. Each cell: scheduled / required people (means over the bin), its state
    in colour AND words in the tooltip, and the peak shortfall. Click a cell to open its detail."""
    import numpy as np
    t, c = T(), C()
    cov = agg["cov"]
    z = np.vectorize(lambda v: matrix_state(v))(cov).astype(float)
    colors = [c["div_mid"], "#e9d9a8" if mode() == "light" else "#4a4026", c["div_neg2"], c["div_neg"],
              t["neutral-bg"]]
    if mode() == "light":
        colors[0] = "#dcebdf"
    else:
        colors[0] = "#1f3a2a"
    scale = []
    for i, col in enumerate(colors):
        scale += [[i / 5, col], [(i + 1) / 5, col]]
    def fmt(v):
        return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"
    full = [[("—" if z[i, j] == 4 else f"{fmt(agg['sch'][i, j])}/{fmt(agg['req'][i, j])}") for j in range(len(cols))]
            for i in range(len(rows))]
    if len(cols) <= 12:                 # room for "scheduled/required" in every cell
        text = full
    else:                               # dense view: mark only short cells with their peak shortfall
        text = [[(f"−{fmt(agg['peak'][i, j])}" if z[i, j] in (1, 2, 3) and agg["peak"][i, j] > 0 else "")
                 for j in range(len(cols))] for i in range(len(rows))]
    names = [s for s, _ in MATRIX_STATES]
    custom = [[[names[int(z[i, j])], "—" if np.isnan(cov[i, j]) else f"{cov[i, j]:.0f}%",
                fmt(agg["peak"][i, j]), i, j] for j in range(len(cols))] for i in range(len(rows))]
    f = fig(height or max(170, 54 + 34 * len(rows)))
    f.add_trace(go.Heatmap(z=z, x=cols, y=rows, zmin=0, zmax=5, colorscale=scale, showscale=False, xgap=2, ygap=2,
                           text=text, texttemplate="%{text}",
                           textfont=dict(size=10 if len(cols) <= 12 else 9, color=t["ink"]),
                           customdata=[[c_ + [full[i][j]] for j, c_ in enumerate(row)] for i, row in enumerate(custom)],
                           hovertemplate="<b>%{y} · %{x}</b><br>%{customdata[0]} · coverage %{customdata[1]}"
                                         "<br>Scheduled / required: %{customdata[5]} people (average)"
                                         "<br>Peak shortfall: %{customdata[2]} people<extra></extra>"))
    f.update_layout(hovermode="closest", margin=dict(l=4, r=4, t=8, b=4))
    step = 1 if len(cols) <= 12 else max(2, len(cols) // 4)          # dense: about four time labels across
    f.update_xaxes(showline=False, showgrid=False, side="top", tickangle=0, tickmode="array",
                   tickvals=list(cols)[::step], ticktext=list(cols)[::step])
    f.update_yaxes(autorange="reversed", showgrid=False)
    return f


def matrix_legend() -> str:
    sw = {"Covered": "#dcebdf" if mode() == "light" else "#1f3a2a",
          "Tight": "#e9d9a8" if mode() == "light" else "#4a4026",
          "Short": C()["div_neg2"], "Critical": C()["div_neg"], "No demand": T()["neutral-bg"]}
    return ('<div class="ss-row" style="gap:14px;font-size:.78rem;color:var(--muted)">' + "".join(
        f'<span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:{sw[n]};'
        f'border:1px solid var(--rule);vertical-align:-2px;margin-right:5px"></span><b style="color:var(--ink)">'
        f'{esc(n)}</b> {esc(d)}</span>' for n, d in MATRIX_STATES) + "</div>")
