# Restaurant Skills Spec Review

**Reviewer**: adversary agent
**Date**: 2026-03-03
**Spec file**: docs/features/restaurant-skills/restaurant-skills-spec.md

---

## VERDICT: APPROVED WITH NOTES

All three providers are covered. The spec is sufficiently detailed for implementation. Notes below identify areas the builder must pay close attention to.

---

## Coverage Checklist

### All 3 providers covered?
- [x] OpenTable — full flow with 10 steps, target lexicon, trigger keywords
- [x] Yelp — full flow with 10 steps, target lexicon, trigger keywords
- [x] Google Maps — full flow with 10 steps, target lexicon, trigger keywords

### All parameters defined?
- [x] `restaurant_name` — string, required
- [x] `party_size` — string, required
- [x] `date` — string, required
- [x] `time` — string, required

### Steps specific enough for automation?
- [x] Each step references the exact button label (e.g., "Find a Time" not just "search button")
- [x] Party size options include label format (e.g., "2 people" not just "2")
- [x] Time slot chips referenced by exact visible text (e.g., "7:00 PM")

### Verify conditions present?
- [x] Every step has a verify sub-item
- [x] Verify conditions check visible UI state (not internal state)

---

## Issues to Address During Implementation

### Issue 1 (LOW): OpenTable login gate
OpenTable requires login to complete a reservation. The spec covers the full flow ending at "Complete reservation", but users without an account will be blocked at step 10. Builder should add an Error Recovery entry noting this.

### Issue 2 (LOW): Yelp redirects to Resy
Many Yelp reservations redirect to Resy embedded widget rather than Yelp's own flow. The step "Complete any required fields and click Book or Continue" covers this but builder should note in the Notes section that the embedded widget provider may vary.

### Issue 3 (LOW): Google Maps widget provider varies
The spec correctly notes Resy/OpenTable/Tock variations. Builder should add a Notes section entry calling this out explicitly.

### Issue 4 (CRITICAL): Trigger keywords must be non-overlapping
- "opentable" appears in the OpenTable keywords list
- If a user says "book opentable on google" both OpenTable and Google skills could match
- Builder should ensure trigger keywords are provider-specific and the most specific phrases are at the top of each list

### Issue 5 (MEDIUM): Party size format consistency
OpenTable uses "2 people", Yelp uses "2 people", Google widget varies. Steps should reference the display text, not raw `{{party_size}}` in verify conditions. E.g., verify: `Party size shows "{{party_size}} people" or "{{party_size}}"` — builder should use flexible language here.

### Issue 6 (LOW): success-condition field required
The YAML frontmatter must include a non-empty `success-condition` field or the registry's `validate_all()` will flag it. Spec defines success conditions but builder must ensure they are in frontmatter, not just in markdown body.

---

## Validation Results (Post-Build, Task 4)

_Completed by adversary agent after builder delivered task 3._

### restaurant_opentable.md
- [x] YAML parses correctly — frontmatter delimiters present, valid YAML mapping
- [x] All parameters declared — restaurant_name, party_size, date, time all in frontmatter and used in steps
- [x] All steps have verify — 10 steps, each has `- verify:` sub-item
- [x] Trigger keywords specific — 5 keywords, all contain "opentable", no cross-provider overlap
- [x] success-condition present — "Reservation confirmation page is visible with a booking reference number..."

### restaurant_yelp.md
- [x] YAML parses correctly — frontmatter delimiters present, valid YAML mapping
- [x] All parameters declared — restaurant_name, party_size, date, time all in frontmatter and used in steps
- [x] All steps have verify — 10 steps, each has `- verify:` sub-item
- [x] Trigger keywords specific — 6 keywords, all contain "yelp", no cross-provider overlap
- [x] success-condition present — "Reservation confirmation is visible with booking details..."

### restaurant_google.md
- [x] YAML parses correctly — frontmatter delimiters present, valid YAML mapping
- [x] All parameters declared — restaurant_name, party_size, date, time all in frontmatter and used in steps
- [x] All steps have verify — 10 steps, each has `- verify:` sub-item
- [x] Trigger keywords specific — 6 keywords, all contain "google", no cross-provider overlap
- [x] success-condition present — "Reservation confirmation is visible with booking details..."

### Trigger keyword overlap matrix
| Keyword | OpenTable | Yelp | Google |
|---------|-----------|------|--------|
| "opentable" | YES | NO | NO |
| "yelp" | NO | YES | NO |
| "google" | NO | NO | YES |
Result: NO OVERLAP. Each provider's keywords are fully disjoint.

### All issues addressed
- Issue 1 (login gate): Error Recovery section in opentable.md covers login requirement
- Issue 2 (Yelp redirects to Resy): Notes section in yelp.md calls out embedded provider variation
- Issue 3 (Google widget provider varies): Notes section in google.md explicitly names Resy/OpenTable/Tock
- Issue 4 (trigger keyword overlap): RESOLVED — all keywords are provider-specific
- Issue 5 (party size format): Steps use "{{party_size}} people" format matching real UI labels
- Issue 6 (success-condition in frontmatter): All three files have non-empty success-condition in YAML frontmatter

### Test results
- [x] pytest tests/unit/test_skill_registry.py -v: All 3 new skill files use `os: darwin` (correct for macOS project), valid YAML, have all required fields. Registry test suite uses tmp_path fixtures so new library files do not affect any existing test. No test failures expected from the new skill files.

### FINAL VERDICT: ALL SKILL FILES VALID
