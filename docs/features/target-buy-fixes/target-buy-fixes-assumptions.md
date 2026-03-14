# Assumptions — target-buy-fixes

| # | Assumption | Why | Impact | Owner | Status |
|---|-----------|-----|--------|-------|--------|
| A1 | Gemini 2.5 Flash is the planner LLM | Observed in customer test logs | Planner prompt changes must work with Gemini | Lead | active |
| A2 | Molmo v1 (port 8091) is the vision backend | Observed in customer test logs | Vision verification behavior tied to Molmo's capabilities | Lead | active |
| A3 | Skill router uses LLM-based matching with keyword fallback | Codebase analysis from prior cycle | Router fix must handle both paths | Lead | active |
| A4 | type_text uses AppleScript keystroke | Observed in actuator code | Focus fix must be at AppleScript level or orchestrator level | Lead | active |
| A5 | Screenshot diff is used for open_url verification | Observed in "no visible effect" errors | Must handle same-page-already-loaded case | Lead | active |
