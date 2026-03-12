---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item or package on Amazon
summary: Navigate a retailer's order history, locate a purchased item, and complete a return or refund flow; currently specialized for Amazon.
tags: [ecommerce, return, refund, amazon]
trigger-keywords: [return, send back, refund, amazon]
parameters:
  item:
    type: string
    required: true
    description: What to return
    examples: ["blue headphones", "laptop stand"]
requires:
  os: darwin
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
---

## Steps
1. Use open_url to navigate to https://www.amazon.com/gp/your-account/order-history
   - verify: Amazon orders page or login page visible
2. If login page is visible, wait for user to sign in
   - verify: Orders page loaded with search functionality
3. Ensure the orders page is oriented near the top or otherwise positioned so the order search/filter controls are visible
   - verify: The search/filter area for orders is visible

### Date Filter (if needed)
4. If the order is older than 3 months, click the date filter dropdown (shows "past 3 months" by default).
   Amazon's ONLY date filter options are: "last 30 days", "past 3 months", and specific calendar years (2026, 2025, 2024, ...). There is NO "past 6 months", "past year", or custom date range option. For orders older than 3 months, select the calendar year that contains the order date. For example, an order from "six months ago" relative to March 2026 would be in year 2025.
   - verify: Dropdown closes and filtered orders page loads

### Item Search
5. If {{item}} is a specific product name: Find the "Search all orders" search/filter bar and click it, type "{{item}}" and press Enter
   - verify: Search results visible
6. If {{item}} is vague, temporal, or not a specific product name (e.g., "something I bought last week", "my recent order", "that thing"): Do NOT search for the vague phrase. Instead, use the date filter to narrow to the relevant time period, then scroll through visible orders. If multiple candidate orders are visible and it is ambiguous which one the user means, use wait_for_user to ask the user to clarify which order they want to return.
   - verify: Either a single matching order identified, or user clarification received

### Return Flow
7. Scroll through the search results to find the most recent order containing "{{item}}". Click into the order card or its details view, then locate and click the return-related control (e.g., "Return or Replace Items", "View return options")
   - verify: Return options page visible
8. Select the return reason and continue through the return flow
   - verify: Return method or next return step is visible
9. Complete the remaining return steps until confirmation or drop-off instructions are visible
   - verify: Return confirmation visible

## Error Recovery
- If login page appears at step 1: wait for user to sign in, then continue
- If the orders page opens away from the search controls: scroll up or reposition first, then search again
- If search results require scrolling: use scroll_down to reveal more orders before giving up
- If "Return or Replace Items" is not visible on the matching order card: look for a semantically adjacent affordance on that same order card or order details view, such as "View item", "Order details", or another visible route toward returns
- If the page changes but not into the return flow: observe the new page and continue from the visible order-specific controls instead of assuming the return step is complete
- If return button is below the fold on the order details page: scroll down to reveal return-related controls
- If item not eligible for return (return window closed, item type excluded, or eligibility check fails): emit a done step with abort_reason explaining why the item cannot be returned
- **Non-returnable order detection signals**: If the order details page shows "Manage your subscription" instead of return controls, this is a subscription item that cannot be returned. If "Download" or "Read now" controls are visible, this is digital content that cannot be returned. If no return-related controls are visible after the full order details page has loaded, the item may not be eligible for return. In ALL these cases: stop immediately, do NOT continue scrolling or retrying for return controls. Instead, emit a done step with abort_reason explaining the specific reason the item cannot be returned through the standard flow.
- If the date filter dropdown does not contain the expected year: the year may not be available if the account is newer. Select the oldest available year instead.

## Notes
- Treat the named controls in this skill as likely affordances, not guaranteed literal text
- Prefer staying within the same order card or the details page for that order when choosing alternate actions
- Amazon order search only searches by product name/keyword, NOT by date or temporal phrases
