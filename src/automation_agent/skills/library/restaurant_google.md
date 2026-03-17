---
name: restaurant-google
skill-id: restaurant-google
description: Make a restaurant reservation via Google Maps by finding the restaurant business page, clicking "Reserve a table", and completing the embedded reservation widget (Resy, OpenTable, or Tock).
summary: Navigate Google Maps, find a restaurant, and complete a reservation through the embedded booking widget (Resy, OpenTable, or Tock).
tags: [restaurant, reservation, booking, google-maps, dining]
trigger-keywords:
  - reserve google maps
  - google restaurant reservation
  - google maps reservation
  - book restaurant google
  - reserve via google
  - google maps book table
parameters:
  restaurant_name:
    type: string
    required: true
    description: Name of the restaurant to book via Google Maps
    examples:
      - "The French Laundry"
      - "Nobu"
      - "Zuni Cafe"
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
    - Google Chrome
  os: darwin
success-condition: Reservation confirmation is visible with booking details including restaurant name, date, time, party size, and a confirmation number or "Reservation confirmed" message from the embedded reservation provider.
max-retries: 3
---

## Steps

1. Open the browser and navigate to https://maps.google.com
   - verify: The browser is open and the URL contains "maps.google.com" and the Google Maps interface is loaded

2. Click the Google Maps search bar and type "{{restaurant_name}}" then press Enter
   - verify: A map result and sidebar panel shows the restaurant named "{{restaurant_name}}" with address and business details

3. Click on the restaurant listing in the search results sidebar or on the map pin for "{{restaurant_name}}"
   - verify: The restaurant details panel is open in the left sidebar showing the restaurant name, address, phone number, and action buttons

4. Click the "Reserve a table" button in the restaurant details panel
   - verify: A reservation widget has appeared (this may be labeled "Resy", "OpenTable", or "Tock") showing party size, date, and time selection controls

5. Select {{party_size}} guests using the "Party size" or "Guests" selector in the embedded reservation widget
   - verify: The party size selector in the widget shows {{party_size}} or "{{party_size}} guests"

6. Select {{date}} using the date picker or calendar control in the embedded reservation widget
   - verify: The date field in the widget shows {{date}}

7. Select {{time}} using the time dropdown or time selector in the embedded reservation widget
   - verify: The time selector in the widget shows a time value close to {{time}}

8. Click the "Find a time" or "Search" button within the embedded reservation widget
   - verify: A grid or list of available time slots is displayed in the widget (e.g., "7:00 PM", "7:15 PM", "7:30 PM" buttons)

9. Click the available time slot closest to {{time}} in the widget
   - verify: A booking details form or confirmation screen is shown within the widget, displaying the selected date, time, and party size

10. Complete any required fields (first name, last name, email address, phone number) and click "Reserve" or "Complete reservation"
    - verify: A reservation confirmation screen is visible showing a booking reference number or "Reservation confirmed" message along with the restaurant name and reservation details

## Error Recovery

- If Google shows a consent banner or login prompt: dismiss it or wait for user to sign in before searching
- If step 4 fails ("Reserve a table" button not visible): Scroll down the restaurant details panel — the button may be below the fold. Also look for equivalent labels like "Book a table", "Make a reservation", or a calendar icon. If none are visible after scrolling, this restaurant does not support online reservations via Google Maps.
- If step 8 fails (no available slots shown): The restaurant may be fully booked for this date/time. Try a different date or time and search again.
- If the embedded widget requires an account login: The specific reservation provider (Resy, OpenTable, or Tock) requires a user account. The user must sign in or create an account to continue.
- If the widget shows "Not available" for all times: Try adjusting the date using the date picker and searching again.
- If time slot list requires scrolling: scroll down within the widget to reveal more available slots

## Notes

- The reservation widget embedded in Google Maps varies by restaurant: it may be powered by Resy, OpenTable, Tock, or another provider. The UI controls and button labels may differ slightly across providers but the flow (select party size → date → time → find slots → book) is consistent.
- Available time slots depend on actual restaurant availability and the embedded provider's data.
- If {{time}} is not available, select the nearest available slot.
- Google Maps shows the "Reserve a table" button only for restaurants that have enabled online reservations through one of the supported booking partners.
