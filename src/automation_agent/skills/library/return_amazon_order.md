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
   - verify: The "Search all orders" input field contains "{{item}}"
3. press_key ["return"] to submit the "Search all orders" query
   - verify: Amazon order-history results visible for "{{item}}" while remaining on amazon.com order history
4. Click the small text link that says exactly "View order details" on the first order card (it appears ABOVE the product title text, near the order date — do NOT click the product image, product title, or any button)
   - verify: Order details page visible with order information and item details
5. Click "Return or replace items" button
   - verify: Return reason selection page visible

## Error Recovery
- If "Search all orders" input is not visible: scroll down past the main navigation
- If return button is absent: look for "View order details" first, then find return option
- If login page appears: use wait_for_user to ask user to log in
- If pressing Return opens Amazon Pay or a general Amazon product search page: reopen https://www.amazon.com/gp/css/order-history and retry from the smaller "Search all orders" input
- If clicking the matching order opens a product page: go back and click "View order details" or the order ID link instead of the product title/image
- IMPORTANT: Do NOT type into the main Amazon search bar at the top of the page. The "Search all orders" input is a separate, smaller text field within the orders section.

## Notes
- The "Search all orders" input is BELOW the main Amazon navigation bar, within the orders content area
- It is NOT the same as the main Amazon search bar at the very top of the page
- After searching, click the exact text "View order details" (small gray/blue link near the order date); NEVER click the product image or product title — those navigate to the product page, not the order details page
- The "View order details" link appears at the top of each order card, before the product information
- Treat labels as likely affordances, not guarantees
