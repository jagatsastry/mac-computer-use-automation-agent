# Restaurant Vertical Skills Specification

## Purpose

Implement Recommendation 5 from VISION_AUTOMATION_RESEARCH.md: provider-specific skill files for restaurant reservation flows on OpenTable, Yelp, and Google Maps. Each skill maintains a target lexicon of known UI element labels to reduce search ambiguity and increase grounding precision.

## Background

From the research doc (Rec 5):
> For OpenTable/Yelp/Google flows, maintain provider-specific target lexicon:
> - party size selector
> - date picker
> - time slot
> - reserve button
> This reduces search ambiguity and increases grounding precision.

## Common Parameters (all providers)

| Parameter | Type | Required | Description | Examples |
|-----------|------|----------|-------------|---------|
| `restaurant_name` | string | true | Name of restaurant to book | "The French Laundry", "Nobu" |
| `party_size` | string | true | Number of guests | "2", "4", "6" |
| `date` | string | true | Reservation date | "2026-03-15", "March 15", "this Saturday" |
| `time` | string | true | Preferred time | "7:00 PM", "19:00", "7pm" |

## Provider 1: OpenTable

### Skill file: `restaurant_opentable.md`

### Trigger keywords
`["book opentable", "reserve opentable", "opentable reservation", "opentable", "make opentable booking"]`

### Target Lexicon (OpenTable UI elements)
- Party size dropdown: "Party size" (shows "2 people", "3 people", etc.)
- Date field: "Date" input or calendar icon
- Time field: "Time" dropdown (shows "7:00 PM", "7:30 PM", etc.)
- Search button: "Find a Time"
- Time slot buttons: individual time chips like "7:00 PM", "7:15 PM", "7:30 PM"
- Reservation confirmation button: "Complete reservation"
- First name field: "First name"
- Last name field: "Last name"
- Email field: "Email address"
- Phone field: "Phone number"

### Step-by-step flow

1. Open Safari and navigate to opentable.com
   - verify: Safari is open and address bar shows opentable.com

2. Search for the restaurant by name ({{restaurant_name}}) using the search bar at the top
   - verify: Search results showing restaurant listings are visible

3. Click on the restaurant named "{{restaurant_name}}" in the search results
   - verify: The restaurant's reservation page is open, showing the reservation widget

4. Set party size to {{party_size}} using the "Party size" dropdown (options shown as "2 people", "3 people")
   - verify: Party size dropdown shows the selected value matching {{party_size}}

5. Set the date to {{date}} using the "Date" field or calendar picker
   - verify: Date field shows {{date}}

6. Set time preference to {{time}} using the "Time" dropdown
   - verify: Time dropdown shows value closest to {{time}}

7. Click the "Find a Time" button
   - verify: Available time slot chips appear below the search form

8. Click on the time slot button closest to {{time}} (e.g., "7:00 PM" chip)
   - verify: A reservation details panel or confirmation modal has opened

9. Complete the reservation form (first name, last name, email, phone if prompted)
   - verify: All required fields are filled in

10. Click "Complete reservation" to submit
    - verify: A confirmation page is shown with a reservation number or "Reservation confirmed" message

### Success condition
Reservation confirmation page is visible with booking reference number or "Reservation confirmed" text.

---

## Provider 2: Yelp

### Skill file: `restaurant_yelp.md`

### Trigger keywords
`["book yelp", "reserve yelp", "yelp reservation", "yelp restaurant booking", "find table yelp"]`

### Target Lexicon (Yelp UI elements)
- Reservation tab: "Make a Reservation" (tab on restaurant page, may also appear as "Reserve")
- Party size dropdown: "Party Size" (shows "1 person", "2 people", etc.)
- Date dropdown: "Date" (shows month/day or calendar picker)
- Time dropdown: "Time" (shows "6:00 PM", "6:30 PM", etc.)
- Search button: "Find a Table"
- Available slot buttons: time chips like "6:45 PM", "7:00 PM", "7:15 PM"
- Continue/book button: "Book" or "Continue" within the reservation flow

