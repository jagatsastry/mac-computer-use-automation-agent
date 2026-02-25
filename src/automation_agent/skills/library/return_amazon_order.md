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
  apps: [Safari]
  os: darwin
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
---

## Steps
1. Open Safari and navigate to https://www.amazon.com/gp/your-account/order-history
   - verify: Amazon orders page or login page visible
2. If login page appears, wait for user to sign in
   - verify: Orders page loaded with search functionality
3. Find the search/filter bar and click it
   - verify: Search bar is focused
4. Type "{{item}}" and press Enter
   - verify: Search results visible
5. Find the order containing "{{item}}" and click "Return or Replace Items"
   - verify: Return options page visible
6. Select return reason and click Continue
   - verify: Return method selection visible
7. Complete return process
   - verify: Return confirmation visible

## Error Recovery
- If "Return" button not found: scroll down, try again
- If item not eligible: abort with message to user
