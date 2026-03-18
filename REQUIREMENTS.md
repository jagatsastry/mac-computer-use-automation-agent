# macOS Automation Agent - Requirements Document

**Version:** 2.0
**Date:** 2026-02-02
**Status:** Planning Phase
**Base:** Fork of Moltbot

---

## 1. Executive Summary

Build an intelligent macOS automation agent that can execute complex, multi-step tasks autonomously through natural language commands. The agent uses computer vision (Claude's vision API) to understand screen state and performs actions via native macOS automation (AppleScript, Accessibility APIs, PyAutoGUI).

This version will be built as a **fork of Moltbot**, leveraging its:
- Gateway architecture for multi-channel messaging
- Browser automation infrastructure (CDP)
- Agent runtime (Pi Agent)
- CLI framework
- Configuration system

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. **Natural Language Control**: Accept plain English commands like "Book a table for 2 at Joey's for 7pm tonight"
2. **Vision-Based Understanding**: Use Claude's vision to understand current screen state
3. **Autonomous Execution**: Complete multi-step tasks without user intervention
4. **Retina Display Support**: Correctly handle coordinate scaling on HiDPI displays
5. **Cross-Application Automation**: Control any macOS application (not just browsers)
6. **Multi-Channel Access**: Control via Telegram, Discord, iMessage, or local CLI
7. **Reliable Actions**: Prefer AppleScript/Accessibility over pixel-based clicking when possible
8. **Error Recovery**: Detect failures and attempt alternative approaches

### 2.2 Non-Goals

1. Multi-user/multi-tenant deployment (personal use only)
2. Windows/Linux support (macOS only)
3. Real-time video game automation
4. Bypassing security measures or CAPTCHAs for malicious purposes
5. Automating authentication credential entry (security risk)

---

## 3. Architecture Overview

### 3.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MESSAGING CHANNELS                           │
│  Telegram │ Discord │ iMessage │ CLI │ WebChat │ Slack │ ...   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                    MOLTBOT GATEWAY                              │
│              tcp://127.0.0.1:18789 (WebSocket)                  │
│  - Message routing          - Session management                │
│  - Channel connections      - Authentication                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                 macOS AUTOMATION SKILL                          │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │   Intent    │  │   Screen    │  │   Action    │             │
│  │   Parser    │→ │  Observer   │→ │  Executor   │             │
│  │  (Claude)   │  │  (Vision)   │  │ (AppleScript│             │
│  └─────────────┘  └─────────────┘  │  PyAutoGUI) │             │
│                                     └─────────────┘             │
│                          ↑                │                     │
│                          └────────────────┘                     │
│                        Agentic Loop                             │
└─────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                      macOS SYSTEM                               │
│  AppleScript │ Accessibility API │ Screen Capture │ PyAutoGUI  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Component Responsibilities

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| Gateway | Message routing, session mgmt | Moltbot (Node.js/TypeScript) |
| Intent Parser | NL → structured actions | Claude API (text) |
| Screen Observer | Screenshot → understanding | Claude API (vision) |
| Action Executor | Execute automation actions | AppleScript, osascript, PyAutoGUI |
| Agentic Loop | Observe → Think → Act cycle | TypeScript state machine |
| Coordinate Scaler | Retina display handling | Custom (screenshot/logical ratio) |

---

## 4. Functional Requirements

### 4.1 Natural Language Understanding

#### FR-4.1.1: Intent Parsing
- **Input**: Natural language command (e.g., "Open Safari and go to youtube.com")
- **Output**: Structured intent with action steps
- **Format**:
```json
{
  "steps": [
    {"action": "open_url", "params": {"url": "https://youtube.com", "browser": "Safari"}}
  ],
  "requires_observation": false,
  "confidence": 0.95
}
```

#### FR-4.1.2: Complexity Detection
The parser must classify tasks as:
- **Simple**: Can be executed sequentially without vision (e.g., "Open Safari")
- **Complex**: Requires vision-based observation (e.g., "Click the most popular video")

#### FR-4.1.3: URL Inference
- "youtube" → "https://www.youtube.com"
- "google weather sf" → "https://www.google.com/search?q=weather+sf"
- Booking patterns:
  - "book table at X" → OpenTable URL with restaurant name
  - "find flights from X to Y" → Google Flights URL
  - "find hotels in X" → Google Hotels URL

#### FR-4.1.4: App Name Normalization
- "chrome" → "Google Chrome"
- "vscode" → "Visual Studio Code"
- "finder" → "Finder"

### 4.2 Screen Observation

#### FR-4.2.1: Screenshot Capture
- Capture full screen at native resolution
- Support for Retina displays (2x, 3x scaling)
- JPEG compression to stay under API limits (< 5MB)
- Return both raw image and base64 encoded

#### FR-4.2.2: Screen Description
- Input: Screenshot + question (optional)
- Output: Natural language description of screen state
- Must identify:
  - Active application
  - Visible UI elements (buttons, text fields, menus)
  - Current state (loading, error, ready)
  - Relevant text content

#### FR-4.2.3: Element Location
- Input: Screenshot + element description (e.g., "the search button")
- Output: Bounding box coordinates in screenshot pixel space
- Format: `{x1, y1, x2, y2}` with center point calculation
- Must handle:
  - Buttons, links, text fields
  - Images with specific content
  - Elements by text label
  - Relative positions ("first result", "top menu")

#### FR-4.2.4: Time-Specific Element Finding
- When finding time slots, dates, or similar options:
  - Find the option CLOSEST to the requested value
  - If exact match unavailable, prefer next value after requested
  - Include the specific value in the search (e.g., "time slot closest to 7:00 PM")

### 4.3 Action Execution

#### FR-4.3.1: Supported Actions

| Action | Description | Parameters |
|--------|-------------|------------|
| `activate_app` | Launch/focus application | `app_name: string` |
| `quit_app` | Close application | `app_name: string` |
| `open_url` | Open URL in browser | `url: string, browser?: string` |
| `click` | Click at coordinates | `x: number, y: number` |
| `click_element` | Click element by description | `description: string` |
| `double_click` | Double-click at coordinates | `x: number, y: number` |
| `right_click` | Right-click at coordinates | `x: number, y: number` |
| `type_text` | Type text at cursor | `text: string` |
| `press_key` | Press key/shortcut | `keys: string[]` |
| `scroll` | Scroll in direction | `direction: up|down|left|right, amount?: number` |
| `drag` | Drag from A to B | `from: {x,y}, to: {x,y}` |
| `wait` | Wait for duration | `duration: number` (seconds) |
| `screenshot` | Capture current screen | (none) |

#### FR-4.3.2: Coordinate Scaling (CRITICAL)

**Problem**: Claude sees screenshots at native resolution (e.g., 3024×1964), but PyAutoGUI operates in logical screen space (e.g., 1512×982).

**Solution**:
```
scale_factor = screenshot_width / logical_width
click_x = claude_x / scale_factor
click_y = claude_y / scale_factor
```

**Requirements**:
- Detect scale factor dynamically (don't hardcode 2.0)
- Cache scale factor (don't recalculate every click)
- Log both original and scaled coordinates for debugging
- Validate scaled coordinates are within screen bounds

#### FR-4.3.3: Action Execution Order

For reliable execution:
1. **AppleScript first**: Use for app control, URL opening, menu access
2. **Accessibility API second**: Use for UI element interaction when available
3. **PyAutoGUI last**: Use for pixel-based clicking when no other option

#### FR-4.3.4: Action Delays
- Default 1.0 second between actions
- Configurable per-action delays
- Longer delays after URL navigation (wait for page load)

### 4.4 Agentic Loop

#### FR-4.4.1: Loop Structure
```
for iteration in range(max_iterations):
    1. OBSERVE: Take screenshot, describe state
    2. THINK: Given goal + history, decide next action
    3. CHECK: Is goal achieved? If yes, return success
    4. ACT: Execute the planned action
    5. WAIT: Allow UI to update
```

#### FR-4.4.2: History Management
- Maintain history of observations and actions
- Include last 10 entries in planning prompt
- Format clearly distinguishes observations vs actions

#### FR-4.4.3: Completion Detection
Goal is complete when:
- Requested information is visible on screen
- Requested action has been performed and verified
- NOT just when a page loads (must verify content)

#### FR-4.4.4: Loop Limits
- Default max iterations: 35
- Configurable per-task
- Return partial results on timeout

#### FR-4.4.5: Error Recovery
- If same action fails twice, try alternative approach
- If stuck for 3 iterations, attempt:
  - Refresh page
  - Click elsewhere to reset focus
  - Use keyboard navigation instead of clicks
- Log all failures for debugging

### 4.5 Messaging Integration

#### FR-4.5.1: Command Reception
- Receive commands from any Moltbot channel
- Support text commands: "automate: [command]" or "/auto [command]"
- Support direct messages to automation persona

#### FR-4.5.2: Progress Updates
- Send periodic updates during long-running tasks
- Include current step and total estimated steps
- Send screenshots at key milestones (optional, configurable)

#### FR-4.5.3: Result Reporting
- Send completion message with success/failure status
- Include summary of actions taken
- Attach final screenshot (optional)

---

## 5. Non-Functional Requirements

### 5.1 Performance

| Metric | Target |
|--------|--------|
| Simple task execution | < 5 seconds |
| Complex task (10 steps) | < 60 seconds |
| Screenshot capture | < 500ms |
| Vision API latency | < 5 seconds per call |
| Coordinate scaling | < 1ms |

### 5.2 Reliability

- Action success rate: > 95% for well-defined tasks
- Coordinate accuracy: Within 5 pixels of target
- No data loss on crash (session state persisted)
- Graceful degradation if API unavailable

### 5.3 Security

- Never automate password entry
- Never access sensitive system preferences
- Blocked apps list (configurable):
  - System Preferences / System Settings
  - Keychain Access
  - Security & Privacy panes
- Require confirmation for:
  - File deletion
  - System configuration changes
  - Financial transactions (optional)

### 5.4 Observability

- Structured logging (JSON format)
- Log levels: DEBUG, INFO, WARNING, ERROR
- Log rotation (10MB max, 5 backups)
- Metrics:
  - Actions executed (success/failure)
  - API calls (latency, errors)
  - Task completion rate

### 5.5 Configuration

All settings configurable via:
1. Environment variables (MACOS_AUTO_* prefix)
2. Config file (~/.config/macos-automation/config.yaml)
3. CLI flags

Key settings:
```yaml
automation:
  action_delay: 1.0
  max_iterations: 35
  screenshot_quality: 85

vision:
  provider: anthropic  # or ollama
  model: claude-sonnet-4-20250514
  max_image_size: 4500000  # bytes

security:
  blocked_apps:
    - "System Preferences"
    - "Keychain Access"
  require_confirmation: false

logging:
  level: INFO
  file: ~/.local/share/macos-automation/logs/agent.log
```

---

## 6. Technical Specifications

### 6.1 Technology Stack

| Layer | Technology | Rationale |
|-------|------------|-----------|
| Runtime | Node.js 22+ | Moltbot compatibility |
| Language | TypeScript (ESM) | Type safety, Moltbot compat |
| Package Manager | pnpm | Moltbot workspace |
| Vision API | Anthropic Claude | Best vision accuracy |
| Text API | Anthropic Claude | Consistency |
| Screen Capture | screencapture (macOS) or PyAutoGUI | Native performance |
| Mouse/Keyboard | PyAutoGUI via child process | Reliable input simulation |
| App Control | AppleScript via osascript | Native macOS integration |
| Accessibility | pyobjc (optional) | Advanced UI inspection |

### 6.2 Directory Structure (as Moltbot Skill)

```
moltbot/
├── skills/
│   └── macos-automation/
│       ├── package.json
│       ├── tsconfig.json
│       ├── src/
│       │   ├── index.ts              # Skill entry point
│       │   ├── skill.ts              # Skill definition
│       │   ├── tools/
│       │   │   ├── automate.ts       # Main automation tool
│       │   │   ├── screenshot.ts     # Screenshot tool
│       │   │   └── click.ts          # Click tool
│       │   ├── agent/
│       │   │   ├── loop.ts           # Agentic loop
│       │   │   ├── planner.ts        # Action planning
│       │   │   └── observer.ts       # Screen observation
│       │   ├── actions/
│       │   │   ├── registry.ts       # Action registry
│       │   │   ├── applescript.ts    # AppleScript actions
│       │   │   ├── click.ts          # Click action
│       │   │   ├── type.ts           # Type action
│       │   │   └── keyboard.ts       # Keyboard actions
│       │   ├── vision/
│       │   │   ├── capture.ts        # Screen capture
│       │   │   ├── analyze.ts        # Vision analysis
│       │   │   └── coordinates.ts    # Coordinate scaling
│       │   ├── parser/
│       │   │   ├── intent.ts         # Intent parsing
│       │   │   └── prompts.ts        # System prompts
│       │   └── config/
│       │       └── schema.ts         # Config schema
│       ├── scripts/
│       │   └── python/
│       │       ├── click.py          # PyAutoGUI click
│       │       ├── type.py           # PyAutoGUI type
│       │       └── screenshot.py     # PyAutoGUI screenshot
│       └── test/
│           ├── actions.test.ts
│           ├── coordinates.test.ts
│           └── integration.test.ts
```

### 6.3 API Contracts

#### 6.3.1 Automation Tool Interface
```typescript
interface AutomationToolInput {
  command: string;           // Natural language command
  max_iterations?: number;   // Override default
  screenshot_updates?: boolean; // Send screenshots during execution
}

interface AutomationToolOutput {
  success: boolean;
  message: string;
  steps_taken: ActionResult[];
  iterations: number;
  final_screenshot?: string; // base64
  error?: string;
}
```

#### 6.3.2 Action Interface
```typescript
interface Action {
  type: string;
  params: Record<string, unknown>;
  validate(): Promise<boolean>;
  execute(): Promise<ActionResult>;
}

interface ActionResult {
  success: boolean;
  action: string;
  params: Record<string, unknown>;
  output?: string;
  error?: string;
  metadata?: {
    original_x?: number;
    original_y?: number;
    scaled_x?: number;
    scaled_y?: number;
    scale_factor?: number;
    duration_ms?: number;
  };
}
```

#### 6.3.3 Coordinate System
```typescript
interface Coordinates {
  // Screenshot pixel space (what Claude sees)
  screenshot_x: number;
  screenshot_y: number;

  // Logical screen space (what PyAutoGUI uses)
  logical_x: number;
  logical_y: number;

  // Bounding box (optional)
  bbox?: {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  };
}

interface ScreenInfo {
  logical_width: number;   // e.g., 1512
  logical_height: number;  // e.g., 982
  screenshot_width: number;  // e.g., 3024
  screenshot_height: number; // e.g., 1964
  scale_factor: number;      // e.g., 2.0
}
```

---

## 7. Known Issues & Solutions

### 7.1 Bug #001: Retina Coordinate Mismatch
**Problem**: Clicks land in wrong location on Retina displays
**Solution**: Scale coordinates by dividing by scale_factor before clicking
**Test**: Verify click lands within 5 pixels of target

### 7.2 Bug #001b: Double Scaling
**Problem**: Observer converts to logical space, then ClickAction scales again
**Solution**: Observer must return screenshot-space coordinates; only ClickAction scales
**Test**: Unit test coordinate pipeline end-to-end

### 7.3 Bug #002: Non-Specific Element Selection
**Problem**: "Click a time slot" clicks random slot instead of requested time
**Solution**: Always include specific value in element description (e.g., "time slot closest to 7:00 PM")
**Test**: Verify correct time slot selected in booking flow

---

## 8. Testing Strategy

### 8.1 Unit Tests
- Coordinate scaling calculations
- Action parameter validation
- Intent parsing for various inputs
- URL inference patterns

### 8.2 Integration Tests
- Screenshot capture → Vision API → Coordinate extraction
- Action execution (mocked screen)
- Agentic loop with mock observer

### 8.3 End-to-End Tests (Manual)
- Simple: "Open Safari and go to youtube.com"
- Medium: "Search YouTube for cooking videos"
- Complex: "Book a table for 2 at Joey's for 7pm"

### 8.4 Regression Tests
- Coordinate scaling on Retina displays
- Time-specific element selection
- App name normalization

---

## 9. Implementation Phases

### Phase 1: Foundation (Core Infrastructure)
- [ ] Fork Moltbot, create skill skeleton
- [ ] Implement screen capture (TypeScript + Python bridge)
- [ ] Implement coordinate scaling with tests
- [ ] Implement basic actions (click, type, key press)
- [ ] Add AppleScript action executor

### Phase 2: Vision Integration
- [ ] Integrate Claude vision API
- [ ] Implement screen description
- [ ] Implement element location with coordinate parsing
- [ ] Add time-specific element finding

### Phase 3: Agentic Loop
- [ ] Implement intent parser
- [ ] Implement agentic loop state machine
- [ ] Add history management
- [ ] Add completion detection
- [ ] Add error recovery

### Phase 4: Moltbot Integration
- [ ] Register as Moltbot skill
- [ ] Add automation tool definition
- [ ] Test via CLI and Telegram
- [ ] Add progress updates

### Phase 5: Polish & Testing
- [ ] Comprehensive test suite
- [ ] Documentation
- [ ] Performance optimization
- [ ] Security audit

---

## 10. Success Criteria

### 10.1 Minimum Viable Product (MVP)
- [ ] Execute simple commands via CLI ("Open Safari", "Go to google.com")
- [ ] Execute complex commands with vision ("Click the first search result")
- [ ] Correct coordinate handling on Retina displays
- [ ] Basic error handling and logging

### 10.2 Full Release
- [ ] All MVP criteria
- [ ] Multi-channel access (Telegram, Discord, CLI)
- [ ] Progress updates during execution
- [ ] Configurable security policies
- [ ] 90%+ test coverage on core modules

---

## 11. Appendix

### A. Example Prompts

#### Intent Parser System Prompt
```
You are a macOS automation intent parser. Convert user commands into JSON actions.

Output Schema:
{
  "steps": [{"action": "ACTION_TYPE", "params": {...}}],
  "requires_observation": true/false
}

Available Actions:
- activate_app: Launch/focus app {app_name}
- open_url: Open URL {url, browser?}
- quit_app: Close app {app_name}
- type_text: Type text {text}
- press_key: Press keys {keys[]}
- click_element: Click by description {description}
- click: Click coordinates {x, y}

Rules:
1. Infer URLs: "youtube" → "https://www.youtube.com"
2. Normalize app names: "chrome" → "Google Chrome"
3. Set requires_observation: true for vision-dependent tasks
```

#### Agentic Planner System Prompt
```
You are an automation agent controlling a macOS computer.

Response Format (JSON only):
- Goal achieved: {"complete": true, "reasoning": "..."}
- More actions: {"action": "ACTION_TYPE", "params": {...}, "reasoning": "..."}

Critical Rules:
1. LOOK before acting - read the observation
2. For forms: click field → clear → type → submit
3. For time slots: specify exact time (e.g., "time slot closest to 7:00 PM")
4. If stuck, try alternative approach
```

### B. Configuration Schema
```yaml
# ~/.config/macos-automation/config.yaml
version: 1

automation:
  action_delay: 1.0        # seconds between actions
  max_iterations: 35       # max agentic loop iterations
  screenshot_quality: 85   # JPEG quality (1-100)

vision:
  provider: anthropic
  model: claude-sonnet-4-20250514
  max_image_size: 4500000
  coordinate_format: normalized  # normalized (0-1000) or pixels

actions:
  applescript_timeout: 30  # seconds
  pyautogui_failsafe: true

security:
  blocked_apps:
    - "System Preferences"
    - "System Settings"
    - "Keychain Access"
  require_confirmation: false
  allowed_domains: []  # empty = all allowed

logging:
  level: INFO
  format: json
  file: ~/.local/share/macos-automation/logs/agent.log
  max_size: 10485760  # 10MB
  backup_count: 5
```

### C. Error Codes
| Code | Meaning |
|------|---------|
| E001 | Screenshot capture failed |
| E002 | Vision API error |
| E003 | Coordinate out of bounds |
| E004 | Action execution failed |
| E005 | Max iterations reached |
| E006 | Blocked app access attempted |
| E007 | AppleScript execution failed |
| E008 | Element not found |
| E009 | Invalid action parameters |
| E010 | Configuration error |

---

**Document End**
