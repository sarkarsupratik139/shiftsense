# Workflow: HR case management

**Owner:** HR. Implementation: `hr.py`, `services._case_gate`, `views/hr.py`. Decision Record HR-01..HR-08.

## Restricted HR cases (HR-07)
- Opened by HR with a caseworker who is an active HR user and not the subject.
- Only the assigned caseworker and Senior HR can open a case; everyone else, Admin included, is refused and the
  attempt is logged in `hr_case_access`.
- Entries: comments, evidence references, follow-ups with due dates. Reassign (Senior HR, reason). Close with an
  outcome. States: open → in progress → awaiting information → closed.

## Separations (HR-01)
Record (reason category, effective date) → a **different** HR user confirms → employee "On notice" → near-day shifts
become staffing alerts, later shifts are released and the Analyst is told to re-plan → at 00:00 Asia/Kolkata after
the last working day the account is deactivated and the record becomes "Exited" (idempotent). Rescind with a reason
reactivates the account if this separation deactivated it.

## Other HR records
| Record | Rule |
|---|---|
| Leave balances (HR-02) | Opening balance + source reference, verified by a second HR user; ledger of debits and credits |
| Review triggers (HR-03) | See leave-review.md |
| Attendance lock (HR-04) | 48 h; corrections reviewed by HR |
| Absence flags (HR-05) | 3 qualifying unplanned absences in any 30 days; **off pending validation**; confidential to HR |
| Overtime approval (HR-06) | Off until a threshold is approved |
| Retention (HR-08) | Deferred: no periods set, nothing deleted automatically; register shown in HR policy |

**HR policy** is versioned; only Senior HR saves a version, approving needs a policy reference. No one acts on their
own record.
