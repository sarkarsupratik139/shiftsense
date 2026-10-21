"""Shared screen pieces for data input and output: record forms, the conversion preview, change approval,
situation reporting. Every action calls services (permissions are checked there)."""
from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import convert, dataflow, exports, refdata, services
from shiftsense.schemas import DOMAINS, RULE_LABELS, SITUATION_TYPES

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LEVEL_STATUS = {"Error": "Critical", "Check": "Warning", "Corrected": "Info"}


def sim_date(w) -> date:
    return datetime.strptime(dataflow.as_of(w), "%Y-%m-%d").date()


def display(domain: str, rec: dict) -> dict:
    """A record with people-friendly labels and values."""
    d = DOMAINS[domain]
    out = {}
    for f in d.fields:
        v = rec.get(f.name)
        if domain == "volumes" and f.name == "time":
            continue
        if isinstance(v, list):
            v = (f"{v[0] * 100:g}% in {v[1]:g}s" if f.kind == "sla" else
                 f"{int(v[0]):02d}:00–{int(v[1]):02d}:00" if f.kind == "hours" else ", ".join(map(str, v)))
        elif f.kind == "weekday" and isinstance(v, int):
            v = WEEKDAYS[v]
        elif f.kind == "bool" and v is not None:
            v = "Yes" if v else "No"
        elif f.kind == "time" and isinstance(v, (int, float)):
            v = f"{int(v):02d}:{int(round(v % 1 * 60)):02d}"
        elif f.kind == "duration_sec" and isinstance(v, (int, float)):
            v = f"{v / 60:g} min"
        out[f.label] = v
    if domain == "volumes" and rec.get("series"):
        out["Calls in the day"] = sum(rec["series"])
    return out


# ------------------------------------------------------------------ record form (one record, any domain)
def record_form(domain: str, ctx: dict, initial: dict | None, key: str, skip: tuple = ()) -> dict:
    """Plain-language widgets for one record. Mandatory fields are marked *. Returns the raw values."""
    d = DOMAINS[domain]
    init = initial or {}
    out = {}
    cols = st.columns(2)
    for i, f in enumerate([f for f in d.fields if f.name not in skip]):
        c = cols[i % 2]
        lab = f.label + (" *" if f.required else "")
        k = f"{key}_{f.name}"
        v = init.get(f.name, f.default)
        if f.kind in ("queue", "queue_new"):
            opts = ctx["queues"] + ([] if f.kind == "queue" else ["(new queue)"])
            pick = c.selectbox(lab, [""] + opts, index=(opts.index(v) + 1) if v in opts else 0, key=k)
            if pick == "(new queue)":
                pick = c.text_input("New queue name (process-language, e.g. Retail-EN)", key=k + "_new")
            out[f.name] = pick or None
        elif f.kind == "queues":
            out[f.name] = c.multiselect(lab, ctx["queues"], default=[q for q in (v or []) if q in ctx["queues"]], key=k)
        elif f.kind == "team":
            opts = ctx["teams"]
            out[f.name] = c.selectbox(lab, [""] + opts, index=(opts.index(v) + 1) if v in opts else 0, key=k) or None
        elif f.kind == "contract":
            opts = ctx["contracts"]
            out[f.name] = c.selectbox(lab, [""] + opts, index=(opts.index(v) + 1) if v in opts else 0, key=k) or None
        elif f.kind == "shift":
            opts = ctx["shifts"]
            out[f.name] = c.selectbox(lab, opts, index=opts.index(v) if v in opts else 0, key=k)
        elif f.kind == "shifts":
            out[f.name] = c.multiselect(lab, ctx["shifts"], default=[s for s in (v or []) if s in ctx["shifts"]], key=k,
                                        help="Leave empty for all shift types")
        elif f.kind == "weekday":
            out[f.name] = WEEKDAYS.index(c.selectbox(lab, WEEKDAYS, index=int(v) if isinstance(v, int) else 6, key=k))
        elif f.kind == "bool":
            out[f.name] = c.checkbox(lab, value=bool(v) if v is not None else False, key=k)
        elif f.kind == "choice":
            opts = list(f.choices)
            out[f.name] = c.selectbox(lab, opts, index=opts.index(v) if v in opts else 0, key=k)
        elif f.kind == "date":
            dv = datetime.strptime(v, "%Y-%m-%d").date() if isinstance(v, str) and v else None
            out[f.name] = c.date_input(lab, value=dv, key=k, format="DD/MM/YYYY")
        elif f.kind == "rule":
            opts = list(RULE_LABELS)
            out[f.name] = c.selectbox(lab, opts, index=opts.index(v) if v in opts else 0, key=k,
                                      format_func=lambda r: f"{RULE_LABELS[r][0]} ({RULE_LABELS[r][1]})")
        elif f.kind in ("int", "float", "percent", "percent_change"):
            out[f.name] = c.text_input(lab, value="" if v is None else str(v), key=k)
        elif f.kind == "sla":
            out[f.name] = c.text_input(lab, value=f"{v[0] * 100:g}/{v[1]:g}" if isinstance(v, list) else "",
                                       key=k, placeholder="80/20 or 80% in 20s")
        elif f.kind == "hours":
            out[f.name] = c.text_input(lab, value=f"{int(v[0]):02d}:00-{int(v[1]):02d}:00" if isinstance(v, list) else "",
                                       key=k, placeholder="09:00-21:00 or 24x7")
        elif f.kind == "duration_sec":
            out[f.name] = c.text_input(lab, value=f"{v:g}" if isinstance(v, (int, float)) else "", key=k,
                                       placeholder="6 min or 360 s")
        else:
            out[f.name] = c.text_input(lab, value="" if v is None else (", ".join(v) if isinstance(v, list) else str(v)),
                                       key=k)
    return out


