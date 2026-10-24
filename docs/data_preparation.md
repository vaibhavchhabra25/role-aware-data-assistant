# Data preparation

This covers how the raw company snapshot becomes the database the assistant runs on: what we changed, the evidence behind each change, and what we deliberately left alone.

```
dumps/source_*.dump            raw snapshot of the 17 company tables (never edited)
   │  backend/scripts/prepare_local_db.sh
   ├─ restore
   ├─ clean_data.sql            steps 1–3: remove test writes, fix mislabelled rows, opening balances
   ├─ seed_extra_data.py        steps 4–5: synthetic procurement history, reorder levels
   ├─ apply_db_roles.sh         per-role read-only logins
   └─ tests/test_data_invariants.py   11 checks that must hold after every rebuild
```

Rebuild any time with `backend/scripts/prepare_local_db.sh`. Run `SKIP_SYNTHETIC=1 backend/scripts/prepare_local_db.sh` for cleaned real data only.

## Principles

Every step below had to pass the same questions:

1. **Would we do this to the client's production data?** If not, why is it still right here?
2. **Does it fix *meaning* or hide *mess*?** We correct rows whose meaning is wrong, such as a stock count recorded as a purchase. We keep the genuine messiness of real operations: missing dates, inconsistent units, over-deliveries. Handling that mess honestly is part of what the assistant is for.
3. **Is it the simplest option that works?** A smaller change wins unless it leaves the data misleading.

Some rules hold for every step:
- The source DB is never written to.
- The raw dump is never edited.
- Every step is a script, so the result is reproducible.
- Every added row can be identified.
- Stock on hand is never changed by the clean-up. That keeps one invariant for every item and warehouse: `stock on hand = sum of all its movements`.

---

## Decision 0: "Today" is 21 Aug 2026

**What we saw.** The real activity stops on 21 Aug 2026: the last stock movement is that day, the last receipt on 20 Aug, and the last PO on 19 Aug. The snapshot was taken on 14 Oct. With the wall-clock date, "last month" would be September, which is empty. Every open PO would also be about two months overdue, which says more about when the data was extracted than about the suppliers.

**Options.**

| Option | For | Against |
|---|---|---|
| Use the wall-clock date | Behaves like a live system | "Last week" and "last month" return nothing. Overdue figures are distorted by the extraction gap. |
| Shift every date forward ~55 days | Data looks current | Touches every date and JSON date column, so it's easy to get wrong. Our answers would no longer match the company's own database, and the evaluators know their data. |
| Fabricate the missing eight weeks | Data looks current | Synthetic rows would outweigh real ones in exactly the period users ask about most. |
| **Fixed as-of date (chosen)** | No data is altered. Answers match the company's DB. Relative dates make sense. | The UI must say "data as of 21 Aug 2026". |

**Decision.** `DATA_AS_OF=2026-08-21` in `.env`. The agent resolves "today", "last month" and "overdue" against it. Nothing in the database may be dated later than it, and an invariant test enforces that.

**Real-world check.** This is standard practice in reporting: every dashboard has a "data refreshed up to" date. In production the setting would simply be the current date, a one-line config change.

---

## Step 1: Remove test writes by other candidates

**What we saw.** The source DB is shared, and some candidates have written to it while testing.
- **Who wrote what:** grouping rows by `created_by` separates the three original users (April–August) from later authors.
- **The test rows:** 21 stock movements in September by accounts named `test_user`, `agent`, `fp`, `v` and `ext`, and 7 more in October by unrecognised users.
- **Test POs:** 8 POs worth ₹102–₹9,250, with 8 lines and 3 receipts.
- **A test supplier:** "ak elec", with no city and no orders.

**Why it matters.** These rows aren't business events. A question like "what did we receive in October" would report a tester's experiment as a real delivery.

**The subtle part.** The test writes also **changed stock on hand** when they were made. For every affected item and warehouse, `last_transaction_at` equals the test row's timestamp, and where the test row is the only movement, stock equals its quantity. So deleting the movements alone would leave stock wrong. We subtract each test row's net quantity from stock and then delete it. Two stock rows that existed only because a tester created them are removed.

**Options considered.**
- *Keep them, and have the assistant filter by author.* Rejected. Every query would need a hidden filter, the LLM would have to remember it, and one miss would show test data to a user.
- *Move them to a quarantine table.* Rejected as unnecessary. The raw dump already keeps them, so they're recoverable.

**Real-world check.** In production you never delete ledger rows. You reverse them with correcting entries, and you stop tests touching production through separate environments. These rows aren't the company's history, though; they're contamination of our copy, and the untouched raw dump is our audit trail. So deleting them here is the right call.

**Result.** −28 movements, −8 POs (and their lines and receipts), −1 supplier, −2 stock rows. Stock still equals the ledger everywhere.

---

## Step 2: The 29 Jul physical count was recorded as purchases

