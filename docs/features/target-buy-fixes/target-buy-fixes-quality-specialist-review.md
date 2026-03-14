# Code Quality Specialist Review: Target Buy Fixes (9 Gaps)

**Reviewer**: Code Quality Specialist (AI)
**Spec Version**: R7
**Date**: 2026-03-13
**Verdict**: **APPROVED** with 2 non-blocking observations

---

## Round 1: Design Smells, Naming, Coupling

### 1.1 `extract_site_entity` — Placement and Scope

**Observation**: The spec places `extract_site_entity()`, `_build_known_sites()`, `_build_site_patterns()`, and `_SEED_SITES` in `router.py`. However, `router.py` is a class-based module (`SkillRouter`) focused on LLM-driven routing. These new functions are pure, stateless utilities with no dependency on `SkillRouter`. The registry (`registry.py`) also imports them. This creates a conceptual mismatch: the router module owns extraction logic that the registry consumes.

**Assessment**: Acceptable. The alternative (a new `site_extraction.py` module) would add a file for ~50 lines of code. The functions are module-level, not methods on `SkillRouter`, so they are importable without instantiating the router. The coupling is import-level only, which is appropriate. The spec explicitly calls out this decision and the import direction (`router.py` is in the `skills` package, imported by `orchestrator/agent.py` — existing import direction). Not blocking.

**Verdict**: Non-blocking. Placement is acceptable for the current skill count. If the extraction logic grows (e.g., NER, embedding-based matching), factor into a separate module at that point.

### 1.2 `_filter_by_site` — Filter Placement (Registry vs Router)

**Observation**: `_filter_by_site()` is a method on `SkillRegistryImpl`, not on `SkillRouter`. The router generates candidates; the registry filters them. This is a sound separation: the router doesn't need to know about site filtering, and the registry (which owns the skill metadata) is the right place to enforce site constraints.

**Verdict**: Good design. The registry is the authority on skill metadata and should own the filtering decision.

### 1.3 `_build_known_sites` — Dynamic Derivation Pattern

**Observation**: `_build_known_sites(skills)` derives the known-site set from skill metadata at rebuild time, supplemented by `_SEED_SITES`. This means the known-site set stays in sync as skills are loaded/promoted. The pattern is clean: `_rebuild_router()` already exists as the synchronization point, and adding `self._known_sites = _build_known_sites(self._skills)` there is minimal and idiomatic.

**Potential concern**: The seed list (`_SEED_SITES`) is a hardcoded frozenset. If a new e-commerce site needs to be supported but has no skill yet, someone must edit `_SEED_SITES`. However, the spec explicitly scopes this: "The extractor does NOT attempt open-ended NER." This is a deliberate tradeoff (precision over recall) and well-justified.

**Verdict**: Clean pattern. No issues.

### 1.4 Naming Consistency: `extract_site_entity` vs PRD's `extract_site_entities`

**Observation**: The PRD (AC-4) specifies `extract_site_entities(prompt: str, known_sites: list[str]) -> list[str]` (plural). The spec defines `extract_site_entity(prompt: str, known_sites: Optional[frozenset[str]] = None) -> Optional[List[str]]` (singular name, but returns a list).

**Assessment**: The spec's signature is better:
- The `Optional[List[str]]` return type correctly distinguishes "no site detected" (`None`) from "one site" (`["target"]`) from "conflict" (`["amazon", "target"]`). The PRD's `list[str]` would use `[]` for "no site detected," which is ambiguous with "parser ran but found nothing."
- The `frozenset[str]` parameter type is more precise than `list[str]` — known sites are a set, not ordered.
- The singular `extract_site_entity` name is slightly misleading since it can return multiple sites. But the docstring clearly documents the multi-site behavior, and the singular name reads naturally at the call site (`site_entity = extract_site_entity(prompt)`).

**Verdict**: Minor naming nit. The function name could be `extract_site_entities` (plural) to match the return type. But this is cosmetic and the docstring is clear. Non-blocking.

### 1.5 `required-keywords` — New Metadata Field

**Observation**: The spec introduces `required-keywords` in skill frontmatter as a new concept. This is checked in `matcher.py`'s keyword fallback. The pattern is: `if skill.metadata.get("required-keywords")` — using the existing metadata catch-all dict.

**Assessment**: This is clean. No changes to `loader.py` or `models.py` needed. The metadata dict already absorbs unknown YAML keys. The keyword matcher checks for the field only when present (backward-compatible). Skills without `required-keywords` work exactly as before.

**Concern**: The field name uses a hyphen (`required-keywords`), matching the existing skill frontmatter convention (`trigger-keywords`, `skill-id`). Consistent.

**Verdict**: Clean. No issues.

### 1.6 Skill Template: `buy_on_target.md` Step Verb Patterns

**Observation**: The spec includes a detailed table mapping each skill step to the `_compile_skill_instruction()` regex patterns. This is excellent engineering discipline — the spec author verified that every step compiles. The codebase analysis independently identified that "Sort results by price" and "Select the first product" would return `None` (aborting fallback), and the skill template avoids these patterns.

**Verdict**: Excellent. This cross-validation between skill template and compiler is a model for future skill authoring.

