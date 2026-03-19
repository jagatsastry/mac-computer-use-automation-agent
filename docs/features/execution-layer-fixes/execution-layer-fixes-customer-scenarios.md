# Execution-Layer Fixes Customer Test Scenarios

These scenarios test the 3 execution-layer fixes:
1. type_text verify honors step.verify (not just field contents)
2. Precondition failure → immediate replan (not retry_different)
3. Pre-click validation for low-confidence vision results

---

### Scenario 1: Listerine return on Amazon (the original failing case)
- **Category:** happy_path
- **Prompt:** "Search for Listerine in my Amazon orders and return it"
- **Expected outcome:** Amazon orders page filtered to show Listerine orders, with return flow initiated or order details visible
- **Success criteria:**
  1. Skill `return-amazon-order` is matched and used
  2. Agent navigates directly to order history URL (not clicking "Returns & Orders")
  3. type_text step for "Listerine" does NOT pass verification if orders are not filtered (the fix: verify must honor step.verify)
  4. If precondition fails on a step, agent replans immediately (not retry with mutated element)
  5. No clicks land in whitespace (low-confidence grounding gets pre-click validation)
- **Notes:** This is the exact scenario that exposed all 3 bugs. With fixes, the agent should either successfully filter orders or cleanly replan when verification detects the search didn't filter.

### Scenario 2: Wikipedia search (regression check)
- **Category:** happy_path
- **Prompt:** "Go to wikipedia.org and search for Albert Einstein"
- **Expected outcome:** Wikipedia article or search results for Albert Einstein visible
- **Success criteria:**
  1. type_text verification passes correctly (text IS about field contents → shortcut OK)
  2. No false precondition failures
  3. Task completes successfully
- **Notes:** Regression check — the type_text fix should NOT break normal type_text verification where step.verify IS about field contents.

### Scenario 3: Amazon product search (regression check)
- **Category:** happy_path
- **Prompt:** "Go to amazon.com and search for wireless headphones"
- **Expected outcome:** Amazon search results for wireless headphones visible
- **Success criteria:**
  1. type_text into search bar passes verification correctly
  2. Search results page is displayed
  3. Task completes successfully
- **Notes:** Ensures the type_text shortcut still works for normal search scenarios.
