---
name: restaurant-yelp
description: Make a restaurant reservation on Yelp by finding the restaurant page, clicking "Make a Reservation", selecting party size, date, time, and completing the booking flow.
trigger-keywords:
  - book yelp restaurant
  - reserve yelp
  - yelp reservation
  - yelp restaurant booking
  - find table yelp
  - make reservation yelp
parameters:
  restaurant_name:
    type: string
    required: true
    description: Name of the restaurant to book on Yelp
    examples:
      - "The French Laundry"
      - "Nobu"
      - "Delfina"
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
success-condition: Reservation confirmation is visible with booking details including restaurant name, date, time, party size, and a confirmation number or "Reservation confirmed" message.
max-retries: 3
---

## Steps

1. Open Safari and navigate to https://www.yelp.com
   - verify: Safari is open and the page title or URL contains "yelp.com"

2. Type "{{restaurant_name}}" in the Yelp search bar (the "Find" or business name field) and press Enter
   - verify: Search results page is visible showing restaurant listings

3. Click on the restaurant listing for "{{restaurant_name}}" in the search results
   - verify: The restaurant's Yelp business page is open showing the restaurant name, address, and action buttons

4. Click the "Make a Reservation" tab or button on the restaurant page
   - verify: The reservation widget is visible with "Party Size", "Date", and "Time" fields

5. Click the "Party Size" dropdown in the reservation widget and select "{{party_size}} people"
   - verify: The "Party Size" dropdown shows "{{party_size}} people" or "{{party_size}}"

6. Click the "Date" dropdown or date picker and select {{date}}
   - verify: The "Date" field shows {{date}}

7. Click the "Time" dropdown and select the time closest to {{time}}
   - verify: The "Time" dropdown shows a time value close to {{time}}

8. Click the "Find a Table" button
   - verify: Available time slot buttons appear showing specific times (e.g., "6:45 PM", "7:00 PM", "7:15 PM")

9. Click the time slot button closest to {{time}}
   - verify: A booking confirmation form or embedded reservation widget (Resy, OpenTable, or similar) is shown with fields for name, email, and contact details

10. Complete any required fields (first name, last name, email, phone) and click "Book" or "Continue" to confirm
    - verify: Reservation confirmation page is displayed with a booking reference number or "Reservation confirmed" message

## Error Recovery

- If step 4 fails ("Make a Reservation" not visible): Scroll down the restaurant page — the reservation widget may be lower on the page or appear as a sidebar widget
- If step 8 fails (no time slots appear): Try a different date or time combination and click "Find a Table" again
- If the booking redirects to Resy or another platform: Follow the Resy/OpenTable embedded widget prompts using the same party size, date, and time values
- If the restaurant does not accept online reservations via Yelp: The page will show "Call to make a reservation" — this skill cannot complete the flow in that case

## Notes

- Many Yelp restaurant reservations are powered by an embedded Resy or OpenTable widget. The visible time slot chips and booking button labels may come from the embedded provider.
- Party size options on Yelp are displayed as "1 person", "2 people", "3 people", etc.
- Available time slots depend on actual restaurant availability. If {{time}} is not available, choose the nearest available slot.
- A Yelp account or the embedded reservation platform account may be required to complete the booking.