### 1.7 `_no_visible_change` Metadata on `actuator_result`

**Observation**: P1-2 stores `actuator_result["_no_visible_change"] = True` as metadata. The `_` prefix signals internal-only. The dict is already used to pass metadata (e.g., `image_x`, `image_y`). This is consistent with the existing pattern.

**Concern**: The `actuator_result` dict is untyped (`Dict[str, Any]`). Adding more metadata keys increases the surface area of implicit coupling between the dispatcher and the verifier. However, this is a pre-existing pattern, not something introduced by this spec. The spec uses it minimally (one key for open_url, two keys for scroll).

**Verdict**: Acceptable. The untyped dict is a known tech debt item but not one this spec should address. The spec's usage is minimal and well-documented.

---

## Round 2: Sharpened Findings — Concrete Issues with Alternatives

### 2.1 Domain Verification Injection: Verify-Text Mutation vs Structured Data

**Observation**: P2-3 injects domain constraints by mutating `step.verify` text: `step.verify = f"{step.verify} AND browser domain is target.com"`. The verifier then parses this back out with a regex (`r"browser domain is (\S+)"`). This is a stringly-typed protocol — data is encoded into a string and decoded back.

**Alternative**: Add an `expected_domain: Optional[str]` attribute to `ActionStep`. The orchestrator sets it; the verifier reads it directly. No string encoding/decoding, no regex parsing.

**Assessment**: The spec explicitly addresses this tradeoff: "This approach requires no changes to the verifier's interface — it operates entirely through existing verify-text processing." The PRD's AC-17 also specifies this approach. The motivation is minimizing interface changes. Since `ActionStep` is a `@dataclass` (not frozen), adding an attribute is trivial, but it requires changes to:
- `shared_models.py` (add field)
- `protocols.py` (update any protocol references — none directly, so no impact)
- All test fixtures that construct `ActionStep`

The string-based approach avoids all of this. The regex is simple and specific. The idempotency guard (`if marker not in step.verify`) prevents bloat.

**Verdict**: The string-based approach is acceptable for a single domain constraint. If more structured data needs to be injected into verification (e.g., expected page title, expected cart count), the pattern should be revisited in favor of structured attributes. Non-blocking.

### 2.2 Scroll Verification: `actuator_result` as Data Bus

**Observation**: P1-3 uses `actuator_result` as a data bus between the orchestrator and verifier, passing `_scroll_y_before` and `_scroll_pixel_changed` through the dict. This means:
- The orchestrator captures data before dispatch and stores it in the result dict.
- The verifier reads these keys from the same dict.
- The keys are `_`-prefixed (internal convention) but not formally documented.

**Alternative**: A structured return type (e.g., `ScrollResult` dataclass) would make the contract explicit. But this would require changes to the `Actuator` protocol and all consumers.

**Assessment**: The spec notes that `get_scroll_position()` has a `getattr` guard in the verifier, which handles actuators that don't implement it (e.g., `HammerspoonActuator`). The pixel-diff data is also guarded: `actuator_result.get("_scroll_pixel_changed")` returns `None` when the key is absent (when `screenshot_diff` is `None`). Both fallback paths are well-defined.

**Verdict**: Acceptable. The `actuator_result` dict is already the de facto data bus for step execution metadata. The spec's usage follows established patterns. A structured type would be cleaner but is a larger refactor. Non-blocking.

### 2.3 Truncation Detection: `plan_interactions < 3` Threshold

**Observation**: AC-10 defines truncation as `fallback_interactions >= 3 and plan_interactions < 3`. The spec implements this directly. The threshold of 3 is well-justified:
- The `buy_on_target` skill has 4 interaction steps (type_text + 3 clicks).
- A plan with `open_url -> click "search" -> done` has 1 interaction — truncated.
- A plan with `open_url -> type "product" -> click "product" -> click "add to cart" -> done` has 3 interactions — not truncated.
- 3 is the minimum for a complete buy workflow (search + select + add-to-cart).

**Concern**: The original check was `fallback_has_interaction and not plan_has_interaction` (any-vs-none). The new check adds a ratio-based comparison. This is a behavioral change that could theoretically affect other skills. However, the guard `fallback_interactions >= 3` ensures this only triggers when the fallback itself has a deep workflow. Simple skills with 1-2 interaction steps won't be affected.

**Verdict**: Well-justified threshold. The guard ensures backward compatibility for simple skills. No issues.

### 2.4 `get_scroll_position` — AppleScript in Actuator

**Observation**: The spec adds `get_scroll_position()` to `AppleScriptActuator`. This method executes a multi-line AppleScript that:
1. Determines the frontmost app via System Events.
2. Branches on Safari vs Chrome vs other.
3. Executes JavaScript to get `window.scrollY`.

This follows the existing pattern of `_get_browser_url()` (applescript_actuator.py:293-316), which also branches on browser type.

**Assessment**: The spec validates Chrome's AppleScript syntax: `execute front window's active tab javascript "window.scrollY"`. The possessive syntax (`front window's active tab`) is valid AppleScript for Chrome. The spec also notes the `HammerspoonActuator` path: no `get_scroll_position` method, so the verifier's `getattr` guard skips Tier S1.

