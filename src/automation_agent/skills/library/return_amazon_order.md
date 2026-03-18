---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item on Amazon
summary: Automates the Amazon return flow
tags: [ecommerce, return]
trigger-keywords: [return, amazon, refund]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  apps: [browser]
  os: darwin
success-condition: Return confirmation visible
max-retries: 3
---

## Steps
1. open_url: https://www.amazon.com/gp/css/order-history
   - verify: Amazon orders page visible with "Your Orders" heading
2. type_text "{{item}}" into the "Search all orders" input field (NOT the main Amazon search bar at the top — look for the smaller search box within the orders section, near "X orders placed in")
   - verify: Orders filtered to show items matching "{{item}}"
3. Click on the order that matches "{{item}}" to view order details
   - verify: Order details page visible
4. Click "Return or replace items" button
   - verify: Return reason selection page visible

## Error Recovery
- If "Search all orders" input is not visible: scroll down past the main navigation
- If return button is absent: look for "View order details" first, then find return option
- If login page appears: use wait_for_user to ask user to log in
- IMPORTANT: Do NOT type into the main Amazon search bar at the top of the page. The "Search all orders" input is a separate, smaller text field within the orders section.

## Notes
- The "Search all orders" input is BELOW the main Amazon navigation bar, within the orders content area
- It is NOT the same as the main Amazon search bar at the very top of the page
- Treat labels as likely affordances, not guarantees
