# Sample Reviewer Queries

These assume both sample documents have been uploaded for customer `acme_corp`.
Exact counts depend on how many times you have run the samples locally; use a fresh `nova.db`
if you want the expected counts below to match exactly.

1. **How many shipments were auto-approved?**  
   Expected on a fresh DB after uploading both samples once: `1`, from the clean document.

2. **How many shipments need human review?**  
   Expected on a fresh DB: `0` or `1`, depending on whether the messy document routes to
   `HUMAN_REVIEW` for uncertainty or `AMEND` for critical mismatches.

3. **How many shipments were flagged for amendment this week?**  
   Expected on a fresh DB: usually `1`, from the messy document's critical mismatch/missing-field path.

4. **What's the top reason ACME documents get rejected?**  
   Expected: one of `consignee_name`, `hs_code`, `gross_weight`, or `port_of_loading`,
   depending on the messy extraction and validation result.

5. **Show me recent runs for ACME.**  
   Expected: two runs, one for `acme_shipment_clean.pdf` and one for `acme_shipment_messy.pdf`, with their final decisions and stage histories.

Notes:

- "Flagged" maps best to amendment-style routing (`AMEND`) in the current planner.
- Ask "needs human review" when you specifically want `HUMAN_REVIEW`.
- The evidence panel should show the safe query tool used, its filters, and the grounded result.
