# Local Coding Consultant Optimization Roadmap

## Context & Objective
This plan outlines a prioritized roadmap for optimizing a **local, read-only coding consultant** designed to serve as an AI pair programmer with a human-in-the-loop architecture. The system operates entirely offline using small local models (e.g., Qwen 7B/9B) constrained by limited VRAM.

The primary goal is to enhance the consultant's ability to assist with:
- Code snippets for new features
- Refactoring and architectural decisions  
- Planning and documentation drafting
- Debugging and root cause analysis

**Key Constraints:** No cloud handoffs, strict VRAM limits, read-only file access (no direct disk modification).

---

## Implementation Priorities

### **Tier 1: Critical Infrastructure & VRAM Optimization** ⚡ *Immediate*

| Feature | Impact | Priority | Effort | Description |
|---------|------|----------|--------|-------------|
| Context Window Hygiene & Token Budgeting | High | Critical | Low | Enforce strict output limits ("concise diffs") and compact system prompts to prevent OOM errors. Essential for 7B/9B models on limited VRAM. |
| Aggressive Context Caching (KV Cache) | High | Critical | Medium | Implement `slot_save`/`slot_restore` or file-based caching in llama.cpp. Prevents re-processing the entire codebase context every turn, drastically reducing latency and peak memory usage. |
| Token & VRAM Telemetry (`/stats`) | Low | High | Low | Add a CLI footer showing current token count, KV cache usage, gen speed, and compression status. Vital for debugging why the model is crashing, slowing down, or hitting memory limits during long sessions. |

---

### **Tier 2: Workflow Enhancements (Human-in-the-Loop)** 🔄 *High Value*

| Feature | Impact | Priority | Effort | Description |
|---------|---------|-------|--------|-------------|
| Clipboard & Direct Patch Handoff | High | Done | Low | GUI provides one-click copy buttons on all code blocks. CLI fallback: `/copy` macro (deprecated). |
| Diagnostic Root-Cause Triage (`/diagnose`) | Medium | Medium | Medium | Optimize stack trace analysis by feeding only relevant file paths/symbols rather than whole files. Focuses the 9B model on logic bugs, not syntax. |

---

### **Tier 3: Advanced Capabilities & Tooling** 🔍 *Expansion*

| Feature | Impact | Priority | Effort | Description |
|---------|---------|----------|--------|-------------|
| Semantic / AST Search Tool | High | Medium | High | Replace guessing file paths with a local embedding model (e.g., `nomic-embed-text`) or tree-sitter. Allows natural language queries like "find auth logic" instead of manual path navigation. |
| Test Scenario & Edge-Case Matrix (`/test-matrix`) | Medium | Medium | Low | Have the model brainstorm boundary conditions and input values rather than writing full test suites. You (or a separate script) generate the actual code based on this matrix. |

---

### **Tier 4: UX & Automation** 🎨 *Polish*

| Feature                                              | Impact | Priority | Effort | Description |
|------------------------------------------------------|-----|----------|-----|-------------|
| Auto-Routing (Smart Mode) | Medium | Low-Medium | High | Heuristic router that sends "diagnose/architect" queries to the reasoning model and syntax/code questions to the fast primary model automatically. |
| Diagram Generation (`/export diagram`) | Low | Low | Medium | Detect Mermaid blocks in output and render them locally as SVGs using a CLI wrapper, making architectural explanations visual. |
| Session Persistence (`/save`, `/load`) ⚠️ Deprecated | Low | Very-Low | Low | Serialize conversation state to JSON for resuming sessions days later. **Note:** Largely superseded by `/prepare` macro workflow which loads fresh context before each session (more reliable, avoids stale data). Keep only if users need long-term session recovery without re-preparing files. |

---

### **Tier 5: Advanced Workflow Features (Requires Further Analysis)** 🔬 *Deferred*

| Feature | Impact | Priority | Effort | Description & Requirements |
|---------|--------|----------|--------|----------------------------|
| **Interactive Step-by-Step Execution Plans (`/plan`)** | Medium | Low-Medium | High | Generate structured checklists before code generation. Requires: (1) Clear distinction between human-in-the-loop vs autonomous workflows, (2) Approval gate mechanism for multi-file changes, (3) Integration with Clipboard/Patch handoff to enable incremental implementation. **Status**: Deferred — requires requirements specification and use-case analysis before implementation. |
| Automatic Context Compression | High | Critical | Medium-High | When conversation approaches 80% of context window limit, use fast primary model to summarize oldest turns into dense summaries OR intelligently drop older conversation segments while preserving key tool results and recent dialogue. Keeps long sessions viable without OOM crashes or hallucinations from truncated context. |

---

## Implementation Strategy Recommendation

1. Implement Tier 1 (Caching + Token Limits). This is non-negotiable for your hardware setup.
2. Build the `/plan` and Clipboard handoff features to establish a reliable human-in-the-loop workflow.
3. Integrate Semantic Search and Auto-Routing to reduce manual context fetching.

---

## Key Design Principles

- **Human-in-the-Middle:** All major changes require user approval before implementation
- **Read-Only Safety:** No direct file modifications; all outputs are diffs, patches, or clipboard-ready code
- **VRAM Consciousness:** Every feature must account for VRAM constraints and small model limitations
- **Local First:** Zero cloud dependencies; all processing happens locally on your machine
