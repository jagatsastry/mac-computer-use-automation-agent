---
trusted: false
name: return-walmart-order-2
skill-id: return-walmart-order-2
description: Duplicate name
summary: Duplicate
tags: [test]
trigger-keywords: [return, walmart]
parameters: {}
requires:
  apps: [Safari]
  os: darwin
success-condition: Done
max-retries: 3
parent-skill-id: return-amazon-order
---

## Steps
1. Do thing
   - verify: Done

## Error Recovery
- If X: Y

## Notes
- Notes
