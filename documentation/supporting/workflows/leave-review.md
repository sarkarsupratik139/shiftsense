# Workflow: leave review

**Roles:** Agent requests; HR reviews when a trigger fires; Team Lead (own team) or Ops decides.
Implementation: `requests.py`, `workflow.submit_leave`, `hr.evaluate_triggers`, `services.decide_request`.

1. **Request.** Agent → Requests (form or free text such as "Sick leave on Friday"). Day from tomorrow; one open
   request per day.
2. **Coverage check.** Green = team stays covered; Amber = thin; Red = short, with alternatives offered (swap, other
   day, shift tweak).
3. **Auto-approve** only when coverage is Green, no HR trigger fires and a verified, sufficient balance exists (or the
   leave is unpaid). One day is debited in the same transaction.
4. **HR review triggers** (configurable, HR policy): unverified or insufficient paid balance; more than 5 consecutive
   calendar days; sick leave of more than 2 days. HR recommends (approve / reject / no objection / more information)
   with a note; tasks can be reassigned.
5. **Decision.** Team lead or Ops approves or declines (reason). Approval is refused while an HR review task is open
   or after the day started, and hard rules are re-checked at commit.
6. **After.** Agent can withdraw while pending; an approved request can be revoked before the day starts (shift
   restored, debit credited back).

**Privacy.** Team leads and Ops see "leave (details held by HR)": the sick-leave type and the agent's note are
visible only to HR and the agent.
