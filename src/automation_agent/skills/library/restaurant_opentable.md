---
name: restaurant-opentable
skill-id: restaurant-opentable
description: Make a restaurant reservation on OpenTable by navigating the OpenTable website, selecting party size, date, time, and completing the reservation form.
summary: Navigate a restaurant reservation platform, select party size, date, and time, then complete the booking flow; currently specialized for OpenTable.
tags: [restaurant, reservation, booking, opentable, dining]
trigger-keywords:
  - book opentable
  - reserve opentable
  - opentable reservation
  - opentable booking
  - make opentable reservation
parameters:
  restaurant_name:
    type: string
    required: true
    description: Name of the restaurant to book on OpenTable
    examples:
      - "The French Laundry"
      - "Nobu"
      - "Chez Panisse"
  party_size:
    type: string
    required: true
    description: Number of guests for the reservation
    examples:
      - "2"
      - "4"
      - "6"
  date:
    type: string
    required: true
    description: Date of the reservation
    examples:
      - "2026-03-15"
      - "March 15"
      - "this Saturday"
  time:
    type: string
    required: true
    description: Preferred reservation time
    examples:
      - "7:00 PM"
      - "7pm"
      - "19:00"
requires:
  apps:
    - Safari
  os: darwin
success-condition: Reservation confirmation page is visible with a booking reference number or "Reservation confirmed" message showing restaurant name, date, time, and party size.
max-retries: 3
---

## Steps

1. Open Safari and navigate to https://www.opentable.com
   - verify: Safari is open and the page title or URL contains "opentable.com"

2. Click on the search bar at the top of the OpenTable homepage and type "{{restaurant_name}}"
   - verify: Search results or autocomplete suggestions are visible showing restaurant names

3. Click on "{{restaurant_name}}" in the search results list
   - verify: The restaurant's OpenTable reservation page is open showing the reservation widget with "Party size", "Date", and "Time" fields

4. Click on the "Party size" dropdown in the reservation widget and select the option showing "{{party_size}} people"
   - verify: The "Party size" dropdown displays "{{party_size}} people" or "{{party_size}}"

5. Click on the "Date" field and select {{date}} from the calendar picker
   - verify: The "Date" field displays {{date}}

6. Click on the "Time" dropdown and select the time closest to {{time}}
   - verify: The "Time" dropdown displays a time value close to {{time}}

7. Click the "Find a Time" button
   - verify: Available time slot chips appear below the reservation form (e.g., "6:45 PM", "7:00 PM", "7:15 PM" buttons are visible)

8. Click the time slot button that shows the time closest to {{time}} (e.g., the "7:00 PM" chip)
   - verify: A reservation details panel or confirmation modal has opened showing the selected time, date, and party size

9. Fill in any required reservation details fields: "First name", "Last name", "Email address", "Phone number"
   - verify: All visible required fields on the form are filled in

10. Click the "Complete reservation" button to submit the booking
    - verify: A reservation confirmation page is shown with a booking reference number or the text "Reservation confirmed" along with the restaurant name, date, and party size

## Error Recovery

- If step 3 fails (restaurant not found): Try searching with a shorter version of the restaurant name or check spelling, then repeat from step 2
- If step 7 fails (no time slots shown): Try a different date or time and click "Find a Time" again
- If step 10 fails (login required): The reservation requires an OpenTable account. If the user has an account, sign in first. If not, the flow cannot be completed without registration.
- If the page is unresponsive: Reload opentable.com and start from step 1

## Notes

- OpenTable requires an account login to complete reservations. If a login gate appears after step 8, the user must sign in or create an account before continuing.
- Party size options are displayed as "1 person", "2 people", "3 people", etc.
- Available time slots depend on actual restaurant availability. If the exact {{time}} is not available, choose the nearest available slot.
- The reservation widget may be embedded on the restaurant page or may open in a modal — both cases are handled by following the same element labels.
