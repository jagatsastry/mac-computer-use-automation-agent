# Amazon Return: Customer Test Scenarios

These scenarios are written from the perspective of a real user who has never seen the source code. Each scenario describes what a user would type, what they expect to see happen on screen, and how to judge success.

---

## Scenario 1: Return a specific item by name (Primary)

- **Prompt**: `Return my listerine on Amazon`
- **Category**: `happy_path`
- **Expected Outcome**: The agent opens Amazon's order history, searches for "listerine," finds the most recent matching order, navigates into the return flow, selects a return reason, and completes the process until a return confirmation page (with a shipping label or drop-off instructions) is visible on screen.
- **Success Criteria**:
  1. Amazon order history page loads in the browser (or login page appears and the agent waits for sign-in before continuing).
  2. The agent searches for "listerine" in the order search bar and matching results appear.
  3. The agent clicks into the correct order containing listerine and reaches a return options page.
  4. A return reason is selected and the agent proceeds through each step of the return wizard.
  5. A return confirmation screen is visible showing either a shipping label, QR code, or drop-off location instructions.

---

## Scenario 2: Vague prompt without specifying the item

- **Prompt**: `I need to return something I bought on Amazon last week`
- **Category**: `ambiguous_input`
- **Expected Outcome**: The agent opens Amazon order history. Since the user did not name a specific item, the agent should either browse recent orders and ask for clarification, or make a reasonable attempt to identify recent orders from the past week. The agent should not pick a random item and silently initiate a return on it.
- **Success Criteria**:
  1. Amazon order history page loads successfully.
  2. The agent does not blindly return an arbitrary item -- it either surfaces recent orders for the user to confirm, or asks the user to specify which item.
  3. If the agent proceeds with an item, it is from a recent order (within the last week), not an old one.
  4. The agent does not complete a return without the user having a chance to confirm the correct item.

---

## Scenario 3: Item is not eligible for return

- **Prompt**: `Return my Kindle ebook purchase on Amazon`
- **Category**: `error_recovery`
- **Expected Outcome**: The agent opens order history, searches for a Kindle ebook, and discovers that digital purchases are not eligible for the standard return flow. The agent should recognize this and communicate clearly to the user that the item cannot be returned through the normal process, rather than getting stuck in a loop or clicking random buttons.
- **Success Criteria**:
  1. Amazon order history loads and the agent searches for the ebook.
  2. The agent identifies that the item is a digital purchase or otherwise not eligible for return.
  3. The agent stops the return flow and provides a clear message explaining why the return cannot be completed (e.g., "digital items are not eligible for return").
  4. The agent does not enter an infinite retry loop or click unrelated UI elements.

---

## Scenario 4: Different phrasing -- casual refund request

- **Prompt**: `I want my money back for the headphones I got from Amazon`
- **Category**: `happy_path`
- **Expected Outcome**: The agent interprets "money back" as a return/refund request. It navigates to Amazon order history, searches for "headphones," finds the matching order, and walks through the return flow to completion -- same as Scenario 1 but triggered by refund-oriented language rather than the word "return."
- **Success Criteria**:
  1. The agent correctly interprets the prompt as a return/refund request (not a complaint or search).
  2. Amazon order history loads and "headphones" is searched.
  3. The correct headphones order is located and the return flow is initiated.
  4. The return wizard completes with a confirmation screen visible.
  5. The flow is substantively the same as a direct "return" request -- the different phrasing does not cause a different (worse) path.

---

## Scenario 5: Item exists but return window has closed

- **Prompt**: `Return the phone case I bought on Amazon six months ago`
- **Category**: `error_recovery`
- **Expected Outcome**: The agent navigates to order history, searches for "phone case," and finds the order. When it attempts to initiate a return, Amazon's UI should indicate the return window has passed (typically 30 days). The agent should recognize this and inform the user rather than getting stuck.
- **Success Criteria**:
  1. Amazon order history loads and the agent searches for "phone case."
  2. The agent finds the order from approximately six months ago.
  3. When the return option is unavailable or greyed out, the agent recognizes the return window has expired.
  4. The agent communicates to the user that the item is no longer eligible for return due to the return window being closed.
  5. The agent does not repeatedly retry or click unrelated controls.
