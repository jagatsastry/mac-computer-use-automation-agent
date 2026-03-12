---
name: google-search
skill-id: google-search
description: Search Google for a query
summary: Open a web browser, navigate to Google, and execute a search query to display results.
tags: [search, web, google, browser]
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
- If Safari doesn't open: try activating it again using activate_app
- If address bar not focused: press Cmd+L to focus it
- If Google shows a consent banner or locale redirect (e.g., google.co.uk): dismiss the banner or navigate directly to google.com
- If search returns no results or "Did you mean": try the suggested spelling or rephrase the query