**Verdict**: Clean. Follows existing actuator patterns. No issues.

### 2.5 Skill Template: Step 6 Empty Verify

**Observation**: The `buy_on_target.md` template has step 6: `Use done to confirm product added to cart. Checkout requires user confirmation.` with `- verify:` (empty). The spec notes that `done` steps have empty verify fields, which is consistent with the existing `amazon_search.md` pattern.

**Concern**: The PRD (AC-6) says "Each step must have a `verify` condition." An empty verify on the `done` step technically violates this. However, `done` is a terminal action — there's nothing to verify after task completion. The existing validation (`plan.validate()`) requires non-empty verify for action steps but may exempt `done`.

**Verdict**: Non-blocking. The `done` step is a semantic terminator, not an action. An empty verify is consistent with existing skills and the validation logic.

---

## Round 3: Deep Issues — Maintainability, Abstraction Quality, Test Strategy

### 3.1 Maintainability: `_compile_skill_instruction()` Regex Table

**Observation**: The 11-pattern regex table in `_compile_skill_instruction()` is the most fragile part of the system. Every new skill must be written to match these patterns, or the fallback plan fails silently (`None` return). The spec handles this well for `buy_on_target.md` by cross-validating each step against the pattern table. However, future skill authors may not have this spec's rigor.

**Assessment**: This is a pre-existing design constraint, not one introduced by this spec. The spec correctly identifies it as a "landmine" and documents the requirement clearly. The proposed compilation tests (`test_buy_on_target_steps_compile`) provide a safety net.

**Recommendation for future**: Consider adding a `_validate_skill_compilability()` method to `SkillRegistryImpl` that runs at skill load time, warning (not erroring) when a step doesn't match any compiler pattern. This would catch new skills that break the fallback path. However, this is out of scope for the current spec.

**Verdict**: Non-blocking. The spec handles this constraint well given the existing architecture.

### 3.2 Test Strategy: Integration Coverage for Cross-Slice Dependencies

**Observation**: The spec defines integration points between slices (Section 5.4) but the test plans are per-slice. The SOTA analysis identifies 5 integration test scenarios (Section "Test Strategy Implications"). However, these are listed as recommendations, not as required test cases in the slice test plans.

**Assessment**: The test plans cover unit tests thoroughly. Each slice's test plan is self-contained and well-structured. The cross-slice integration scenarios (e.g., "routing + skill + plan depth" end-to-end) would require all three slices to be complete, which means they can only be written after all engineers finish. This is acknowledged by the task list (task #11: "Integration testing: cross-component tests for all 9 fixes").

**Verdict**: Non-blocking. The unit test coverage is adequate for per-slice validation. Integration tests are deferred to task #11, which is the correct sequencing.

### 3.3 Abstraction Quality: Domain Injection in Orchestrator

**Observation**: `_inject_domain_verification()` is a method on `AutomationAgent` that mutates `ActionStep.verify` text. This places domain-awareness logic in the orchestrator, which is the right abstraction level — the orchestrator has access to both the skill metadata and the plan, and is the natural coordination point.

The verifier's domain check (`_extract_base_domain()` + regex parsing of verify text) is also well-placed — it's in the verification pipeline where URL validation already happens.

**Concern**: The `expected_domain = f"{site_entities[0]}.com"` construction assumes all sites map to `{site}.com` domains. This is true for the current known sites (target.com, amazon.com, walmart.com) but wouldn't work for a site like "zappos" (zappos.com works) or a site with a non-.com TLD. However, the spec scopes this to the seed list, which is all `.com` sites.

**Verdict**: Acceptable for current scope. If non-.com sites are added to the seed list, the domain construction logic would need updating. This is a known limitation, not a design flaw.

### 3.4 Test Strategy: Negative Test Coverage

**Observation**: The test plans include good negative cases:
- `test_no_site_generic` — no false positive for generic prompts
- `test_no_false_positive_product` — "buy target gift card" doesn't extract site
- `test_no_false_positive_verb` — "target the cheapest option" doesn't extract site
- `test_keyword_no_target_no_match` — generic "buy" doesn't match buy-on-target
- `test_domain_no_constraint` — no domain check when no constraint injected
- `test_type_text_no_element_no_click` — no click when no element param
- `test_click_no_diff_still_fails` — click diff behavior unchanged

This is thorough negative test coverage that validates backward compatibility (AC-19).

**Verdict**: Excellent test strategy. Negative cases are well-covered.

### 3.5 Consistency: Scroll Tier Pattern vs Existing Verification Tiers

**Observation**: The existing verification system has a 3-tier pattern:
1. Tier 1: Hammerspoon/AppleScript state query (~50ms)
2. Tier 2: Vision screenshot verification (2-5s)
3. Tier 3: Falls through tiers

The scroll verification introduces its own 3-tier pattern:
1. Tier S1: JavaScript scrollY delta
2. Tier S2: Screenshot pixel-diff
3. Tier S3: Actuator success fallback

These are named "S1/S2/S3" to distinguish from the existing "Tier 1/Tier 2/Tier 3". The scroll tiers live within Tier 1 of the existing system — the scroll check in `_verify_tier1` implements all three scroll tiers before returning.

