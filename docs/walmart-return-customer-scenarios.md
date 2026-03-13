# Walmart Return — Customer Test Scenarios

### Scenario 1: Return a specific recent purchase
- **Category:** happy_path
- **Prompt:** "Return the Crest 3D Whitestrips I bought on Walmart"
- **Expected outcome:** Agent navigates to Walmart orders, finds the Crest 3D Whitestrips order, opens it, initiates the return flow, selects a return reason, and reaches the return confirmation or shipping label page.
- **Success criteria:**
  1. Walmart orders page loads in Safari (URL contains walmart.com/orders)
  2. The correct order containing "Crest 3D Whitestrips" is opened — not a different item
  3. The return reason selection screen appears and a reason is chosen
- **Notes:** User is already logged into Walmart in Safari. Item was purchased within the return window. This is the straightforward path the skill was designed for.

### Scenario 2: Vague description matching multiple possible orders
- **Category:** edge_case
- **Prompt:** "Return the shoes I got from Walmart last month"
- **Expected outcome:** Agent navigates to orders, scans for shoe-related items, and either picks the most recent shoe order or asks the user to clarify which pair if multiple shoe orders exist.
- **Success criteria:**
  1. Agent reaches the Walmart orders page and visually scans for shoe items
  2. If multiple shoe orders exist, the agent does not blindly return the wrong one — it either picks the most recent or pauses for clarification
  3. Agent does not start a return on a non-shoe item (e.g., socks, shoe cleaner)
- **Notes:** "Shoes" is vague — the user might have ordered Nike running shoes and Crocs in the same month. The skill template takes an `item` parameter but the user's description is fuzzy. Tests whether the agent can visually match items when the name is imprecise.

### Scenario 3: Item not eligible for return
- **Category:** error_recovery
- **Prompt:** "Return the bag of dog food I ordered on Walmart"
- **Expected outcome:** Agent navigates to orders, finds the dog food order, opens it, and discovers there is no "Start a return" button because the item is past its return window or is in a non-returnable category. Agent reports clearly to the user that the item is not eligible for return.
- **Success criteria:**
  1. Agent navigates to Walmart orders and locates the dog food order
  2. Agent does not get stuck in a retry loop clicking non-existent return buttons — infeasibility detection fires within 3 attempts
  3. Agent surfaces a clear message to the user explaining the item cannot be returned (e.g., "return window closed" or "not eligible")
- **Notes:** Consumable/grocery items and items past the return window often lack return buttons entirely. The skill's error recovery section says to "report that the item is no longer eligible for return." This tests whether the agent gracefully stops instead of endlessly searching for a return link.
