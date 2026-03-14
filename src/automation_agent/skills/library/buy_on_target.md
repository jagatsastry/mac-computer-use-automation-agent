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
1. Navigate to https://www.target.com/s?searchTerm={{product}}&sortBy=PriceLow
   - verify: Target search results page is visible with product listings for {{product}} sorted by price
   - on_fail: wait_for_user
2. Scroll down past "Popular filters" to see full product listings
   - verify: Product listings with prices are fully visible
   - on_fail: scroll
3. Click on the title of the first {{product}} result
   - verify: Product detail page is loaded with product title and price visible
   - on_fail: replan
4. Scroll down to see the "Add to cart" button below the size and color selectors
   - verify: "Add to cart" button is visible on screen
   - on_fail: scroll
5. Click on the "Add to cart" button
   - verify: Cart confirmation appears or cart icon badge updates
   - on_fail: replan
6. Use done to confirm product added to cart. Checkout requires user confirmation.
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
- Target search URL format: target.com/s?searchTerm=<query>&sortBy=PriceLow
- Sorting is done via URL parameter, not by clicking the sort UI
- Size/color selection may be required before "Add to cart" is enabled
- Click on product TITLE TEXT (not the image) — title links are more reliably clickable
- Target search results show "Popular filters" above product listings — scroll down to see products
- Product detail pages show size/color selectors above "Add to cart" — scroll down to see the button
- This skill stops at add-to-cart; checkout is out of scope
