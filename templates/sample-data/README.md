# Sample data

The application ships its own synthetic base data pack in `application/basedata/`. Nothing in it is real: names,
clients ("InsureCo (fictional)", "PaySecure") and volumes are generated (seed 42).

| Pack | People | Queues | Use |
|---|---|---|---|
| `basedata/small` (default) | 50 | 4 (Claims-EN, Claims-HI, Banking-EN, Banking-HI) | demo and tests |
| `basedata/full` | 120 | 6 | larger demonstration; not loaded into live by default |

Files in each pack: `employees.csv`, `contracts.csv`, `leave.csv`, `rules.csv`, `service_levels.csv`,
`call_volumes.csv` (30-minute intervals), `situations.csv`.

Rebuild the packs: `application/scripts/build_base_pack.sh` (`python -m shiftsense.basepack build`).
Load into the app: Analyst → Data hub → Base data.