### Step-by-step flow

1. Open Safari and navigate to yelp.com
   - verify: Safari is open and yelp.com is loaded

2. Search for {{restaurant_name}} using the Yelp search bar (business field)
   - verify: Search results are visible showing restaurant listings

3. Click on the restaurant named "{{restaurant_name}}" from the results
   - verify: The restaurant's Yelp business page is open

4. Click the "Make a Reservation" tab or button on the restaurant page
   - verify: The reservation widget is visible with party size, date, and time fields

5. Select {{party_size}} guests from the "Party Size" dropdown (options like "2 people")
   - verify: Party Size dropdown shows {{party_size}} people selected

6. Select {{date}} from the "Date" dropdown or date picker
   - verify: Date field shows {{date}}

7. Select {{time}} from the "Time" dropdown
   - verify: Time dropdown shows time closest to {{time}}

8. Click the "Find a Table" button
   - verify: Available time slot chips appear (e.g., "6:45 PM", "7:00 PM", "7:15 PM")

9. Click the time slot chip closest to {{time}}
   - verify: A booking confirmation form or Resy/OpenTable embedded widget is shown

10. Complete any required fields and click "Book" or "Continue"
    - verify: Reservation confirmation is displayed with booking details

### Success condition
Reservation confirmation is visible with booking details or a "Reservation confirmed" message.

---

## Provider 3: Google Maps (restaurant page)

### Skill file: `restaurant_google.md`

### Trigger keywords
`["book google", "reserve google maps", "google restaurant reservation", "google maps reservation", "reserve via google"]`

### Target Lexicon (Google Maps restaurant reservation UI elements)
- Reserve button on business page: "Reserve a table" (blue button)
- Embedded widget provider label: "Resy", "OpenTable", or "Tock" (varies by restaurant)
- Party size selector: "Party size" or "Guests" (within the embedded widget)
- Date picker: "Date" or calendar control (within embedded widget)
- Time selector: "Time" dropdown or time slot grid
- Find/Search button: "Find a time" or "Search" (within embedded widget)
- Confirmation button: "Reserve" or "Complete reservation" (within embedded widget)

### Step-by-step flow

1. Open Safari and navigate to maps.google.com
   - verify: Safari is open and Google Maps is loaded

2. Search for {{restaurant_name}} in the Google Maps search bar
   - verify: A map result or sidebar shows the restaurant named "{{restaurant_name}}"

3. Click on the restaurant listing in the search results or map sidebar
   - verify: The restaurant details panel is open showing address, hours, and action buttons

4. Click the "Reserve a table" button on the restaurant details panel
   - verify: A reservation widget (Resy, OpenTable, or Tock embedded) has appeared

5. Select {{party_size}} guests using the party size selector in the embedded widget
   - verify: Party size shows {{party_size}} selected

6. Select {{date}} using the date picker in the embedded widget
   - verify: Date field shows {{date}}

7. Select {{time}} using the time dropdown in the embedded widget
   - verify: Time field shows {{time}}

8. Click the "Find a time" or "Search" button within the embedded widget
   - verify: Available time slots are displayed in the widget

9. Click on the time slot closest to {{time}}
   - verify: The booking details page or confirmation form is shown

10. Complete any required fields (name, email, phone) and click "Reserve" or "Complete reservation"
    - verify: Reservation confirmation page shows a booking number or "Reservation confirmed"

### Success condition
Reservation confirmation is visible with booking details, date, party size, and restaurant name.

---

## Notes on Verification Strategy

Following the 3-tier verification from the orchestrator:
1. Fast Hammerspoon state check: check if expected window/app is frontmost
2. Vision screenshot verification: check for expected UI element text labels
3. Both tiers can detect success/failure for each step

## Skill File Constraints

- Each skill file is fully self-contained (no cross-file dependencies)
- Parameters use `{{double_braces}}` syntax
- Every step must have a `verify:` sub-item
- Element labels match real UI text exactly (case-sensitive where noted)
- Trigger keywords are provider-specific to avoid cross-matching
