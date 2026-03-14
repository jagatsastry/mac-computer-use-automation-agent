---
name: return-walmart-order
skill-id: return-walmart-order
description: Return an item on Walmart.com by navigating to order history and initiating the return flow
summary: Navigate to Walmart order history via direct URL, find the item, open order details, and start a return.
tags: [ecommerce, return, walmart, refund, order]
trigger-keywords: [return, walmart, refund, send back, money back]
site: walmart
required-keywords: [walmart, walmart.com]
parameters:
  item:
    type: string
    required: true
    description: Item to return (product name or description)
    examples: ["Crest 3D Whitestrips", "dog food", "shoes"]
requires:
  apps: [Safari, Google Chrome]
  os: darwin
success-condition: Return confirmation or label page is visible
max-retries: 3
---

## Steps
1. open_url: https://www.walmart.com/orders
   - verify: Walmart orders page is visible
   - on_fail: If a login page appears, use wait_for_user to ask the user to log in, then continue
2. observe: Look at the orders page to understand what is visible
3. click: Find and click the order that matches "{{item}}" — look for the item name or image
   - verify: Order details page is visible
   - on_fail: If the item is not visible in the current order list, scroll down to find it
4. click: "Start a return" or "Return items" button
   - verify: Return reason selection page is visible
   - on_fail: Look for alternative return links like "Start return" or "Return or replace"
5. Select the item to return if prompted, then select a return reason and click Continue
   - verify: Return method or confirmation page is visible

## Error Recovery
- If login page appears: wait for user to log in, then continue
- If no orders match: report to user that item was not found in order history
- If "Return window closed" or not eligible: report that the item is no longer eligible for return
- If order details don't show return option: the item may not be eligible — check for eligibility text

## Notes
- Walmart orders page requires login
- The return flow may vary: some items go through a return center, others through mail
- Use direct orders URL to skip navigation
