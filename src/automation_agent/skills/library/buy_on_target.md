---
name: buy-on-target
skill-id: buy-on-target
description: Search for a product on Target.com and add it to cart
summary: Navigate to Target, search for a product, apply filters, select a product, and add to cart.
tags: [ecommerce, target, shopping, buy, cart]
trigger-keywords: [target, buy, purchase, shop, add to cart, target.com]
site: target
required-keywords: [target, target.com]
parameters:
  product:
    type: string
    required: true
    description: Product to search for
    examples: ["bed sheets", "queen-size bed sheet set", "throw pillow"]
  max_price:
    type: string
    required: false
    description: Maximum price filter (e.g., "$50", "50")
    examples: ["$50", "25"]
requires:
  apps: [Safari, Google Chrome]
  os: darwin
success-condition: Product has been added to cart and cart confirmation is visible
max-retries: 3
---

## Steps
1. Navigate to https://www.target.com/s?searchTerm={{product}}
   - verify: Target search results page is visible with product listings for {{product}}
   - on_fail: If login page appears, wait_for_user to log in
2. Click on the "sort by" dropdown
   - verify: Sort options are visible
   - on_fail: If sort/filter not found, scroll up to find sorting controls
3. Click on a product listing
   - verify: Product detail page is loaded with "Add to cart" button
   - on_fail: If no matching product visible, scroll down to find more options
4. Click on the "Add to cart" button
   - verify: Cart confirmation appears or cart icon badge updates
   - on_fail: If "Add to cart" not visible, scroll down to find it
5. Use done to confirm product added to cart. Checkout requires user confirmation.
   - verify: Product has been added to cart successfully

## Error Recovery
- If login page appears: wait for user to sign in, then continue
- If CAPTCHA appears: wait for user to solve it, then continue
- If search returns no results: try a broader search term
- If product is out of stock: look for "Notify me" or select a different product
- If price is above max_price: scroll to find cheaper options or sort by price
- If "Add to cart" button is disabled: check if size/color selection is required first

## Notes
- Target uses "Add to cart" button (not "Buy now")
- Target search URL format: target.com/s?searchTerm=<query>
- Target sort by price: may need to click "Price: low to high" in sort dropdown
- Size/color selection may be required before "Add to cart" is enabled
- This skill stops at add-to-cart; checkout is out of scope