def form_to_result(domain: str, raw: dict, ctx: dict) -> convert.Result:
    """Run form values through the same conversion and checks as files."""
    d = DOMAINS[domain]
    row = {}
    for f in d.fields:
        v = raw.get(f.name)
        if isinstance(v, date):
            v = v.isoformat()
        if isinstance(v, list):
            v = "; ".join(map(str, v))
        if f.kind == "weekday" and isinstance(v, int):
            v = WEEKDAYS[v]
        if f.kind == "bool" and isinstance(v, bool):
            v = "Yes" if v else "No"
        if v in ("", None):
            continue
        row[f.label] = v
    mapping = {f.label: f.name for f in d.fields}
    df = pd.DataFrame([row]) if row else pd.DataFrame([{}])
    return convert.convert_frame(df, d, {c: mapping[c] for c in df.columns if c in mapping}, ctx, first_row=1)


# ------------------------------------------------------------------ conversion preview
def show_result(res: convert.Result, key: str):
    s = res.summary()
    st.markdown(f'{kit.pill(str(s["usable"]) + " ready", "Normal")} '
                f'{kit.pill(str(s["held"]) + " held back", "Critical" if s["held"] else "Neutral")} '
                f'{kit.pill(str(s["checks"]) + " to check", "Warning" if s["checks"] else "Neutral")} '
                f'{kit.pill(str(s["corrected"]) + " corrected", "Info" if s["corrected"] else "Neutral")} '
                f'<span class="ss-muted">· converted by {kit.esc(s["converted_by"])}</span>', unsafe_allow_html=True)
    if res.issues:
        grouped = {}
        for i in res.issues:
            k = (i.level, i.field, i.message if i.row is None else _generic(i.message))
            grouped.setdefault(k, []).append(i.row)
        rows = [{"Level": lv, "Field": DOMAINS[res.domain].field(fl).label if fl in
                 [f.name for f in DOMAINS[res.domain].fields] else fl, "What": msg,
                 "Rows": ", ".join(str(r) for r in rs[:12] if r) + (" …" if len(rs) > 12 else "") or "whole file",
                 "Count": len(rs)} for (lv, fl, msg), rs in grouped.items()]
        order = {"Error": 0, "Check": 1, "Corrected": 2}
        st.dataframe(pd.DataFrame(sorted(rows, key=lambda r: order.get(r["Level"], 3))), hide_index=True,
                     width="stretch")
    if res.rows:
        st.caption(f"Converted records (first 200 of {len(res.rows)}), in ShiftSense's format:")
        st.dataframe(pd.DataFrame([display(res.domain, r) for r in res.rows[:200]]), hide_index=True, width="stretch",
                     height=min(40 + 35 * min(len(res.rows), 200), 360))
    if res.held:
        st.download_button("Download rows to fix (Excel)", convert.rows_to_fix(res), "rows_to_fix.xlsx",
                           key=f"{key}_fix", help="The held-back rows with a 'What to fix' column; fix and upload again")


def _generic(msg: str) -> str:
    return msg if len(msg) < 60 else msg[:60] + "…"