**Assessment**: This is a nested tier pattern, not a parallel one. The naming convention (S1/S2/S3 within Tier 1) is clear. The implementation in `_verify_tier1` keeps all scroll logic in one place, which is high cohesion. The existing Tier 2 (vision verification) is still available as a fallback if Tier 1 returns `None`, but the spec notes that vision verification of abstract scroll conditions always fails — hence the scroll-specific tiers.

**Verdict**: Consistent with existing patterns. The naming convention clearly distinguishes scroll tiers from the main verification tiers.

### 3.6 Error Handling: `get_scroll_position` Failure Modes

**Observation**: `get_scroll_position()` has clear failure handling:
- Timeout (3s) → returns `None` → Tier S1 skipped
- Non-browser app → returns `None` → Tier S1 skipped
- Permission denied → returns `None` → Tier S1 skipped
- Parse error (non-numeric output) → `ValueError` caught → returns `None`

All failure modes result in `None`, which triggers graceful degradation to Tier S2/S3. No exceptions escape. This is robust.

**Verdict**: Clean error handling. No issues.

---

## Summary of Findings

### Blocking Issues

None.

### Non-Blocking Observations

| # | Finding | Location | Recommendation |
|---|---------|----------|----------------|
| 1 | `extract_site_entity` (singular) returns a list — name could be plural | Spec 2.1.1 | Consider renaming to `extract_site_entities` for consistency with return type. Cosmetic only. |
| 2 | `expected_domain = f"{site_entities[0]}.com"` assumes .com TLD | Spec 3.3.2 | Document this assumption. If non-.com sites are needed, add a `domain` field to the seed list rather than deriving from site name. |

### Strengths

