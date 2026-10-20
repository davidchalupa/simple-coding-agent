# Supplementary Context & Technical Details for Implementation Planning

This document contains detailed technical context, implementation notes, and architectural considerations extracted from `consultant_plans_raw.txt` to support the development of individual features. This information should be preserved as reference material during feature implementation.

---

## **1. Clipboard & Direct Patch Handoff - Detailed Context**

### Implementation Notes:
#### `/diff` Command:
- Model must output changes as standard `git diff -u` format (unified diff)
- Should strip all markdown formatting from code blocks before generating diffs
- Example expected output structure:
  ```diff
  --- a/src/auth_service.py
  +++ b/src/auth_service.py
  @@ -1,5 +1,8 @@
    def authenticate(user_id):
      # existing code
  
  +def validate_oidc_token(token):
  +    pass
  ```

#### `/apply` Command:
- Takes last unified diff block from conversation history
- Pipes directly to `git apply --check` first (dry-run)
- If clean, runs `git apply <diff>` automatically
- Provides immediate feedback on patch success/failure without corrupting git tree

#### `/copy` Command:
- Strips all markdown formatting (` ```python `, etc.) from latest code block
- Pushes raw content to system clipboard using platform-specific tools:
  - macOS: `pbcopy`
  - Linux: `xclip` or `wl-copy` (Wayland)
  - Windows: `clip.exe`

### Technical Considerations:
- Need robust diff parsing logic to handle edge cases (multi-file changes, whitespace issues)
- Should maintain a history buffer of last N diffs for `/apply` command
- Clipboard operations should be non-blocking and provide visual feedback

---

## **2. Diagnostic Root-Cause Triage (`/diagnose`) - Detailed Context**

### Implementation Notes:
#### Target Models:
- Qwen 3.5 9B (reasoning-tuned model)
- These excel at stack trace analysis but have limitations with multi-file state tracking

#### Workflow:
1. User pastes test failure/panic trace into CLI
2. Consultant uses `read_symbol` and `read_file` to trace call stack backward through repository
3. Explains why state became invalid (root cause)
4. Proposes 2-3 possible fixes for user selection

#### Key Limitation:
- Small models struggle with deep architectural logic bugs spanning multiple abstraction layers
- Cannot hold complex, multi-file execution paths in "head" simultaneously
- May hallucinate variable states or call relationships across distant files

### Technical Considerations:
- Should prioritize feeding only relevant file contents (not entire files) to context window
- Can use `read_symbol` for function signatures and local scope analysis
- May benefit from showing recent commits/changes near the crash site
- Output should include confidence indicators or "I'm not sure about X" statements

---

## **3. Test Scenario & Edge-Case Matrix (`/test-matrix`) - Detailed Context**

### Implementation Notes:
#### Target Models:
- 7B models struggle with generating 200-line syntactically perfect test suites autonomously
- Excel at brainstorming boundary conditions and input combinations

#### Workflow:
1. User points to function using `@file:path/to/file.py:function_name` syntax
2. Requests Test Matrix generation
3. Model outputs structured list of:
   - Input values (normal, edge cases, invalid inputs)
   - Expected behaviors for each case
   - Potential side effects or state changes
4. User writes actual test cases OR requests individual micro-tests one at a time

### Technical Considerations:
- Should support both function-level and class-level testing scenarios
- Can integrate with existing testing frameworks (pytest, unittest) later
- Output format should be easily parseable for automated test generation scripts
- May want to include coverage metrics or priority ratings for each test case

---

## **4. Key VRAM Optimizations - Detailed Context**

### Implementation Notes:

#### Context Window Hygiene:
- Keep system prompts compact and minimal
- Avoid heavy multi-shot tool examples in initial prompt
- Inject few-shot examples dynamically ONLY when a tool call fails (on-demand)
- This reduces baseline token usage significantly

#### Aggressive Context Caching:
- **Technology:** llama.cpp / llama-cpp-python with KV cache management
- **Mechanism:** `slot_save`/`slot_restore` or `prompt_cache_file`
- **Benefit:** Reuses KV cache across multiple consultative questions in same session
- **Impact:** Without this, entire codebase context is reprocessed every turn (expensive)

#### Explicit Token Budgeting:
- System prompt instruction: "Keep explanations brief. Present code changes as concise, localized diffs rather than reprinting entire files."
- Smaller output token counts = faster generation + less VRAM pressure
- Prevents model from going into degradation zone at high token counts

### Technical Considerations:
- Monitor KV cache usage via `/stats` command
- Set hard limits on context window size based on available VRAM
- Implement automatic fallback to smaller models when approaching memory limits

---

## **5. Multi-Agent Debate & Local Verification - Detailed Context**

### Implementation Notes:
#### Problem Addressed:
- 9B reasoning model has right intuition but gets tangled in its own explanation
- Prone to superficial hallucinations on complex problems

#### Solution Architecture:
1. User enters `/diagnose` mode with stack trace
2. 9B reasoning model proposes fix/explanation
3. CLI silently routes proposal back to fast 7B coder model with hidden system prompt:
   ```
   "You are a senior reviewer. The junior developer just proposed this fix for the following 
    stack trace. Identify any edge cases or side effects this fix will cause. If it looks perfect, say 'LGTM'."
   ```
4. User sees both outputs sequentially

### Technical Considerations:
- Requires two model instances running simultaneously (7B + 9B)
- Hidden system prompt must be carefully crafted to avoid leaking context to user
- Should timestamp and label each agent's output clearly for attribution
- Can extend this pattern to other workflows beyond `/diagnose`

---

## **6. Execution Path Logging - Detailed Context**

### Implementation Notes:
#### Problem Addressed:
- Static code context isn't enough; models need runtime state values
- Small models struggle to mentally simulate state transitions across function calls

#### Enhancement Architecture:
1. User hits bug, uses `/trace [function_name]` command
2. Consultant (or Python script) temporarily wraps that function in basic execution logger:
   ```python
   def traced_function(*args, **kwargs):
       log = {"function": "original_func", 
              "args": args, 
              "kwargs": kwargs}
       result = original_function(*args, **kwargs)
       return result
   ```
3. User runs test → generates structured JSON log of exact variables at crash point
4. Feeds JSON log into `/diagnose` instead of just code

### Technical Considerations:
- Logging should be minimal and non-intrusive (don't slow down execution)
- Can use decorators or context managers for easy wrapping/unwrapping
- Log format must be deterministic and parseable by model
- Should support both synchronous and asynchronous function tracing
- May need to handle exceptions gracefully during logging setup

---

## **7. Codebase Navigation & Context - Detailed Context**

### Implementation Notes:

#### Semantic / AST Search Tool:
- Current limitation: Agent relies on `read_symbol`/`read_file`, requires knowing/guessing file paths
- Solution: Add tool like `search_code(intent="user authentication")`
- Backend options:
  - Lightweight embedding model (e.g., `nomic-embed-text`) via Ollama/llama.cpp
  - AST parser like tree-sitter for structural understanding
- Benefit: Find code conceptually rather than guessing paths

#### Git Integration Tools:
- Give read-only agent access to git commands
- Example tools:
  - `read_git_diff(branch="main")` → shows recent changes
  - `read_commit_history()` → answers "What did I break in my last commit?"
- Turns consultant into powerful local PR reviewer

#### Interactive File Picker (CLI-side):
- Before sending prompt, allow user to type `@` (similar to Cursor IDE)
- Opens fzf or questionary fuzzy finder in CLI
- Immediately injects selected file's contents into context window
- Benefit: Model doesn't waste a turn calling tool to fetch files

### Technical Considerations:
- Semantic search requires embedding model setup and vector database (can be lightweight SQLite-based)
- Git tools should handle edge cases like uncommitted changes, merge conflicts
- File picker needs proper keyboard navigation and escape handling in CLI environment

---

## **8. Output & Deliverables - Detailed Context**

### Implementation Notes:

#### `/export patch` Command:
- Triggered when model suggests code change in markdown block
- Python script intercepts last message from conversation history
- Formats diff into standard `.patch` file
- Saves to directory (e.g., `./patches/`) for easy `git apply` later

#### Diagram Generation:
- Consultants explain architectures using Mermaid markdown blocks
- CLI automatically detects mermaid syntax in output
- Renders locally as SVG or HTML using:
  - Mermaid CLI tool, OR
  - Python wrapper (e.g., `mermaid-cli`, `diagrams`)
- Saves to working directory for immediate viewing

### Technical Considerations:
- Patch export should validate diff format before saving
- Diagram rendering can be optional/background process
- Should support multiple diagram types beyond Mermaid if needed later
- Can integrate with existing documentation tools (e.g., mkdocs, docusaurus)

---

## **9. Session & CLI Experience - Detailed Context**

### Implementation Notes:

#### Session Persistence (`/save` and `/load`):
- Current issue: `ConsultantState.messages` array wiped on exit or `/clear`
- Solution: Serialize state to local file (~/.consultant_sessions/)
- Format options: JSON (simple) or SQLite (structured queries later)
- Benefit: Resume deep debugging sessions days later without losing context

#### Auto-Routing (Smart Mode):
- Current issue: User must manually type `/mode think` for reasoning model
- Solution: Lightweight heuristic router based on prompt keywords
- Routing logic examples:
  - Contains "why", "diagnose", "architect", or "bug" → route to reasoning model
  - Contains "show me the syntax for X", "how do I write..." → route to fast primary model
- Can be extended with more sophisticated NLP later

#### Token & VRAM Telemetry (`/stats`):
- Local GGUF users care deeply about hardware limits
- Display metrics in dynamic CLI footer:
  - Current context window token count
  - KV cache usage (if applicable)
  - Tokens/second generation speed of last turn
- Helps diagnose performance issues proactively

### Technical Considerations:
- Session files should be encrypted or at least have restricted permissions
- Auto-routing heuristics need to avoid false positives/negatives initially
- Telemetry data can be used for future model selection algorithms
- Should handle session corruption gracefully (auto-recovery)

---

## **10. Advanced Guardrails & Memory - Detailed Context - Deferred (Tier 5)** ⚠️

### Implementation Notes:

#### Automatic Context Compression

**Decision Rationale:**
- Originally planned as Tier 1 critical feature, but analysis suggests deferral is prudent until practical need demonstrated
- Current testing environment at ~58% context usage shows no immediate pressure for compression
- Key insight from observation: >80% token counts typically result in model hallucinations/logic errors rather than pure context grounding failures — compression alone doesn't solve this fundamental limitation

**Deferred Implementation Strategy:**
1. **Wait for real-world evidence**: Monitor autonomous coding agent during long sessions with self-corrections to observe when 80%+ actually becomes problematic
2. **Focus on reliability first**: Prioritize improving model grounding and reducing hallucinations at high token counts before adding compression complexity
3. **Add as opt-in feature later** only when:
   - Users report OOM errors or severe degradation specifically tied to context length (not just general quality issues)
   - Concrete examples exist showing what breaks and why
   - Autonomous agent demonstrates clear need in practice

**Future Implementation Notes (when activated):**
- Trigger condition: Context hits 80% of window limit (configurable threshold)
- Action: User confirmation required before compressing old tool results + chat history into single dense blocks
- Compression approach: Summarize dialogue turns; for tool results preserve metadata (file paths, function names, line ranges) while compressing actual content to key excerpts (~20-50 lines max per file)
- Use fast primary model (7B coder) for compression tasks — not reasoning model
- Add "compressed" flag so model knows it can re-query via tools when needed

**Technical Considerations:**
- Background threading must not block main inference loop
- Summary quality depends on model choice (use fastest available for this task)
- Should maintain summary history to avoid redundant summarization
- Can implement progressive compression (summarize more aggressively as session grows)
- May need user confirmation before discarding original conversation turns

**Alternative Optimizations to Prioritize:**
- Keep system prompts compact and minimal (already implemented)
- Enforce concise diffs in output via instructions rather than full file reprints
- Improve model grounding mechanisms to reduce hallucinations at high token counts

---

## **11. Interactive Step-by-Step Execution Plans (`/plan`) - Deferred (Tier 5)** ⚠️

### Current Status
- **Deferred from Tier 2 → Tier 5**: Limited value for read-only human-in-the-loop consultant without autonomous execution capability
- Requires requirements specification before implementation

---

### Critical Design Decisions Needed:

| Decision | Options to Evaluate |
|----------|---------------------|
| **Trigger Model** | Mandatory for complex tasks vs. optional on-demand? Auto-detect complexity via prompt analysis? |
| **Approval Mechanism** | Interactive CLI prompts per step / Batch approval with edits / Integrate into Think Mode reasoning output instead of separate macro |
| **State Management** | Track approved plans across turns? Resume after interruption? Clear state on `/clear` or mode switch? |
| **Output Format Integration** | Align with Clipboard/Patch handoff: unified diff (`git diff -u`) / Markdown code blocks for clipboard / Both formats |

---

### Key Requirements (if implemented):
- Must work with `read_symbol` and `read_file` tools to gather context before plan generation
- Output must be parseable (Markdown list or JSON) but human-readable for review
- Plan should include file locations, code skeletons, and estimated complexity per step
- Human-in-the-Middle flow: Model generates → User approves/tweaks → Step-by-step implementation begins

---

### Recommended Next Steps:
1. Define use cases where `/plan` adds value over direct Think Mode reasoning
2. Specify CLI interaction model for plan approval (interactive vs batch)
3. Determine integration points with Clipboard/Patch handoff features
4. Consider if this belongs to autonomous agent variant rather than read-only consultant
5. 
---

## **12. Model Selection & Architecture Notes**

### Current Stack:
- Primary fast model: 7B coder (syntax, quick tasks)
- Reasoning model: Qwen 3.5 9B (diagnostics, complex reasoning)
- Hardware constraint: VRAM total

### Architectural Insights from Document:
- **Small models excel at:** Isolated stack traces, boundary condition brainstorming, syntax questions
- **Small models struggle with:** Deep architectural logic bugs spanning multiple abstraction layers, holding multi-file execution paths in memory simultaneously
- **Key limitation:** 9B model fundamentally limited by parameter count when trying to hold complex state transitions "in head" at once

### Strategic Implications:
- Don't try to prompt small models harder for capabilities they lack architecturally
- Focus on giving them better tools (search, logging, step-by-step planning) rather than expecting more reasoning power
- Consider multi-agent patterns where fast model verifies/simplifies slow model's output
- Accept that some problems may require human intervention or external resources

---

## **13. Implementation Dependencies & Prerequisites**

### Required External Tools:
- `git` (for diff/apply operations)
- Platform-specific clipboard utilities (`pbcopy`, `xclip`, `wl-copy`)
- Optional but recommended: Mermaid CLI for diagram rendering
- Optional: Lightweight embedding model (nomic-embed-text via Ollama/llama.cpp)

### Required Python Libraries:
- `gitpython` or subprocess calls to git commands
- Platform detection utilities for clipboard operations
- JSON serialization libraries (built-in)
- SQLite3 driver if choosing database storage over JSON files

### Infrastructure Requirements:
- llama.cpp / llama-cpp-python with KV cache support enabled
- Ability to run multiple model instances simultaneously (for multi-agent debate)
- Background threading capability for context compression tasks

---

## **14. Testing & Validation Considerations**

### Feature-Specific Test Cases:

#### For `/plan`:
- Verify plan includes all necessary files and steps
- Check that approved plans can be executed without errors
- Validate human-in-the-loop approval flow works correctly

#### For Clipboard/Patch commands:
- Test cross-platform clipboard functionality (macOS, Linux, Windows)
- Verify patch application succeeds with clean diffs
- Ensure failed patches don't corrupt git state

#### For `/diagnose`:
- Create test cases covering various bug types (syntax errors, logic bugs, race conditions)
- Validate that model correctly identifies root causes in simple vs. complex scenarios
- Test multi-file dependency tracing accuracy

#### For VRAM optimizations:
- Monitor memory usage under load with large codebases
- Verify KV cache reduces reprocessing overhead
- Confirm token budgeting prevents OOM errors during long sessions

---

## **15. Future Enhancement Opportunities**

### Potential Additions Not Covered in Current Plan:
- Integration with IDE plugins for richer context (vscode, neovim)
- Voice input/output capabilities for hands-free operation
- Plugin system for custom tools and workflows
- Collaborative mode where multiple users can contribute to same session
- Automated documentation generation from conversation history
- Learning mechanism that adapts to user's coding style over time

### Research Directions:
- Optimal context window sizes for different model architectures
- Best practices for KV cache management in long-running sessions
- Techniques for reducing hallucinations in small language models
- Methods for improving multi-file state tracking without increasing VRAM usage significantly

---

*This supplementary document captures all technical details, implementation notes, and architectural considerations from the original raw ideas. Use this as reference during feature development to ensure alignment with intended behavior and constraints.*
