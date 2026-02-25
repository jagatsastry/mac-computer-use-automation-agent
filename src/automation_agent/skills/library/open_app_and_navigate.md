---
name: open-app-and-navigate
description: Open an application and navigate to a specific section
trigger-keywords: [open, launch, go to, navigate]
parameters:
  app_name:
    type: string
    required: true
    description: Application name
    examples: ["Safari", "Finder", "System Settings"]
  destination:
    type: string
    required: false
    description: Where to navigate within the app
    examples: ["Downloads", "Privacy settings"]
requires:
  apps: []
  os: darwin
success-condition: Application open at requested location
max-retries: 3
---

## Steps
1. Open {{app_name}}
   - verify: {{app_name}} is frontmost application
2. Navigate to {{destination}} (if specified)
   - verify: {{destination}} view/section is visible

## Error Recovery
- If app not found: check exact name, try Spotlight
- If navigation fails: try menu bar navigation
