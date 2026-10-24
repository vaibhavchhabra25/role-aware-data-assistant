# Assumptions and missing-data policy

Facts below describe the prepared local data (see `docs/data_preparation.md`). Raw-data findings are in `docs/data_quality.md`.

## Missing-data policy

One rule, applied everywhere:

> **Never guess a missing value. Leave the row out of any calculation that needs the
> missing field, and say how many rows were left out.**

In practice:

| Situation | What the assistant does | Caveat shown |
|---|---|---|
| A metric needs a field that is NULL or blank (`''`, `{}`, `[]`) | Excludes the row from that metric | "N rows excluded because X is not recorded" |
| A grouping field is missing (e.g. item has no sub-category) | Keeps the row and groups it as **Not recorded** | None, since it's visible in the result |
| Quantities in different units would be added together (pcs + kg) | Groups by unit instead of summing | "Quantities are shown per unit" |
| A value can be derived exactly from other fields | Derives it, e.g. tranche amount = % × PO total | None |

Blanks are treated the same as NULL.

## Business definitions

- **Today**: the as-of date, **21 Aug 2026** (`DATA_AS_OF`), not the wall clock. The company data ends that day; every relative period ("last month", "overdue") is resolved against it, and answers say "as of 21 Aug 2026".
- **Current stock**: `inv_current_stock.quantity` per item per warehouse, for active items in active warehouses unless the question asks otherwise.
- **Below reorder level**: `quantity < min_stock_level` where `min_stock_level > 0`. A level of 0 means "not set" (310 rows, items with no recent consumption); those rows are excluded and counted in a caveat.
- **Consumption**: outbound transactions (`transaction_type = 'outbound'`, i.e. issues to the shop floor or dispatch). Adjustments (`opening_balance`, `stock_count`) and transfers don't count as consumption.
- **Receipts**: inbound transactions only. Stock counts and opening balances are adjustments, not receipts.
- **Open PO**: status `placed` or `partial`. `received` means closed.
- **Expected delivery date**: stored as a JSON list because it gets revised. The **last** entry is the current expectation and the first is the original promise.
- **Overdue PO**: open, and its current expected delivery date is before today. POs with no expected date can't be judged and are counted in a caveat.
- **Supplier delay**: receipt date (`proc_po_receipts.received_at`) minus the PO's original expected delivery date, in days.
- **Pending quantity**: `quantity_ordered − quantity_received`, floored at 0. 5 lines were over-received.
- **Spend**: `proc_purchase_orders.total_amount`, in INR. Tax is 0 on every PO, so spend is before GST; answers say so.
- **Stock value** (Owner only): quantity × latest PO unit price for the item. Items never bought on a PO are excluded and counted.

## Known data issues

- **Prepared data.** Test writes by other candidates were removed, the 29 Jul stock count was re-typed, and opening balances were dated at the start of the ledger. Full reasoning in `docs/data_preparation.md`.
- **Synthetic history.** POs from Feb–Jul 2026 (numbers `PO-2026-H…`) and their deliveries and issues are synthetic, built from real suppliers, items and prices. Real POs start in August. Answers about trends before August rest mostly on synthetic data.
- **Units.** PO line unit labels are standardised during data preparation (`KG` → `kg`, `Nos.` → `pcs`, …). 8 items were bought in a different unit from the item master (e.g. kg for an item counted in pcs); those quantities keep the PO's unit. Quantities are only added or ranked within one unit; comparisons across units use ₹ value.
- **Supplier code**: missing for every supplier. Suppliers are identified by name.
- **HSN code**: missing on 68% of items. It isn't used for analysis.
- **Unit cost**: missing on 30% of stock movements. It isn't used for valuation; PO prices are used instead.
- **Near-duplicate item**: "Electromagnet Solenoid for AFC Mechanism" exists as `BO-EL-0193` (name has a trailing space) and `NW-33051`. Names are unique only because of the space. Item code is the identifier; the agent trims names before matching.
- **User IDs** (`created_by`, `received_by`, …) don't link to any users table and are never shown.
- **`is_synthetic` flag**: carried by most movements and stock rows, including the company's own generated rows. It's treated as normal data and not shown to users.

## Role-scope assumptions

See `backend/config/roles.yaml`. The main judgement calls:

- Inventory sees **no** procurement tables. Every PO receipt and unexpected receipt already appears as an inbound stock movement, so Inventory can see what came in without seeing vendors or prices.
- Procurement sees **no** stock levels or movements. Questions that combine "what's low on stock and who should we buy it from" are Owner questions.
- Item, category and warehouse names are shared reference data. They hold no commercial or operational figures.