**What we saw.** 239 movements on 29 Jul, all noted *"Physical count reconciliation 29th July 2026"*. They were saved as 223 "unexpected receipt" inbound movements (+244,053 units) and 16 "warehouse issue" outbound movements (−1,030 units). 207 of the 239 items involved had never moved before. This was a stock count that brought on-hand quantities into the system, not deliveries and not consumption.

**Why it matters.** Movement type drives every flow question:
- "What did we receive in July?" would be inflated about 5×, by 244,053 units that never arrived on a truck.
- "Consumption by department" would count count corrections as issues to the shop floor.
- The Owner's "received vs issued" chart would show a fake July spike.

**Options considered.**
- *Leave the rows; tell the agent to exclude rows with that note.* Rejected. It depends on the LLM remembering a magic string in every query, and it fails silently when it forgets.
- *Move the count to an opening-balance date.* Rejected. The count really happened on 29 Jul, so its date is a fact.
- **Re-type (chosen):** `transaction_type = 'adjustment'`, `reference_type = 'stock_count'`. Quantities, dates and notes are unchanged.

**Real-world check.** Yes. A data engineer would fix misclassified transactions with a correcting migration, and would also fix the screen that let a stock count be entered as receipts. Re-typing changes the meaning, which was wrong, and nothing else.

---

## Step 3: The reconciliation rows are the missing opening balances

**What we saw.** 311 adjustments dated 14 Oct 2026 by one unknown user, all noted *"M2 reconciliation adjustment: aligns ledger history with verified physical stock count"*. We first suspected contamination and tested it:
- **Without them, the ledger is impossible.** 310 of 562 stock rows don't match their movements, and **192 item-warehouse histories go below zero** (issues recorded before any stock was).
- **With them, it's exact.** Every stock row matches its movements.
- They're 310 positive rows and one negative.

So these rows hold true information: stock that existed before the ledger began. Only their **date** is wrong. 14 Oct is when someone ran the fix, not when the stock was there.

**Why it matters.**
- Left on 14 Oct, they're "142,791 units adjusted in October", after our as-of date.
- Any "stock over time" view would show negative stock for months.

**Options considered.**
- *Delete them.* Rejected. 192 negative histories, and stock no longer matches the ledger.
- *Keep them on 14 Oct.* Rejected. They'd be future-dated relative to the as-of date and show up as a huge October adjustment.
- **Re-date as opening balances (chosen):** `reference_type = 'opening_balance'`, dated 31 Jan 2026, the start of the ledger.
  - The one negative row can't be an opening balance, so it stays a stock-count correction, dated on the as-of date.
  - After this, one item-warehouse still didn't reconcile. It's a real issue of 50 "PU Flap" from warehouse 132-1, where stock was never recorded, and a test write had been masking it. The same rule applies: we add the missing opening balance, marked as added in data preparation.

**Real-world check.** Yes, this is standard practice. When a system goes live, existing stock is entered as opening balances dated at go-live, so the ledger is complete from day one.

**Result.** Every running balance is now ≥ 0, and stock = ledger everywhere. Both are checked by tests.

---

## Step 4: Synthetic procurement history (Feb–Jul 2026)

**What we saw.** The real procurement history covers **three weeks**: 50 POs placed 3–19 Aug and 58 receipts. That's enough to test role security, but not to answer the questions a procurement manager and the Owner actually ask: spend trends, supplier on-time performance, price movement. The brief explicitly allows fabricating data for this.

**What we generated.** 69 POs with 113 lines, 130 deliveries and 69 payment tranches over February–July, before the real POs. Everything is based on the real data:
- **Suppliers and items:** the 23 real suppliers who have catalogue items, each selling only the items they actually sold us, at their real prices (slightly lower further back, to show mild inflation) and real order sizes.
- **Delivery behaviour:** a profile per supplier (reliable / usual / chronically late). Kingfisher Steels is kept late, as its real receipts were.
- **Payment schedules:** follow each supplier's real payment terms. Suppliers with no recorded terms get **no** schedule; we don't invent terms.
- **Deliberate gaps:** 10% of POs have no expected delivery date, 10% have a revised date, and some deliveries are split or short. Payments due before the as-of date are mostly cleared, but not all.
- **Inventory side:** every synthetic delivery creates the matching stock receipt, exactly as real ones do, and the same quantity is issued to the shop floors over the following weeks. **Each item and warehouse nets to zero, so stock on hand is unchanged.**

**How it's labelled.**
- `created_by = 00000000-0000-4000-8000-000000000001`
- `is_synthetic = true` where the column exists
- `custom_fields = {"synthetic": true}` on POs and lines
- PO numbers in their own series, `PO-2026-H001…`

It's deterministic (fixed seed), and `SKIP_SYNTHETIC=1` builds without it.

**Options considered.**
- *Generate a whole synthetic company.* Rejected. The evaluators would be testing against data that isn't theirs, and every real quirk would disappear.
- *Fill the eight weeks after the real data.* Rejected, see Decision 0.
- *No fabrication.* Considered seriously. Role security, single-number answers and missing-data handling all work on real data alone. But procurement trend and supplier-performance answers would be one data point long. Fabricating only the procurement past, built from real parameters, is the smallest change that makes those questions answerable.