def mapping_editor(res: convert.Result, key: str) -> dict | None:
    """Show how columns were matched; let the person change any of them. Returns a manual mapping if changed."""
    if not res.mapping:
        return None
    d = DOMAINS[res.domain]
    labels = {"": "— ignore —", **{f.name: f.label + (" *" if f.required else "") for f in d.fields}}
    how = {"saved": "remembered", "exact": "exact name", "synonym": "known name", "close": "close spelling",
           "ai": "AI", "manual": "you", "unmatched": "not matched", "ignored": "ignored"}
    with st.expander("Column matching" + (" · check unmatched columns" if any(v is None for v in res.mapping.values())
                                          else ""), expanded=any(v is None for v in res.mapping.values())):
        new, changed = {}, False
        for col, fld in res.mapping.items():
            c1, c2, c3 = st.columns([3, 3, 2])
            c1.markdown(f"**{kit.esc(col)}**")
            opts = list(labels)
            pick = c2.selectbox("Field", opts, index=opts.index(fld or ""), format_func=labels.get,
                                key=f"{key}_map_{col}", label_visibility="collapsed")
            c3.caption(how.get(res.mapping_method.get(col, ""), ""))
            new[col] = pick or None
            changed |= (pick or None) != fld
        if changed and st.button("Re-check with these matches", key=f"{key}_remap"):
            return new
    return None


# ------------------------------------------------------------------ impact
def show_impact(imp: dict):
    if imp.get("error"):
        st.error(imp["error"])
        return
    c = imp.get("counts", {})
    tiles = [{"label": "New", "value": c.get("new", 0)}, {"label": "Changed", "value": c.get("changed", 0)},
             {"label": "Removed", "value": c.get("delete", 0), "status": "Warning" if c.get("delete") else None},
             {"label": "Unchanged", "value": c.get("same", 0)}]
    if "release_near" in imp:
        tiles += [{"label": "Alerts today/tomorrow", "value": imp["release_near"],
                   "status": "Warning" if imp["release_near"] else None},
                  {"label": "Later shifts released", "value": imp["release_later"],
                   "status": "Warning" if imp["release_later"] else None}]
    if "gap_before_ph" in imp:
        tiles += [{"label": "Staffing gap (people-h)", "value": f'{imp["gap_before_ph"]:g} → {imp["gap_after_ph"]:g}',
                   "status": "Warning" if imp["gap_after_ph"] > imp["gap_before_ph"] else None}]
    kit.kpis(tiles)
    for k in ("note", "queue_change"):
        if imp.get(k):
            st.info(imp[k])
    if imp.get("released"):
        st.caption("Shifts this change makes impossible (today and tomorrow become alerts with a fix; later days go "
                   "into a re-plan recommendation):")
        st.dataframe(pd.DataFrame(imp["released"]), hide_index=True, width="stretch", height=200)
    if imp.get("requests_affected"):
        st.caption(f"{imp['requests_affected']} pending request(s) fall on the new leave days.")


# ------------------------------------------------------------------ approvals
def approvals(w, can_decide: bool, key: str, domains: tuple | None = None):
    rows = [r for r in services.data_changes(w, ui.user(), status="pending")
            if domains is None or r["domain"] in domains]
    if not rows:
        st.caption("No data changes waiting for a decision.")
        return
    for r in rows:
        with st.container(border=True):
            c = r["summary"] or {}
            st.markdown(f'{kit.pill("Waiting", "Warning")} <b>#{r["id"]} · {kit.esc(DOMAINS[r["domain"]].label)}</b> '
                        f'<span class="ss-muted">· {kit.esc(r["submitted_by"])} · effective {kit.esc(r["effective_from"])}'
                        f' · {c.get("new", 0)} new, {c.get("changed", 0)} changed, {c.get("delete", 0)} removed</span>',
                        unsafe_allow_html=True)
            if r["reason"]:
                st.caption(f"Reason: {r['reason']}")
            with st.expander("Details and impact"):
                show_impact(r["impact"] or {})
                show_ops(r)
            if can_decide:
                why = st.text_input("Reason (required to reject)", key=f"{key}_why_{r['id']}")
                b1, b2 = st.columns(2)
                for label, ok in (("Approve", True), ("Reject", False)):
                    if (b1 if ok else b2).button(label, key=f"{key}_{label}_{r['id']}", type="primary" if ok else "secondary",
                                                 width="stretch"):
                        try:
                            with ui.mutate(ui.live_world()):
                                out = services.decide_change(ui.live_world(), ui.user(), r["id"], ok, why)
                            ui.flash(f"Change #{r['id']} {'approved · ' + services._outcome_text(out.get('outcome', {})) if ok else 'rejected'}")
                        except (ValueError, services.Denied) as e:
                            st.error(str(e))


