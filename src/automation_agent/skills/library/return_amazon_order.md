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
3. Press Command+Up
   - verify: The top of the Amazon orders page is visible
4. Find the "Search all orders" search/filter bar and click it
   - verify: Search bar is focused
5. Type "{{item}}" and press Enter
   - verify: Search results visible
6. Find the most recent order containing "{{item}}" and click "Return or Replace Items"
   - verify: Return options page visible
7. Select return reason and click Continue
   - verify: Return method selection visible
8. Complete return process
   - verify: Return confirmation visible

## Error Recovery
- If the orders page opens at the bottom: press Command+Up, then search again
- If "Return" button not found: scroll down, try again
- If item not eligible: abort with message to user
