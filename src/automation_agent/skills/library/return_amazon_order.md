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
  apps: [Safari]
  os: darwin
success-condition: Return confirmation visible
max-retries: 3
---

## Steps
1. Open orders page
   - verify: Orders page visible
2. Search for "{{item}}"
   - verify: Matching order visible

## Error Recovery
- If return button is absent: look for order details first

## Notes
- Treat labels as likely affordances, not guarantees
