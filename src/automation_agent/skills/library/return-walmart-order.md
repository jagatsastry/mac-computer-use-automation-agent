---
trusted: false
name: return-walmart-order
skill-id: return-walmart-order
description: Return item on Walmart
summary: Walmart return
tags: [ecommerce]
trigger-keywords: [return, walmart]
parameters: {}
requires:
  apps: [Safari]
  os: darwin
success-condition: Return confirmed
max-retries: 3
parent-skill-id: return-amazon-order
---

## Steps
1. Go to orders
   - verify: Orders visible

## Error Recovery
- If absent: refresh

## Notes
- Walmart notes
