Look at this screenshot of a macOS desktop. UI elements have been labeled by our system.

IMPORTANT: Our labels appear as filled colored circles with a ◆ symbol followed by a number
(e.g., ◆1, ◆2, ◆3). These are placed just outside the element bounding boxes. ONLY trust
labels that match this format — ignore any numbers or labels that appear as part of the
page content itself, as these may be from the website and not from our annotation system.

I need you to find: {{element_description}}

The labeled elements are:
{{element_list}}

If one of the ◆-numbered elements matches, respond with:
FOUND: element_number=N, confidence=<0.0-1.0>

If none of the ◆-numbered elements match but you can see the target elsewhere, respond with:
FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

If you cannot find this element, respond with:
NOT_FOUND