1. **Compiler cross-validation**: Every skill step is verified against the `_compile_skill_instruction()` regex table. This prevents silent fallback failures.
2. **Backward compatibility analysis**: Section 5.1 systematically addresses each change's regression risk with specific mitigations.
3. **Dead zone analysis** (P2-2): The confidence dead zone (replan cap 0.6 vs threshold 0.7) is a subtle bug that would have been missed without careful code path tracing.
4. **Idempotency guard** on domain injection: Prevents verify text bloat across replans.
5. **Negative test coverage**: Extensive tests for false positives, non-regression, and edge cases.
6. **Slice decomposition**: Clean dependency graph (Slice 3 independent, Slice 2 depends on Slice 1's `extract_site_entity`), enabling parallel engineering work.
7. **Graceful degradation**: AC-4b ensures the system works even without a matching skill. Scroll verification degrades through three tiers. type_text falls through to current focus on find_element failure.

---

## Final Verdict

**APPROVED**

The spec demonstrates high code quality across all dimensions:
- **Right-sized abstractions**: Pure functions for extraction, method on registry for filtering, orchestrator for coordination.
- **Low coupling**: Each fix touches only the necessary files. No new protocols, no new dependencies.
- **High cohesion**: Site extraction logic in router module, domain verification in verifier, scroll verification in verifier's Tier 1.
- **Clean interfaces**: `actuator_result` dict as data bus follows existing patterns. Verify-text injection avoids verifier API changes.
- **Adequate testing**: Each slice has comprehensive unit tests with negative cases. Integration tests are correctly deferred to post-implementation (task #11).

The two non-blocking observations (naming, TLD assumption) are minor and do not affect correctness or maintainability.

---

# Phase 2: Implementation Review

**Date**: 2026-03-13
**Scope**: All 3 engineers' implementations reviewed for code and design quality.
**Method**: Evidence-based pushback (DOER THINK HARDER / PUSH-BACKER THINK HARDER rounds).

---

## Engineer 1 Review (P0-1, P0-2, P2-1)

### Round 1: [EVIDENCE GATE - DOER THINK HARDER] for Engineer 1 [CODE QUALITY]

#### E1-R1-1: `_filter_by_site` type annotation is too loose

**File**: `src/automation_agent/skills/registry.py:543-571`

**Issue**: The method signature is `_filter_by_site(self, candidates: list, site_entity: str) -> list`. The `list` type is unparameterized — it should be `list[SkillRouteCandidate]`. This is inconsistent with the codebase's own convention: the same file uses `list[SkillRouteCandidate]` on line 258, `list[SkillCard]` on line 84, and parameterized types throughout. The method is also called from three different stages in `match()`, each passing `list[SkillRouteCandidate]`, confirming the type.

**Evidence**: `registry.py:258` — `candidates: list[SkillRouteCandidate] = []`. Same variable name, same semantic role, parameterized. `registry.py:543` — `candidates: list` — unparameterized. This is the only `list` annotation without a type parameter in the new code.

**Impact**: Low severity (runtime behavior unchanged), but it degrades IDE type checking and violates the file's own style. Any downstream consumer (e.g., an adversary writing a test) cannot rely on static analysis to catch type errors.

**Fix**: Change signature to `_filter_by_site(self, candidates: list[SkillRouteCandidate], site_entity: str) -> list[SkillRouteCandidate]`.

**Verdict**: PUSHBACK. Fix the type annotations to match the file's established pattern.

---

#### E1-R1-2: `_build_known_sites` and `_build_site_patterns` return types are bare `frozenset` and `list`

**File**: `src/automation_agent/skills/router.py:33,43`

**Issue**: `_build_known_sites(skills: Dict[str, "Skill"]) -> frozenset` — no type parameter. `_build_site_patterns(known_sites: frozenset) -> list` — no type parameter. The `_SEED_SITES` annotation on line 27 is also bare `frozenset` without a type parameter.

This is inconsistent with the function `extract_site_entity` on line 58 which correctly uses `Optional[frozenset]` as parameter type (though also bare) and `Optional[List[str]]` as return type (parameterized).

The Python 3.11 target specified in the CLAUDE.md supports `frozenset[str]` and `list[re.Pattern[str]]` natively.

**Evidence**: Line 27: `_SEED_SITES: frozenset = frozenset({...})` — bare. Line 33: `-> frozenset` — bare. Line 43: `-> list` — bare. Line 61: `-> Optional[List[str]]` — parameterized. The inconsistency is within the same file.

**Impact**: Low severity but makes the type contracts unclear. A caller cannot distinguish `frozenset[str]` from `frozenset[Skill]` without reading the implementation.

**Fix**: `_SEED_SITES: frozenset[str]`, `_build_known_sites(...) -> frozenset[str]`, `_build_site_patterns(...) -> list[re.Pattern[str]]`.

**Verdict**: PUSHBACK. Parameterize the type annotations to match the file's own usage patterns.

---

### Round 2: [EVIDENCE GATE - PUSH-BACKER THINK HARDER] for Engineer 1 [CODE QUALITY]

#### E1-R2-1 (Re-evaluation of E1-R1-1): Is this actually the file's convention?

Checking the broader codebase: `registry.py:84` uses `list[SkillCard]`, `registry.py:258` uses `list[SkillRouteCandidate]`, and the `_build_multi_skill_context` at line 501 uses `list[SkillRouteCandidate]`. The parameterized form is the dominant pattern. The bare `list` on `_filter_by_site` is an outlier.

However, I should check if there are pre-existing bare `list` or `frozenset` annotations in the file. Looking at `registry.py:8` — `from typing import ... Dict, List, Optional` — the file imports both `List` (typing) and uses `list` (builtin). The `typing.List` is used for return types in older methods (e.g., `list_skills() -> List[Dict[str, str]]` at line 573), while newer methods use lowercase `list[...]`. This is a mixed convention, which is fine for Python 3.11. The issue is specifically that `_filter_by_site` uses bare `list` without a parameter at all.

**Verdict**: PUSHBACK SUSTAINED. The bare `list` is not the convention — it's an omission. All other methods in the file parameterize their list types. Fix to `list[SkillRouteCandidate]`.

#### E1-R2-2 (Re-evaluation of E1-R1-2): Are bare frozensets common in this codebase?

The `router.py` file is the only file that uses `frozenset` annotations. There is no pre-existing codebase convention to follow. However, since the file's own `extract_site_entity` parameterizes its return type (`Optional[List[str]]`), the bar is set — type parameters should be used when the element type is known.

**Verdict**: PUSHBACK SUSTAINED but downgraded to non-blocking. The bare `frozenset` is less impactful than the bare `list` because `frozenset` is only used internally between `_build_known_sites` and `_build_site_patterns`. The `_filter_by_site` bare `list` crosses module boundaries (called from `match()`) and is more important to fix.

#### E1-R2-3: Test helper `_make_registry` bypasses constructor, directly mutates `_skills`

**File**: `tests/unit/test_site_routing.py:152-159`

**Issue**: `TestFilterBySite._make_registry()` creates a registry with an empty skill_dir, then directly assigns `registry._skills = skills_dict` and calls `registry._rebuild_router()`. This is testing the internals rather than the public API. If the registry's initialization order changes (e.g., `_rebuild_router` gains a dependency on something set during `load_from_directory`), these tests will silently pass while the production code breaks.

However, the alternative is writing .md skill files to tmpdir and loading them, which `TestSiteFilterIntegration` already does correctly (lines 258-298). The `TestFilterBySite` tests specifically need to test `_filter_by_site` in isolation, and the direct mutation is the cleanest way to do this without file I/O overhead.

**Verdict**: ACCEPTED. The direct mutation pattern is appropriate for unit tests that target a private method. The integration tests (`TestSiteFilterIntegration`) cover the full path through the public API. This is a reasonable test strategy split.

---

**Engineer 1 Summary**: 2 pushbacks issued, both on type annotation completeness. Code logic, test coverage, naming, and architecture are clean. The skill template (`buy_on_target.md`) is well-crafted with correct step verbs.

---

## Engineer 2 Review (P0-3, P1-1, P2-3)

### Round 1: [EVIDENCE GATE - DOER THINK HARDER] for Engineer 2 [CODE QUALITY]

#### E2-R1-1: `_is_truncated_plan` has a magic number threshold without a named constant

**File**: `src/automation_agent/orchestrator/agent.py:1493-1518`

**Issue**: The threshold `3` appears twice in `_is_truncated_plan` (lines 1516): `if fallback_interactions >= 3 and plan_interactions < 3`. This is a magic number that controls whether a plan is considered truncated. The docstring justifies the value well ("Fixed threshold of 3 is calibrated for current skills"), but the number is not extracted into a named constant.

**Evidence**: The same method already has `interaction_actions = {"click", "type_text", "scroll"}` as a named local. The comment on line 1499 says "For skills with 6+ interaction steps, consider ratio-based." This means the `3` is a deliberate design choice, not an arbitrary number — all the more reason to give it a name.

Compare with the codebase's pattern: `MIN_USEFUL_CONFIDENCE = 0.5` in `router.py:23` — thresholds with behavioral significance are given module-level names.

**Impact**: Medium. When a future engineer reads `plan_interactions < 3`, they have to read the docstring to understand why it's 3. A named constant like `_MIN_DEEP_INTERACTIONS = 3` at the class or module level would be self-documenting. Also, the value appears twice — if someone changes one occurrence but not the other, the logic silently breaks.

**Fix**: Extract to a class-level constant: `_MIN_DEEP_INTERACTIONS = 3`, then reference it in both comparisons.

**Verdict**: PUSHBACK. Extract the magic number to a named constant. The value appears in two comparisons and has a documented rationale that should be captured in the name.

---

#### E2-R1-2: `_inject_domain_verification` source_entity computation is dead code

**File**: `src/automation_agent/orchestrator/agent.py:1533`

**Issue**: Line 1533 computes `source_entity = expected_domain.replace(".com", "")` and uses it only in the `slog.debug` call on line 1535. The variable `source_entity` is never used for any logic — it's purely for logging. Computing a derived value solely for a debug log is unnecessary overhead. The `expected_domain` itself (e.g., `"target.com"`) is already logged and sufficient for debugging.

**Evidence**: The variable `source_entity` is defined on line 1533, referenced only on line 1537 (`source_entity=source_entity`), and appears nowhere else in the method. The `.replace(".com", "")` is also fragile — it would produce `"bestbuy"` from `"bestbuy.com"` but `"target.co"` from `"target.co.uk"` if non-.com TLDs are ever added.

**Impact**: Very low (it's debug logging), but it's unnecessary code that adds cognitive load. A reader encountering `source_entity` wonders "where is this used for logic?" only to find it's just logging.

**Fix**: Remove `source_entity` and log `expected_domain` directly, or keep it but document it as logging-only with a comment.

**Verdict**: PUSHBACK (minor). Remove the dead intermediate variable. Log `expected_domain` directly in the debug call.

---

### Round 2: [EVIDENCE GATE - PUSH-BACKER THINK HARDER] for Engineer 2 [CODE QUALITY]

#### E2-R2-1 (Re-evaluation of E2-R1-1): Is a named constant actually better here?

The docstring already explains the threshold. The method is `@staticmethod` and self-contained. Adding a class-level constant means `_MIN_DEEP_INTERACTIONS` would be visible to all other methods on `AutomationAgent`, even though only `_is_truncated_plan` uses it. A module-level constant would be even more exposed.

Counter-argument: the value `3` appears in exactly two comparisons within the same `if` statement (`fallback_interactions >= 3 and plan_interactions < 3`). A reader who sees one `3` immediately sees the other. The DRY violation is minimal because it's a single expression.

However, the constant still serves a documentation purpose. `fallback_interactions >= _MIN_DEEP_INTERACTIONS` reads better than `fallback_interactions >= 3` even if the value appears only twice.

**Verdict**: PUSHBACK SUSTAINED but downgraded to non-blocking. The magic number is acceptable with the existing docstring, but a named constant would be better. Not a required fix.

#### E2-R2-2 (Re-evaluation of E2-R1-2): Is the source_entity truly dead code?

Re-reading the debug log: `slog.debug("domain_verification_injected", expected_domain=expected_domain, source_entity=source_entity, step_action=step.action)`. The `source_entity` provides the un-dotted site name for log correlation (e.g., matching against `extract_site_entity` output which returns `["target"]` not `["target.com"]`). This is useful for grep-based log analysis: searching for `source_entity=target` would correlate domain injection with site extraction events.

This is a judgment call. The value isn't dead code — it serves a log correlation purpose. But it could also be computed on read (by the log consumer) rather than on write.

**Verdict**: PUSHBACK WITHDRAWN. The `source_entity` serves a legitimate log correlation purpose (matching the `extract_site_entity` output format). Keep it.

#### E2-R2-3: type_text focus flow has no test for `_clear_first` and `_slow_type` interaction with element focus

**File**: `tests/unit/test_type_text_focus.py`

**Issue**: The tests thoroughly cover the click-to-focus path (9 test cases), but none test the interaction between `element` + `_clear_first` or `element` + `_slow_type`. In `_dispatch_action` (agent.py:2218-2226), after the focus click, the code checks `_clear_first` and `_slow_type` — these are orthogonal features that compose with the focus click. A test like `test_type_text_element_with_clear_first` would verify the full composition.

**Evidence**: `agent.py:2218` — `if params.pop("_clear_first", False)` — this runs after the focus click. `test_type_text_focus.py` never includes `_clear_first` or `_slow_type` in any test's params dict.

**Impact**: Low — the features are orthogonal and unlikely to interfere. The `_clear_first` and `_slow_type` paths are tested elsewhere (presumably in the main agent tests). But the composition path is untested: "focus on element X, then clear, then type slowly."

**Verdict**: PUSHBACK (non-blocking). Add at least one composition test covering `element` + `_clear_first` to verify the full flow. This is a gap in the test matrix, not a code quality issue.

---

**Engineer 2 Summary**: 2 pushbacks issued (1 on magic number extraction — downgraded to non-blocking; 1 on test composition gap — non-blocking). The type_text focus fix is clean with proper error handling and graceful fallthrough. The domain verification injection is well-designed with idempotency guard. The truncation detection logic is well-documented and correctly threshold-gated.

---

## Engineer 3 Review (P1-2, P1-3, P2-2)

### Round 1: [EVIDENCE GATE - DOER THINK HARDER] for Engineer 3 [CODE QUALITY]

#### E3-R1-1: `get_scroll_position` calls `self.get_state()` redundantly

**File**: `src/automation_agent/actuator/applescript_actuator.py:311-349`

**Issue**: `get_scroll_position()` calls `self.get_state()` at the top (line 320) to determine the frontmost app name, then constructs a browser-specific AppleScript. But `get_state()` is itself an osascript call that returns `app_name`, `window_title`, etc. This means a single `get_scroll_position()` call incurs two subprocess invocations: one for `get_state()` and one for the scrollY query.

In the verifier (verifier.py:481-483), `get_scroll_position()` is called AFTER `actuator.get_state()` has already been called at line 345 (`state = actuator.get_state()`). The `state["app_name"]` is already available, but `get_scroll_position()` re-fetches it.

**Evidence**: `verifier.py:345` calls `actuator.get_state()`, then `verifier.py:483` calls `get_scroll()` which internally calls `self.get_state()` again. That's 3 subprocess calls (2x get_state + 1x scrollY) when 2 would suffice (1x get_state + 1x scrollY).

**Impact**: Each subprocess call to osascript takes ~50-200ms. The redundant call adds ~100ms latency to every scroll verification. For e-commerce workflows with multiple scroll actions, this accumulates.

**Fix**: Add an optional `app_name: Optional[str] = None` parameter to `get_scroll_position()`. If provided, skip the `get_state()` call. The verifier can pass `state.get("app_name")` from its existing state query.

**Verdict**: PUSHBACK. The redundant subprocess call is a measurable performance issue in a latency-sensitive verification path. Accept an optional `app_name` parameter to avoid the double call.

---

#### E3-R1-2: `_escape_for_applescript` is a `@staticmethod` but not defensive about non-string input

**File**: `src/automation_agent/actuator/applescript_actuator.py:296-309`

**Issue**: `_escape_for_applescript(text: str) -> str` is typed as accepting `str`, but `type_text` at line 66 passes `text` directly from `params.get("text", "")`. The params dict is `Dict[str, Any]`, so if `text` is accidentally an `int` or `None`, the `.replace()` chain will raise `AttributeError`. The existing `type_text` path always passes a string (due to the default `""`), so this is not a current bug — but the method is `@staticmethod` and therefore callable from other contexts.

However, looking at the broader pattern: `_get_browser_url` (line 351) also takes `app_name: str` without defensive checks, and the entire actuator assumes valid string inputs from the params dict. Adding defensive checks here but not elsewhere would be inconsistent.

**Verdict**: WITHDRAWN pre-emptively. The `@staticmethod` follows the same pattern as other actuator methods. Adding input validation here would be inconsistent with the codebase's trust-internal-code approach (per CLAUDE.md: "Only validate at system boundaries").

#### E3-R1-3: Scroll pixel_changed capture has a hard-coded 0.5s sleep

**File**: `src/automation_agent/orchestrator/agent.py:2283`

**Issue**: After the scroll action, `await asyncio.sleep(0.5)` is hardcoded before capturing the pixel diff. This 500ms delay is added to every scroll action, regardless of whether `screenshot_diff` is active. Looking more carefully: the sleep is inside the `if self.screenshot_diff:` block (line 2282), so it only fires when screenshot diff is enabled. But the 0.5s value is not configurable and not documented.

Compare with `action_delay` (line 2063, 2202, etc.) which uses `self.config.action_delay` — a configurable value. The scroll sleep is an ad-hoc constant.

**Evidence**: `agent.py:2283` — `await asyncio.sleep(0.5)` — hardcoded. `agent.py:2063` — `await asyncio.sleep(max(self.config.action_delay, 0.2))` — configurable. The inconsistency suggests the scroll sleep was ad-hoc.

**Impact**: Low. The 0.5s is reasonable for waiting for scroll animations to settle before capturing a screenshot. Making it configurable would add complexity without clear benefit since the value is closely tied to UI rendering timing.

**Verdict**: PUSHBACK (non-blocking). The 0.5s sleep should at minimum have a comment explaining why this value was chosen (e.g., "Wait for scroll animation to settle before pixel diff"). The code is unclear about whether the delay is for UI rendering, network content loading, or something else. A brief comment would prevent a future engineer from "optimizing" it away.

---

### Round 2: [EVIDENCE GATE - PUSH-BACKER THINK HARDER] for Engineer 3 [CODE QUALITY]

#### E3-R2-1 (Re-evaluation of E3-R1-1): Is the redundant get_state() actually a problem?

The scroll verification path in the verifier is:
1. `state = actuator.get_state()` (line 345) — used for ALL step types, not just scroll
2. `get_scroll = getattr(actuator, "get_scroll_position", None)` (line 481)
3. `scroll_after = get_scroll()` (line 483) — internally calls `get_state()` again

The first `get_state()` call is shared infrastructure — it's used by the URL matching, app detection, and other verification logic above the scroll block. Passing the `app_name` from `state` to `get_scroll_position()` would require changing the verifier's call site, which works with a `getattr` guard. The `getattr` pattern means the verifier doesn't know the signature of `get_scroll_position()` — it just calls it with no args.

Adding an optional parameter would still work with the `getattr` pattern: `get_scroll()` with no args would still call `get_state()` internally (backward compat), while `get_scroll(app_name="Safari")` would skip it. But the verifier's call site would need to change from `get_scroll()` to `get_scroll(app_name=state.get("app_name"))`.

The fix is clean and backward-compatible. The performance impact (100ms per scroll verification) is real but marginal in the context of a 2-5s verification cycle.

**Verdict**: PUSHBACK DOWNGRADED to non-blocking. The redundant call is real but not critical. The fix is clean but optional. Keep as a tech debt note.

#### E3-R2-2 (Re-evaluation of E3-R1-3): Is the sleep actually undocumented?

Looking at the surrounding code: `agent.py:1084` — `await asyncio.sleep(0.3)` for click screenshot diff. This also has no comment. The pattern of undocumented sleep durations for screenshot diff is pre-existing, not introduced by Engineer 3.

However, Engineer 3's scroll sleep is *longer* (0.5s vs 0.3s for click) and the difference is not explained. Scroll animations typically take longer than click state changes, which justifies the longer delay — but this reasoning should be in a comment.

**Verdict**: PUSHBACK SUSTAINED (non-blocking). Add a comment explaining the 0.5s sleep. The existing 0.3s click sleep also lacks a comment, but that's pre-existing tech debt — Engineer 3's new code should meet the higher bar.

#### E3-R2-3: open_url false-negative fix — test coverage is correct but `_make_plan` helper hides the test's intent

**File**: `tests/unit/test_open_url_verification.py:64-69`

**Issue**: The `_make_plan` helper appends a `done` step to every plan: `ActionPlan(steps=[step, ActionStep(action="done", params={}, verify="")])`. This is necessary because `_execute_step` may reference `plan.steps[index+1]` or iterate beyond the current step. But the helper obscures the test's intent — a reader has to look at `_make_plan` to understand why a done step is there.

More importantly, `test_click_no_diff_still_fails` (line 157) uses `_make_plan` to wrap a click step, then calls `_execute_step(0, step, [], "test", plan)`. The test verifies `result.success is False`. But the test patches `_dispatch_action` to return `{"success": True}` — so the failure comes from the screenshot_diff gate (line 1097-1109 in agent.py). This causality chain is non-obvious without reading the agent source.

This is not a code quality issue per se — it's a test readability issue. The tests correctly verify behavior, but the abstraction layer (mock patches + helper) makes the test harder to understand in isolation.

**Verdict**: ACCEPTED. The test is correct and the pattern (mock dispatch, verify downstream behavior) is standard for orchestrator tests. The `_make_plan` helper is reused cleanly. Not a pushback.

---

**Engineer 3 Summary**: 2 pushbacks issued (1 on redundant subprocess call — downgraded to non-blocking; 1 on undocumented sleep constant — non-blocking). The open_url false-negative fix is clean with correct metadata tagging. The scroll verification tiered design is well-structured. The `_escape_for_applescript` is minimal and correct.

---

## Implementation Review Verdict

**APPROVED** with 5 non-blocking findings across all 3 engineers (at least 2 per engineer):

| # | Engineer | Finding | Severity | Fix Required? |
|---|----------|---------|----------|---------------|
| 1 | E1 | `_filter_by_site` bare `list` type annotation | Non-blocking | Recommended |
| 2 | E1 | `_build_known_sites`/`_build_site_patterns` bare `frozenset`/`list` | Non-blocking | Recommended |
| 3 | E2 | `_is_truncated_plan` magic number `3` not named | Non-blocking | Nice-to-have |
| 4 | E3 | `get_scroll_position` redundant `get_state()` subprocess call | Non-blocking | Tech debt |
| 5 | E3 | Scroll pixel-diff 0.5s sleep undocumented | Non-blocking | Recommended |

### Cross-Cutting Observations

1. **All three engineers follow the same `_make_config` test helper pattern** (pinning `model_provider="local"` to avoid `.env` leakage). This is consistent and correct.
2. **All new code uses `slog` (structlog) consistently** — no `print()` or `logging.info()` regressions.
3. **No new imports of external packages** — all changes use existing dependencies.
4. **Test naming is descriptive and follows the `test_<scenario>` convention** throughout.
5. **No security issues** — `get_scroll_position` runs hardcoded JavaScript (`"window.scrollY"`) with no user input interpolation, and the engineer added a security comment documenting this (line 317-319).