def show_ops(r: dict, limit: int = 100):
    ops = r.get("ops") or []
    if not ops:
        return
    rows = []
    for o in ops[:limit]:
        after = display(r["domain"], o["data"]) if o["op"] != "delete" else {}
        before = display(r["domain"], o["before"]) if o.get("before") else {}
        diff = "; ".join(f"{k}: {before.get(k)} → {v}" for k, v in after.items() if before and before.get(k) != v)
        rows.append({"Record": o["key"], "Change": o["kind"], "What changes": diff if o["kind"] == "changed" else
                     ("new record" if o["kind"] == "new" else "removed")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(40 + 35 * len(rows), 300))


# ------------------------------------------------------------------ situations
def situation_form(w, key: str, allowed_types: list[str]):
    """Only what is always known is mandatory (what, where, when it starts); the rest is optional."""
    ctx = services.data_context(w, ui.user())
    typ = st.selectbox("Situation", allowed_types, format_func=lambda t: SITUATION_TYPES[t][0], key=f"{key}_type")
    label, need, optional, _ = SITUATION_TYPES[typ]
    st.caption("Mandatory: " + ", ".join({"start": "when it starts", "end": "when it ends", "queues": "queues affected",
                                          "where": "teams or agents affected", "volume_pct": "volume change %",
                                          "where_or_queues": "what it affects", "rule_key": "which rule",
                                          "rule_value": "the temporary value"}[n] for n in need)
               + ". Everything else is optional and can be updated later.")
    title = st.text_input("Title *", key=f"{key}_title", placeholder=label)
    c1, c2 = st.columns(2)
    pre = lambda k, v: {} if k in st.session_state else {"value": v}      # pre-filled by the quick report
    d0 = c1.date_input("Starts on *", key=f"{key}_d0", format="DD/MM/YYYY", **pre(f"{key}_d0", sim_date(w)))
    t0 = c2.time_input("at", key=f"{key}_t0", step=1800, **pre(f"{key}_t0", time(0, 0)))
    unknown = st.checkbox("End not known yet", value="end" not in need, key=f"{key}_unk",
                          disabled="end" in need)
    end = None
    if not unknown:
        c3, c4 = st.columns(2)
        d1 = c3.date_input("Ends on" + (" *" if "end" in need else ""), value=sim_date(w), key=f"{key}_d1",
                           format="DD/MM/YYYY")
        t1 = c4.time_input("at", value=time(23, 30), key=f"{key}_t1", step=1800)
        end = f"{d1.isoformat()} {t1.strftime('%H:%M')}"
    rec = {"type": typ, "title": title.strip(), "start": f"{d0.isoformat()} {t0.strftime('%H:%M')}", "end": end}
    if unknown:
        rec["review_hours"] = st.select_slider("Remind Ops to update it every", [1, 2, 3, 4, 6, 8, 12],
                                               value=int((w.policy.get("recovery") or {}).get("situation_review_hours", 2)),
                                               format_func=lambda h: f"{h} h", key=f"{key}_rev")
    fields = set(need) | set(optional)
    if fields & {"queues", "where_or_queues"} or typ in ("holiday",):
        rec["queues"] = st.multiselect("Queues affected" + (" *" if "queues" in need else " (empty = all)"),
                                       ctx["queues"], key=f"{key}_q")
    if fields & {"where", "where_or_queues"}:
        mine = ui.user().get("team") if ui.user()["role"] == "team_lead" else None
        tkey = f"{key}_tm"
        rec["teams"] = st.multiselect("Teams or sites affected" + (" *" if "where" in need else ""),
                                      [mine] if mine else ctx["teams"], key=tkey,
                                      **({} if tkey in st.session_state else {"default": [mine] if mine else []}))
    if "agents" in optional or typ == "training":
        pool = w.data["agents"]
        if ui.user()["role"] == "team_lead":
            pool = pool[pool.team == ui.user().get("team")]
        rec["agents"] = st.multiselect("Named agents (optional)", list(pool.agent_id), format_func=ui.name,
                                       key=f"{key}_ag")
    nums = st.columns(3)
    i = 0
    for fld, lab in (("volume_pct", "Volume change % (+/-)"), ("capacity_lost_pct", "Capacity lost %"),
                     ("aht_pct", "Handle time change %"), ("absence_pct", "Expected absence %"),
                     ("continuity_sla_pct", "Continuity service target % (lowered target)")):
        if fld in fields:
            v = nums[i % 3].text_input(lab + (" *" if fld in need else ""), key=f"{key}_{fld}")
            i += 1
            if v.strip():
                try:
                    rec[fld] = float(v.replace("%", "").replace("+", ""))
                except ValueError:
                    st.error(f"{lab}: '{v}' is not a number")
    if "closed" in fields:
        rec["closed"] = st.checkbox("Queues closed for the period", key=f"{key}_closed")
    if typ == "rule_exception":
        rk = list(RULE_LABELS)
        rec["rule_key"] = st.selectbox("Rule *", [k for k in rk if not k.startswith(("governance.", "costs."))],
                                       format_func=lambda r: f"{RULE_LABELS[r][0]} ({RULE_LABELS[r][1]})",
                                       key=f"{key}_rk")
        v = st.text_input("Temporary value *", key=f"{key}_rv")
        if v.strip():
            try:
                rec["rule_value"] = float(v)
            except ValueError:
                st.error(f"'{v}' is not a number")
        st.caption("Applies to the whole plan period while active and lapses at its end; Ops approves it.")
    rec["description"] = st.text_area("Description", key=f"{key}_desc", height=70)
    if typ == "disaster":
        st.caption("Safety first: agents in the affected teams are marked unavailable and are never asked to cover. "
                   "A lowered continuity target needs Ops and an end date.")
    if typ == "protest":
        st.caption("Nobody is assumed absent unless you name agents or set expected absence to 100%; otherwise their "
                   "shifts are shown as at risk and team leads confirm actual absences.")
    if st.button("Report situation", type="primary", key=f"{key}_go"):
        if rec.get("continuity_sla_pct") and not rec.get("end"):
            st.error("Continuity mode needs an end date.")
            return
        try:
            with ui.mutate(ui.live_world()):
                out = services.report_situation(ui.live_world(), ui.user(), {k: v for k, v in rec.items() if v not in
                                                                             (None, [], "")})
            ui.flash(f"Situation recorded (#{out['id']}, {out['status']})"
                     + (f" · {services._outcome_text(out['outcome'])}" if out.get("outcome") else ""))
        except (ValueError, services.Denied) as e:
            st.error(str(e))


def active_situations(w, key: str, can_close: bool):
    sits = [s for s in refdata.current("situations").values() if (s.get("status") or "Active") == "Active"]
    if not sits:
        st.caption("No active situations.")
        return
    rows = [{"Ref": s["id"], "Situation": SITUATION_TYPES.get(s["type"], (s["type"],))[0], "Title": s["title"],
             "Starts": s["start"], "Ends": s.get("end") or "open (not known)",
             "Affects": ", ".join((s.get("queues") or []) + (s.get("teams") or []) + (s.get("agents") or [])) or "all queues",
             "Effect": ", ".join(f"{lbl} {s[f]:+g}%" if f in ("volume_pct", "aht_pct") else f"{lbl} {s[f]:g}%"
                                 for f, lbl in (("volume_pct", "volume"), ("capacity_lost_pct", "capacity lost"),
                                                ("aht_pct", "handle time"), ("absence_pct", "absence"),
                                                ("continuity_sla_pct", "continuity target")) if s.get(f))
             + (" · closed" if s.get("closed") else "")} for s in sits]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if can_close:
        pick = st.selectbox("Close a situation", [""] + [s["id"] for s in sits], key=f"{key}_close",
                            format_func=lambda x: "Choose…" if not x else x)
        if pick and st.button("Close it", key=f"{key}_closego"):
            rec = {**next(s for s in sits if s["id"] == pick), "status": "Closed"}
            try:
                with ui.mutate(ui.live_world()):
                    services.submit_change(ui.live_world(), ui.user(), "situations", [rec], reason="closed",
                                           source="form", converted_by="form")
                ui.flash(f"{pick} closed")
            except (ValueError, services.Denied) as e:
                st.error(str(e))


# ------------------------------------------------------------------ add or update (shared by Data hub and HR)
def add_or_update(w, u, domains: list[str]):
    """Upload / paste / type -> check -> preview impact -> submit. Domains are the ones this role owns."""
    from shiftsense import convert as convert_mod
    convert = convert_mod
    ctx = services.data_context(w, u)
    if not domains:
        st.info("Your role doesn't put reference data in here.")
        return
    dom = st.selectbox("What data?", domains, format_func=lambda d: DOMAINS[d].label, key="dh_dom")
    D = DOMAINS[dom]
    st.caption(D.description + " Mandatory: " + ", ".join(D.field(f).label for f in D.required) + "."
               + (" Needs Ops approval." if D.approval else "")
               + ("" if D.partial_ok else " Applied all-or-nothing."))
    c_how, c_dates = st.columns([2, 2])
    with c_how:
        how = st.segmented_control("How", ["Upload a file", "Paste", "Type it in"], default="Upload a file",
                                   key="dh_how") or "Upload a file"
    day_first = c_dates.radio("Dates in this input", ["Day first (12/10 = 12 Oct)", "Month first (10/12 = 12 Oct)"],
                              horizontal=True, key="dh_datefmt").startswith("Day")
    res_key = f"dh_res_{dom}"
    if how == "Upload a file":
        f = st.file_uploader("Excel or CSV, any layout", type=["xlsx", "xls", "xlsm", "csv", "tsv", "txt"],
                             key=f"dh_file_{dom}")
        if f is not None:
            data = f.getvalue()
            sheets = convert.read_bytes(data, f.name)
            sheet = st.selectbox("Sheet", list(sheets), key=f"dh_sheet_{dom}") if len(sheets) > 1 else None
            if st.button("Read and check", type="primary", key="dh_conv_f"):
                st.session_state[res_key] = services.convert_data(w, u, dom, data=data, filename=f.name, sheet=sheet, day_first=day_first)[0]
                st.session_state[res_key + "_src"] = (f.name, data, sheet)
    elif how == "Paste":
        txt = st.text_area("Paste a table from Excel or Sheets, or plain text such as an email", height=160,
                           key=f"dh_txt_{dom}",
                           placeholder="Employee ID\tFrom\tTo\nA01\t12/10/2026\t14/10/2026\n\n"
                                       "or: Aarav and Sneha are off 12 to 14 Oct for training")
        if txt.strip() and st.button("Read and check", type="primary", key="dh_conv_t"):
            st.session_state[res_key] = services.convert_data(w, u, dom, text=txt, day_first=day_first)[0]
            st.session_state[res_key + "_src"] = ("pasted", None, None)
        if not convert.ai_enabled(dom):
            st.caption("Tables are read with rules. Free text is read with rules for common leave sentences; AI reading "
                       "of other text is switched off (SHIFTSENSE_AI_CONVERT).")
    else:
        current = refdata.current(dom)
        pick = None
        if current and D.key[0] in ("employee_id", "name", "queue", "rule"):
            pick = st.selectbox("Edit an existing record or add a new one", ["(new)"] + sorted(current),
                                key=f"dh_pick_{dom}")
        init = current.get(pick) if pick and pick != "(new)" else None
        with st.form(f"dh_form_{dom}"):
            raw = record_form(dom, ctx, init, key=f"dhf_{dom}_{pick}")
            go = st.form_submit_button("Check", type="primary")
        if go:
            st.session_state[res_key] = form_to_result(dom, raw, ctx)
            st.session_state[res_key + "_src"] = ("form", None, None)

    res = st.session_state.get(res_key)
    if res is not None:
        kit.label("Check")
        manual = mapping_editor(res, f"dh_{dom}")
        if manual is not None:
            name, data, sheet = st.session_state.get(res_key + "_src", (None, None, None))
            if data is not None:
                st.session_state[res_key] = services.convert_data(w, u, dom, data=data, filename=name, sheet=sheet,
                                                                  manual=manual, day_first=day_first)[0]
                st.rerun()
        show_result(res, f"dh_{dom}")
        blocked = bool(res.errors) and not D.partial_ok
        if blocked:
            st.error("This kind of data is applied all-or-nothing: fix the errors above first.")
        if res.rows and not blocked:
            kit.label("Apply")
            c1, c2 = st.columns(2)
            mode = c1.radio("How to apply", ["Add and update", "Replace everything of this kind"], key=f"dh_mode_{dom}",
                            help="Replace removes records that are not in this input (they stay in history).")
            eff = c2.date_input("Effective from", value=sim_date(w), key=f"dh_eff_{dom}", format="DD/MM/YYYY",
                                help="The day the change starts to count. A later date schedules it.")
            reason = st.text_input("Reason" + (" *" if dom in ("contracts", "service_levels", "rules") else ""),
                                   key=f"dh_reason_{dom}")
            mode_k = "replace" if mode.startswith("Replace") else "upsert"
            if st.button("Preview impact", key=f"dh_prev_{dom}"):
                ops, imp = services.preview_change(w, u, dom, res.rows, mode=mode_k, effective_from=eff.isoformat())
                st.session_state[f"dh_imp_{dom}"] = imp
            imp = st.session_state.get(f"dh_imp_{dom}")
            if imp:
                show_impact(imp)
            if st.button("Submit" + (" for approval" if D.approval else " and apply"), type="primary",
                         key=f"dh_go_{dom}"):
                try:
                    issues = [{"row": i.row, "field": i.field, "level": i.level, "message": i.message}
                              for i in res.issues]
                    src = st.session_state.get(res_key + "_src", ("form", None, None))[0]
                    with ui.mutate(w):
                        out = services.submit_change(w, u, dom, res.rows, mode=mode_k, effective_from=eff.isoformat(),
                                                     reason=reason, source=src, converted_by=res.converted_by,
                                                     issues=issues)
                    if res.signature and res.mapping:
                        services.remember_mapping(res.signature, dom, {k: v for k, v in res.mapping.items() if v})
                    for k in (res_key, f"dh_imp_{dom}"):
                        st.session_state.pop(k, None)
                    msg = (f"Change #{out['id']} sent to {'Ops' if out.get('approver') == 'ops_head' else 'the approver'}"
                           if out["status"] == "pending" else
                           f"Change #{out['id']} {out['status']} · {services._outcome_text(out.get('outcome', {}))}")
                    ui.flash(msg)
                except (ValueError, services.Denied) as e:
                    st.error(str(e))


def drop_folder(w, u, key: str):
    """Files waiting in the drop folder that this role may apply, and a button to process them."""
    from shiftsense import dropfolder
    files = dropfolder.waiting()
    st.caption(f"Drop folder: {dropfolder.inbox()} · one sub-folder per kind of data (e.g. employees/, leave/, "
               f"volumes/). {len(files)} file(s) waiting.")
    if files and st.button(f"Process drop folder ({len(files)})", key=f"{key}_drop"):
        try:
            with ui.mutate(ui.live_world()):
                res = dropfolder.process(ui.live_world(), u)
            st.session_state[f"{key}_dropres"] = res
            st.rerun()
        except (ValueError, services.Denied) as e:
            st.error(str(e))
    res = st.session_state.get(f"{key}_dropres")
    if res:
        st.dataframe(pd.DataFrame(res), hide_index=True, width="stretch")



# ------------------------------------------------------------------ quick report, update, calendar
def quick_report(w, key: str, allowed_types: list[str]):
    """A few sentences -> a filled-in situation form (rules first; AI only if switched on and rules found little)."""
    txt = st.text_area("Describe what is happening", key=f"{key}_qr", height=80,
                       placeholder="Heavy flooding in Lucknow, TL-3 and TL-4 can't reach the office from 2 pm")
    if txt.strip() and st.button("Read it", key=f"{key}_qrgo"):
        r = services.read_situation_text(w, ui.user(), txt)
        rec = r["record"]
        if rec["type"] not in allowed_types:
            st.error(f"Your role can't report a {SITUATION_TYPES[rec['type']][0].lower()}. Pick another type below.")
            rec["type"] = allowed_types[0]
        prefix = f"{key}_f"
        st.session_state[f"{prefix}_type"] = rec["type"]
        st.session_state[f"{prefix}_title"] = rec.get("title", "")
        if rec.get("start"):
            d, t = rec["start"].split(" ")
            st.session_state[f"{prefix}_d0"] = datetime.strptime(d, "%Y-%m-%d").date()
            st.session_state[f"{prefix}_t0"] = datetime.strptime(t, "%H:%M").time()
        for f in ("teams", "queues"):
            if rec.get(f):
                st.session_state[f"{prefix}_{'tm' if f == 'teams' else 'q'}"] = rec[f]
        for f in ("volume_pct", "capacity_lost_pct", "absence_pct"):
            if rec.get(f) is not None:
                st.session_state[f"{prefix}_{f}"] = f"{rec[f]:g}"
        st.session_state[f"{prefix}_desc"] = rec.get("description", "")
        st.session_state[f"{key}_qrinfo"] = r
        st.rerun()
    info = st.session_state.get(f"{key}_qrinfo")
    if info:
        st.caption("Read from your text: " + "; ".join(info["found"]) + (". Still needed: " + ", ".join(
            info["missing"]) if info["missing"] else ". Check the form below and report it."))
    situation_form(w, f"{key}_f", allowed_types)


def situation_update(w, key: str):
    """Change an active situation as it develops: end, effect, reminder interval. Re-checked like any change."""
    sits = [s for s in refdata.current("situations").values() if (s.get("status") or "Active") == "Active"]
    if not sits:
        st.caption("No active situations to update.")
        return
    pick = st.selectbox("Situation", [s["id"] for s in sits], key=f"{key}_pick",
                        format_func=lambda i: next(f"{s['id']} · {s['title']}" for s in sits if s["id"] == i))
    s0 = next(s for s in sits if s["id"] == pick)
    rec = dict(s0)
    c1, c2 = st.columns(2)
    open_end = c1.checkbox("Still going on (no end yet)", value=not s0.get("end"), key=f"{key}_open_{pick}")
    if not open_end:
        cur_end = datetime.strptime(s0["end"][:16], "%Y-%m-%d %H:%M") if s0.get("end") else datetime.combine(
            sim_date(w), time(23, 30))
        d1 = c1.date_input("Ends on", value=cur_end.date(), key=f"{key}_d1_{pick}", format="DD/MM/YYYY")
        t1 = c2.time_input("at", value=cur_end.time(), key=f"{key}_t1_{pick}", step=1800)
        rec["end"] = f"{d1.isoformat()} {t1.strftime('%H:%M')}"
    else:
        rec["end"] = None
        rec["review_hours"] = c2.select_slider("Remind Ops every", [1, 2, 3, 4, 6, 8, 12],
                                               value=int(s0.get("review_hours") or 2), key=f"{key}_rev_{pick}",
                                               format_func=lambda h: f"{h} h")
    cols = st.columns(3)
    for i, (fld, lab) in enumerate((("volume_pct", "Volume change %"), ("capacity_lost_pct", "Capacity lost %"),
                                    ("absence_pct", "Expected absence %"))):
        v = cols[i].text_input(lab, value="" if s0.get(fld) is None else f"{s0[fld]:g}", key=f"{key}_{fld}_{pick}")
        rec[fld] = float(v) if v.strip() else None
    note = st.text_input("What changed (required)", key=f"{key}_why_{pick}")
    if st.button("Update situation", key=f"{key}_go_{pick}", type="primary"):
        if not note.strip():
            st.error("Say what changed.")
            return
        try:
            with ui.mutate(ui.live_world()):
                out = services.submit_change(ui.live_world(), ui.user(), "situations",
                                             [{k: v for k, v in rec.items() if v is not None}], reason=note,
                                             source="update form", converted_by="form")
            ui.flash(f"{pick} updated (#{out['id']}, {out['status']})")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


def calendar(w, key: str):
    """Situations and scheduled data changes over the plan period, one bar each."""
    import plotly.express as px
    start = datetime.combine(refdata.start_date(w.policy), time(0, 0))
    end = start + pd.Timedelta(days=w.policy["horizon"]["days"])
    rows = []
    for s in refdata.current("situations").values():
        if (s.get("status") or "Active") != "Active":
            continue
        b = datetime.strptime(s["start"][:16], "%Y-%m-%d %H:%M") if len(s["start"]) > 10 else \
            datetime.strptime(s["start"], "%Y-%m-%d")
        e = datetime.strptime(s["end"][:16], "%Y-%m-%d %H:%M") if s.get("end") else end
        rows.append({"What": f"{s['id']} · {s['title']}"[:48], "Start": max(b, start), "End": min(max(e, b), end),
                     "Kind": SITUATION_TYPES.get(s["type"], (s["type"],))[0] + ("" if s.get("end") else " (open)")})
    for cs in dataflow.sets(status="scheduled"):
        d = datetime.strptime(cs["effective_from"], "%Y-%m-%d")
        rows.append({"What": f"Change #{cs['id']} · {DOMAINS[cs['domain']].label}"[:48], "Start": d,
                     "End": d + pd.Timedelta(hours=6), "Kind": "Scheduled data change"})
    if not rows:
        st.caption("Nothing on the calendar: no active situations and no scheduled data changes.")
        return
    df = pd.DataFrame(rows)
    fig = px.timeline(df, x_start="Start", x_end="End", y="What", color="Kind")
    fig.update_yaxes(autorange="reversed", title=None)
    fig.add_vline(x=datetime.combine(sim_date(w), time(0, 0)) + pd.Timedelta(minutes=30 * (w.now_t % 48)),
                  line_dash="dot")
    fig.update_layout(height=120 + 32 * len(df), margin=dict(l=10, r=10, t=10, b=10), legend_title=None)
    st.plotly_chart(fig, use_container_width=True, key=f"{key}_cal")
    st.caption(f"{len(df)} item(s); the dotted line is now. Open-ended situations run to the end of the plan period.")
    with st.expander("Same as a table"):
        st.dataframe(df.assign(Start=df.Start.dt.strftime("%a %d %b %H:%M"), End=df.End.dt.strftime("%a %d %b %H:%M")),
                     hide_index=True, width="stretch")


def quality_list(w):
    items = refdata.quality(w.policy, dataflow.as_of(w))
    order = {"Critical": 0, "Warning": 1, "Info": 2}
    for lvl, msg in sorted(items, key=lambda x: order.get(x[0], 3)):
        st.markdown(f"{kit.pill(lvl, lvl if lvl != 'Info' else 'Info')} {kit.esc(msg)}", unsafe_allow_html=True)
    if not items:
        st.caption("No data quality issues found.")
