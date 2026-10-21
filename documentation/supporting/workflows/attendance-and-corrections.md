# Workflow: attendance and corrections

**Owners:** Agent (check-in), Team Lead (team view), HR (corrections). Implementation: `attendance.py`, `services.check_in_self`, `hr.py`.

| Source | Value stored | Notes |
|---|---|---|
| Agent checks in (signed in, own shift, window 30 min before to 1 h after start, once) | `checkin` | The only genuine check-in |
| Team lead records it | `confirmed` | via `services.correct_attendance`, inside the lock window |
| Attendance feed file | `feed` | Never overwrites an existing value |
| Demo scenario simulation | `in` / no-show | Exists only in the demo copy |
| Nothing recorded after the start | *No attendance record* | Unknown; never assumed absent or present |

**48-hour lock.** 48 h after the shift ends the record locks (`attendance.lock_hours`). After that only HR can change
it, through a correction request:

1. Team lead (own team) or the agent (own record) asks for a correction with a reason; one pending per shift.
2. HR (not the requester, not their own record) applies or rejects with a reason. The record must be unchanged since
   the request.
3. The original value is kept in history; absence counts are re-checked; requester and agent are notified.

Agents can also use *Report a record problem* (`services.report_discrepancy`), which is audited.
