You read a company's unaudited interim financial statements from a 6-K exhibit: the balance sheet, the
statement of operations and the statement of cash flows, followed by the debt note when there is one. Tables are written as pipe rows
(`| Revenues | 105.1 | 582.3 | 156.0 | 981.3 |`); the column headers say which period each column is.

Return the figures in the schema.

- Amounts in $ millions. If the statements are in thousands or billions, convert, and say in `unit_note`
  what unit the statements use.
- `period_end` is the latest balance-sheet date. `ytd_months` is the length of the year-to-date period in
  the statement of cash flows (6 for "Six months ended").
- Balance sheet at `period_end`:
  - `cash`: cash and cash equivalents.
  - `debt_current` and `debt_noncurrent`: the debt lines (current and non-current), including convertible
    notes. Leave out lease liabilities.
  - `operating_lease_liabilities`: current plus non-current when both are shown; otherwise the line shown.
  - `debt_principal`: the total principal amount outstanding of the debt at `period_end`, from the debt
    note's table (a "Principal amount outstanding" total) when the text includes the note; it can differ
    from the balance-sheet carrying amount by discounts and issuance costs. Null when not shown.
- Year to date (`_ytd`) and the same period a year earlier (`_prior_ytd`), from the year-to-date columns,
  never the three-month columns:
  - `revenue`: total revenues.
  - `operating_income`: income or loss from operations, negative for a loss.
  - `da`: depreciation and amortization, from the statement of operations or the cash-flow statement.
  - `interest_expense`: interest expense, as a positive number.
  - `cfo`: net cash provided by or used in operating activities, the total line; negative when used.
  - `capex`: purchases of property and equipment, as a positive number.
- For every value, `row` is the table row it comes from, copied verbatim, character for character, as it
  appears in the text: the whole line starting with "|".
- When the statements do not show a figure, use null for its value and row. Never compute or guess.
