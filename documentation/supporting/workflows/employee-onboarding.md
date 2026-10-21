# Workflow: employee onboarding

**Owner:** HR. **Supporting roles:** Admin (account), Ops (contract approval), Analyst (re-plan).

| Step | Who | Where in the app | Server action / rule |
|---|---|---|---|
| 1. Create the employee record | HR | HR → People data → Employees (form, Excel/CSV upload, paste, or the drop folder `data/inbox/employees/`) | `convert.py` validates every row; mandatory fields (ID, name, team, primary queue, contract) are never invented; rows with errors are held back in a rows-to-fix file |
| 2. Skills | HR | same record: primary queue, other trained queues, skill level per queue | Eligibility is a **hard rule**: the planner and recovery never place anyone on a queue they are not trained for |
| 3. Contract | HR proposes; Ops approves | HR → People data → Contracts | Contract limits can never be looser than the company-wide floor (`config/policy.yaml`) |
| 4. Effective date | HR | change set preview → approve → apply | `dataflow.py`: applied on the effective date; near days raise alerts, later days produce a re-plan notice for the Analyst |
| 5. Profile photo (optional) | HR | Employee profile → Photo | JPEG/PNG/WebP, ≤ 5 MB, resized, metadata stripped; initials when absent |
| 6. Opening leave balance | HR, verified by a second HR user | HR → Leave & balances | Unverified balances send paid-leave requests to HR review |
| 7. Sign-in account | Admin | Admin → People & accounts → New account | Temporary password, changed at first sign-in; or single sign-on by matching email; role always comes from ShiftSense |
| 8. Plan | Analyst | Planning → Draft → Validate → Publish | New person is used from the next published plan |

Every step writes an audit row. HR is told nothing it cannot see; the Analyst receives a notice with the effect of
each applied people-data change on the plan (`services._tell_analyst`).
