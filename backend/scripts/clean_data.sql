-- Clean the raw snapshot of the company DB. LOCAL DB ONLY.
-- Run by backend/scripts/prepare_local_db.sh right after restoring the dump.
-- The reasoning behind every step is in docs/data_preparation.md.
--
-- Invariant kept throughout: for every (item, location),
--   inv_current_stock.quantity = sum(inv_transactions.quantity).

\set ON_ERROR_STOP on
-- :as_of is passed in by prepare_local_db.sh from DATA_AS_OF (end of that day).
\set go_live '2026-01-31 00:00:00'

BEGIN;

-- The three users who created the original dataset (Apr–Aug 2026).
CREATE TEMP TABLE original_author (id text PRIMARY KEY) ON COMMIT DROP;
INSERT INTO original_author VALUES
  ('f6570cdb-2153-4b92-aedb-77514c880d7d'),  -- stores: daily movements and receipts
  ('a12e74bb-c602-49d3-803e-1ff8e1cdef96'),  -- 29 Jul physical count
  ('3f6b5856-336b-4643-8f6a-241cc69351d0');  -- procurement: suppliers and POs

-- ---------------------------------------------------------------------------
-- Step 1. Remove test writes made by other candidates on the shared source DB.
-- ---------------------------------------------------------------------------

-- 1a. Test purchase orders. Lines, receipts and regularisations cascade.
DELETE FROM proc_po_payment_tranches t
USING proc_purchase_orders po
WHERE po.id = t.po_id AND po.created_by NOT IN (SELECT id FROM original_author);

DELETE FROM proc_purchase_orders
WHERE created_by NOT IN (SELECT id FROM original_author);

-- 1b. Test supplier ("ak elec", no city, no POs).
DELETE FROM proc_vendors v
WHERE v.created_by NOT IN (SELECT id FROM original_author)
  AND NOT EXISTS (SELECT 1 FROM proc_purchase_orders po WHERE po.vendor_id = v.id);

-- 1c. Test stock movements. The 'reconciliation' rows are kept (Step 3).
CREATE TEMP TABLE junk_tx ON COMMIT DROP AS
SELECT id, item_id, location_id, quantity
FROM inv_transactions
WHERE created_by NOT IN (SELECT id FROM original_author)
  AND reference_type IS DISTINCT FROM 'reconciliation';

-- The test writes also moved stock when they were made, so take their net effect back out.
UPDATE inv_current_stock s
SET quantity = s.quantity - j.net
FROM (SELECT item_id, location_id, sum(quantity) AS net FROM junk_tx GROUP BY 1, 2) j
WHERE s.item_id = j.item_id AND s.location_id = j.location_id;

DELETE FROM inv_transactions WHERE id IN (SELECT id FROM junk_tx);

-- Stock rows that only existed because a test wrote to them.
DELETE FROM inv_current_stock s
WHERE s.quantity = 0
  AND EXISTS (SELECT 1 FROM junk_tx j WHERE j.item_id = s.item_id AND j.location_id = s.location_id)
  AND NOT EXISTS (SELECT 1 FROM inv_transactions t WHERE t.item_id = s.item_id AND t.location_id = s.location_id);

-- ---------------------------------------------------------------------------
-- Step 2. The 29 Jul physical count was recorded as receipts and issues.
-- Re-type it as a stock-count adjustment; quantities and date are unchanged.
-- ---------------------------------------------------------------------------

UPDATE inv_transactions
SET transaction_type = 'adjustment', reference_type = 'stock_count'
WHERE notes = 'Physical count reconciliation 29th July 2026';

-- ---------------------------------------------------------------------------
-- Step 3. The reconciliation rows are the opening balances the ledger was missing.
-- Date them at the start of the ledger instead of the day the fix was run.
-- ---------------------------------------------------------------------------

UPDATE inv_transactions
SET reference_type = 'opening_balance', created_at = :'go_live'
WHERE reference_type = 'reconciliation' AND quantity > 0;

-- The single negative row cannot be an opening balance: keep it as a count correction on the as-of date.
UPDATE inv_transactions
SET reference_type = 'stock_count', created_at = :'as_of'
WHERE reference_type = 'reconciliation' AND quantity < 0;

-- Same rule for any gap still left (one real issue of stock never recorded as received;
-- a test write had masked it): add the missing opening balance, marked as data preparation.
INSERT INTO inv_transactions
  (id, item_id, location_id, transaction_type, quantity, reference_type, notes,
   created_by, created_at, currency, is_synthetic)
SELECT gen_random_uuid()::text, l.item_id, l.location_id, 'adjustment', coalesce(s.quantity, 0) - l.qty,
       'opening_balance', 'Opening balance added in data preparation to reconcile the ledger with on-hand stock',
       '00000000-0000-4000-8000-000000000001', :'go_live', 'INR', true
FROM (SELECT item_id, location_id, sum(quantity) AS qty FROM inv_transactions GROUP BY 1, 2) l
LEFT JOIN inv_current_stock s USING (item_id, location_id)
WHERE abs(coalesce(s.quantity, 0) - l.qty) > 0.001;

-- ---------------------------------------------------------------------------
-- Step 4. Nothing may be dated after the as-of date (data ends 21 Aug 2026).
-- ---------------------------------------------------------------------------

UPDATE inv_current_stock s
SET last_transaction_at = (SELECT max(t.created_at) FROM inv_transactions t
                           WHERE t.item_id = s.item_id AND t.location_id = s.location_id)
WHERE s.last_transaction_at > :'as_of';

UPDATE inv_current_stock SET updated_at = :'as_of' WHERE updated_at > :'as_of';

-- ---------------------------------------------------------------------------
-- Step 5. One label per unit on PO lines (docs: step 6) ('KG' -> 'kg', 'Nos.' -> 'pcs', ...), matching
-- the item master's vocabulary. Labels only: no quantity is converted.
-- ---------------------------------------------------------------------------

UPDATE proc_po_lines
SET unit = CASE lower(trim(unit))
             WHEN 'nos.' THEN 'pcs'
             WHEN 'nos' THEN 'pcs'
             WHEN 'no.' THEN 'pcs'
             WHEN 'unit' THEN 'pcs'
             WHEN 'units' THEN 'pcs'
             ELSE lower(trim(unit))
           END
WHERE unit IS NOT NULL;

COMMIT;
