---
name: google-search
description: Search Google for a query
trigger-keywords: [google, search, look up, find]
parameters:
  query:
    type: string
    required: true
    description: Search query
    examples: ["weather today", "best restaurants nearby"]
requires:
  apps: [Safari]
  os: darwin
success-condition: Google search results page visible
max-retries: 2
---

## Steps
1. Open Safari
   - verify: Safari is frontmost app
2. Click the address bar
   - verify: Address bar is focused
3. Type "https://www.google.com/search?q={{query}}" and press Enter
   - verify: Google search results page visible for "{{query}}"

## Error Recovery
- If Safari doesn't open: try activating it again
- If address bar not focused: press Cmd+L
