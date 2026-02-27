---
name: amazon-search
description: Search Amazon for a product and find the cheapest option
trigger-keywords: [amazon, buy, cheapest, shop, purchase, price, product]
parameters:
  product:
    type: string
    required: true
    description: Product to search for
    examples: ["shampoo", "wireless mouse", "USB-C cable"]
requires:
  apps: [Safari]
  os: darwin
success-condition: Amazon search results visible sorted by price low to high
max-retries: 3
---

## Steps
1. Use activate_app to open Safari
   - verify: Safari is frontmost app
2. Use open_url to navigate to https://www.amazon.com/s?k={{product}}&s=price-asc-rank
   - verify: Amazon search results page visible showing {{product}} listings sorted by price
3. Use done to confirm results are visible
   - verify: Amazon search results visible with price-sorted listings

## Error Recovery
- If login page appears: wait for user to sign in
- If CAPTCHA appears: wait for user to solve it
- If search bar not found: press Cmd+L, type amazon.com, try again
- If sort dropdown not found: scroll up to find it
