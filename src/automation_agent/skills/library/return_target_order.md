---
name: return-target-order
skill-id: return-target-order
description: Return an item on Target.com
summary: Automates the Target return flow
tags: [ecommerce, return]
trigger-keywords: [return, target, refund]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  apps: [Safari]
  os: darwin
success-condition: Return confirmation or shipping label page is visible
max-retries: 3
---

## Steps
1. open_url: https://www.target.com/orders
   - verify: Target orders page is visible
   - on_fail: If a login page appears, use wait_for_user to ask the user to log in, then continue
2. observe: Look at the orders page to understand what is visible
3. click: Find and click the order that matches "{{item}}" — look for the item name, "View order details", or the order card
   - verify: Order details page is visible
   - on_fail: If the item is not visible, scroll down to find it
4. click: "Return an item" or "Start a return" button
   - verify: Return item selection or return reason page is visible
   - on_fail: Look for alternative links like "Return" or "Return or exchange"
5. Select the item to return if prompted, choose a return reason, and click Continue
   - verify: Return method selection or confirmation page is visible

## Error Recovery
- If login page appears: wait for user to log in, then continue
- If no orders match: report to user that item was not found in order history
- If return option not available: report that the item may not be eligible for return
- If "Sign in" prompt appears mid-flow: wait for user to complete sign-in

## Notes
- Target orders page requires login
- Target offers both in-store and mail returns
- Use direct orders URL to skip navigation
