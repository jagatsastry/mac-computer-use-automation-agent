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
1. Open Safari
   - verify: Safari is frontmost app
2. Click the address bar or press Cmd+L
   - verify: Address bar is focused
3. Type "https://www.amazon.com" and press Enter
   - verify: Amazon homepage or search bar visible
4. Find the Amazon search bar and click it
   - verify: Amazon search bar is focused
5. Type "{{product}}" and press Enter
   - verify: Amazon search results page visible showing {{product}} listings
6. Find the "Sort by" dropdown and click it
   - verify: Sort options visible
7. Select "Price: Low to High"
   - verify: Results re-sorted with lowest price items first

## Error Recovery
- If login page appears: wait for user to sign in
- If CAPTCHA appears: wait for user to solve it
- If search bar not found: press Cmd+L, type amazon.com, try again
- If sort dropdown not found: scroll up to find it
