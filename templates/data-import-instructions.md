# Data import instructions

1. **Pick the template.** `employee-import-template.csv` (people) or `call-volume-template.csv` (call history /
   actuals). Other data types (contracts, leave, rules, service levels, situations, leave balances) use the column
   names of the files in `application/basedata/small/`; field definitions are in
   `documentation/supporting/data/data-dictionary.xlsx` (sheet *Reference fields*).
2. **Column names are flexible.** Common synonyms are recognised (e.g. "Emp ID", "Agent ID"); a mapping you confirm
   is remembered for the next file.
3. **Formats.** Dates: `YYYY-MM-DD` or `DD/MM/YYYY`. Times: `HH:MM` (30-minute slots). Lists (trained queues): `;` or
   `,` separated. Yes/No for flags. Daily call totals without a time slot are spread over the day.
4. **Mandatory fields** are never guessed: rows missing them are held back and listed in a downloadable
   *rows to fix* file. Good rows continue when the data type allows partial apply.
5. **Where.**
   - HR: HR → People data (employees, contracts, leave), HR → Leave & balances (balances).
   - Analyst: Data hub (volumes, service levels, rules, situations, attendance feed, base pack).
   - Drop folder: put the file in `data/inbox/<kind>/` and process it from the app, or stage it with
     `scripts/stage_drop_folder.sh <username>`.
6. **Preview → approve → apply.** Changes apply on their effective date. Near days raise staffing alerts; later
   days produce a re-plan notice. Shifts that have started are never changed.
7. **Photos.** Put image files in `data/inbox/photos/` and give the plain file name in *Profile photo*. Missing or
   invalid images give a warning and the initials are used; the record still imports.