**Real-world check.** Never in production. Here it's demo scaffolding, as the brief allows: clearly labelled, removable, and it never changes a real row or stock on hand. If we had more time, the better route would be to ask the client for an export of their older POs.

---

## Step 5: Reorder levels from actual consumption

**What we saw.** 499 stock rows are flagged `is_synthetic` and carry template levels: 346 at min 25 / max 250, 91 at 250 / 2,500, and so on. The 63 real rows have levels that look hand-set (39 / 294, 9 / 68, …). With the template values, "below reorder level" flagged 46% of all stock rows, which doesn't mean anything.

**Decision.** For the **synthetic rows only**:
- **min** = 3 weeks of average consumption over the last 13 weeks (≈ typical supplier lead time plus a safety week);
- **max** = 8 weeks of consumption.
- Rows with no consumption get 0 / 0, which the assistant treats as "no reorder level set" (see `assumptions.md`).

The 63 hand-set levels are kept.

**What it showed.** 153 rows are below minimum. That looks high, so we checked whether our method caused it:
- 145 of the 153 are at **zero stock**;
- only 1 is below minimum because of synthetic consumption;
- the hand-set rows show the same pattern (25 at zero).

The real data says about 143 item-warehouse combinations were in active use over the summer and have since run out. That's a genuine finding for the Owner, so we did **not** tune the formula to make it look better.

**Real-world check.** Partly. Deriving reorder points from consumption × lead time is standard and is what we'd advise. In production we'd *recommend* the levels to the store manager rather than overwrite them. Here we only replace placeholders that were never set by a person.

---

## Step 6: One label per unit on purchase orders

**What we saw.** PO lines write the same unit in several ways: `KG` and `kg`; `Nos.`, `Nos`, `Unit` and `pcs` all mean pieces; `Mtr`, `Ltr` and `Set` are capitalised. 116 of 223 lines used a label the item master doesn't. Separately, 20 lines for 8 items use a genuinely *different* unit from the item master, for example an item counted in pieces but bought by weight (kg), length (mtr) or volume (ltr).

**Why it matters.** Totals and comparisons need to know which quantities share a unit. With `KG` and `kg` treated as different, a per-unit total splits in two. With `Nos.` and `pcs` treated as different, pieces never add up correctly.

**Options considered.**
- *Leave the labels; have the agent normalise them in every query.* Rejected. Every query would need the same mapping, and one miss silently splits a total.
- *Convert everything to one unit per item.* Rejected. Turning kg into pieces needs a weight per piece, which the data doesn't have; inventing conversion factors would fabricate quantities.
- **Standardise the labels (chosen):** `KG` → `kg`; `Nos.`, `Nos`, `Unit` → `pcs`; `Mtr` → `mtr`; `Ltr` → `ltr`; `Set` → `set`. Only the text changes; no quantity is converted.

**The genuinely different units stay as recorded.** PO quantities are always shown with the PO line's own unit, quantities are only compared or added within one unit (enforced by a deterministic check on every query), and comparisons across units are offered by ₹ value instead.

**Real-world check.** Yes. Standard unit codes are routine master-data hygiene, and it's what an ERP's unit-of-measure table enforces. Converting between unrelated units without conversion factors is exactly what we wouldn't do.

---

## What we deliberately did not clean

| Issue | Why we left it |
|---|---|
| 16 real POs with no expected delivery date; 10 suppliers with no payment terms; 41 items with no sub-category | Real gaps. The assistant must handle them with the missing-data policy and show caveats. |
| 8 items bought in a different unit from the item master (e.g. counted in pcs, bought in kg) | Converting needs a weight per piece, which we don't have. Quantities keep the PO's unit and are never added or ranked across units. |
| 5 over-received PO lines | Real events (suppliers delivered more), already recorded as regularisations. |
| Near-duplicate item ("Electromagnet Solenoid…" with a trailing space) and a garbled name (`1250d72500d71.5mm`) | Merging items would rewrite history across tables. Item code is the identifier. |
| 42 goods received without a PO, none matched to a PO yet | That's the real state of the business: a backlog worth showing. |
| Tax = 0 on every PO | Real. Answers say spend is before GST. |

## Before and after

| | Raw snapshot | After cleaning | After seeding |
|---|---|---|---|
| Stock movements | 1,028 | 1,001 | 1,432 (431 synthetic) |
| Purchase orders | 58 | 50 | 119 (69 synthetic) |
| Months of PO history | 1 (Aug) | 1 | 7 (Feb–Aug) |
| Suppliers | 35 | 34 | 34 |
| Stock rows | 562 | 560 | 560 |
| Stock = ledger everywhere | yes, only because of the 14 Oct fix | yes | yes |
| Item-warehouse histories that go negative | 192 (without the 14 Oct fix) | 0 | 0 |
| Rows dated after 21 Aug | 340+ | 0 | 0 |
| Stock rows below reorder level | 259 of 562 (template levels) | – | 153 (145 at zero stock) |
| Stock rows with no reorder level | 0 | – | 310 (no consumption) |
