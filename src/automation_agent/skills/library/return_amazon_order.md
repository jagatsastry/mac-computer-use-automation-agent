---
name: return-amazon-order
description: Return an item or package on Amazon
trigger-keywords: [return, send back, refund, amazon]
parameters:
  item:
    type: string
    required: true
    description: What to return
    examples: ["blue headphones", "laptop stand"]
requires:
  os: darwin
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
---

## Steps
1. Use open_url to navigate to https://www.amazon.com/gp/your-account/order-history
   - verify: Amazon orders page or login page visible
2. If login page is visible, wait for user to sign in
   - verify: Orders page loaded with search functionality
3. Ensure the orders page is oriented near the top or otherwise positioned so the order search/filter controls are visible
   - verify: The search/filter area for orders is visible
4. Find the "Search all orders" search/filter bar and click it
   - verify: Search bar is focused
5. Type "{{item}}" and press Enter
   - verify: Search results visible
6. Find the most recent order containing "{{item}}" and navigate into the return flow from that same order card or its details view
   - verify: Return options page visible
7. Select the return reason and continue through the return flow
   - verify: Return method or next return step is visible
8. Complete the remaining return steps until confirmation or drop-off instructions are visible
   - verify: Return confirmation visible

## Error Recovery
- If the orders page opens away from the search controls: reposition first, then search again
- If "Return or Replace Items" is not visible on the matching order card: look for a semantically adjacent affordance on that same order card or order details view, such as "View item", "Order details", or another visible route toward returns
- If the page changes but not into the return flow: observe the new page and continue from the visible order-specific controls instead of assuming the return step is complete
- If item not eligible: abort with message to user

## Notes
- Treat the named controls in this skill as likely affordances, not guaranteed literal text
- Prefer staying within the same order card or the details page for that order when choosing alternate actions
