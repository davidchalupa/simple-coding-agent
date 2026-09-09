import ast
import os
import shutil
import json
import re

from common.tool_definitions import extract_code_blocks


def analyze_file_metrics(filepath):
    """
    Parses the file using AST to extract the structure and compute
    heuristics to detect a 'God Class' anomaly.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content)

        classes = []
        top_level_functions = 0
        total_methods = 0

        structure_lines = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef)]
                classes.append({
                    "name": node.name,
                    "method_count": len(methods)
                })
                total_methods += len(methods)

                method_str = ", ".join(methods) if methods else "No methods"
                structure_lines.append(f"- class {node.name}: {method_str}")

            elif isinstance(node, ast.FunctionDef):
                top_level_functions += 1
                structure_lines.append(f"- top-level function {node.name}()")

        is_god_class = False
        god_class_name = ""

        if classes:
            largest_class = max(classes, key=lambda x: x["method_count"])
            total_blocks = total_methods + top_level_functions
            if largest_class["method_count"] >= 12 and total_blocks > 0:
                share = largest_class["method_count"] / total_blocks
                if share >= 0.60:
                    is_god_class = True
                    god_class_name = largest_class["name"]

        return {
            "structure_map": "\n".join(structure_lines) if structure_lines else "No major structural components.",
            "is_god_class": is_god_class,
            "god_class_name": god_class_name
        }

    except SyntaxError as e:
        return {"error": f"Syntax Error: Cannot parse structure. ({e})"}
    except Exception as e:
        return {"error": f"Error reading file: {e}"}


def build_split_prompt(filepath, target_dir, execute_mode=False):
    """
    Dynamically generates a framework-agnostic prompt.
    """
    filename = os.path.basename(filepath)
    analysis = analyze_file_metrics(filepath)

    if "error" in analysis:
        return f"System Error: {analysis['error']}"

    structure_map = analysis["structure_map"]

    if analysis["is_god_class"]:
        context_framing = (
            f"The file `{filename}` contains an anti-pattern: a single giant 'God Class' "
            f"(`{analysis['god_class_name']}`) which wraps almost all logic."
        )
        architectural_guidance = (
            "- Isolate business logic, data calculations, or heavy processing routines away from core orchestration/UI layers.\n"
            "- Group highly related operations into clean specialized Services, Managers, or specialized sub-components.\n"
            "- Extract secondary presentation blocks, operational workflows, or optional attributes into explicit Extension classes or Mixins."
        )
    else:
        context_framing = (
            f"The file `{filename}` is a generic structural monolith with high density and "
            "scattered distinct responsibilities."
        )
        architectural_guidance = (
            "- Group interconnected independent functions and definitions into high-cohesion standalone module domains.\n"
            "- Separate utility primitives, driver logic, or presentation targets into explicit horizontal tiers."
        )

    if execute_mode:
        prompt = f"""You are a senior Software Architect. Your task is to design a conservative refactoring split blueprint.

[Context]
File Target: `{filename}`

IMPORTANT:
- `{filename}` is the ORIGINAL ROOT FILE.
- The original root filename is FIXED and MUST NOT be renamed.
- If any functions or methods remain in the root file, they MUST be assigned to the EXACT filename `{filename}`.
- NEVER invent a replacement root filename such as `main.py`, `processor.py`, `order_processor.py`, or similar.
- You may create additional module filenames, but `{filename}` itself must remain the root output file.

[Layout Footprint]
{context_framing}

[AST Map Output]
{structure_map}

[Architectural Rules]
{architectural_guidance}
- Preserve all existing functions and methods by assigning each one to exactly one output file.
- Keep the core entry/orchestration methods in `{filename}` where appropriate.
- Use the existing filename `{filename}` exactly as the root-file key in the blueprint.
- Do not rename the original file as part of the refactoring.

[Required Output Layout Format]
1. EXPLANATION: Briefly explain the proposed structural division.
2. BLUEPRINT: Immediately after the explanation, provide ONE valid JSON object.

CRITICAL BLUEPRINT RULES:
- The JSON object MUST be inside exactly one fenced JSON code block.
- The opening line MUST be exactly: ```json
- The closing line MUST be exactly: ```
- The JSON object maps output filenames to lists of function/method names.
- The key `{filename}` MUST appear in the JSON if any functions or methods remain in the root file.
- The root filename MUST be exactly `{filename}`.
- Do NOT rename the root file.
- Do NOT use a <blueprint> XML tag.
- Do NOT output inline JSON.
- Do NOT output multiple JSON blocks.
- Do NOT call any tools.
- STOP immediately after the closing JSON code fence.

Example structure:

{{
  "{filename}": ["__init__", "process_order"],
  "some_specialized_module.py": ["validate_order"]
}}

The system will parse this JSON blueprint and perform the AST extraction deterministically.
"""
    else:
        prompt = f"""You are a senior Software Architect. Your task is to design a refactoring split blueprint for review.

[Context]
File Target: `{filename}`
Layout Footprint:
{context_framing}

[AST Map Output]
{structure_map}

[Architectural Rules]
{architectural_guidance}
- Ensure that core entry execution setups or initialization points remain clearly in the root file.
- Preserve all existing functions and methods by assigning each one to exactly one output file.

[Required Output Layout Format]
1. EXPLANATION: Write out your structural design reasoning. Include the marker tag `[MODE: ADVISOR_SCAFFOLD]` in your reasoning text.
2. BLUEPRINT: Immediately after the explanation, provide ONE valid JSON object.

CRITICAL BLUEPRINT RULES:
- The JSON object MUST be inside exactly one fenced JSON code block.
- The opening line MUST be exactly: ```json
- The closing line MUST be exactly: ```
- The JSON object maps output filenames to lists of function/method names.
- The key `{filename}` MUST appear in the JSON if any functions or methods remain in the root file.
- Do NOT rename the root file.
- Do NOT call any tools. Do NOT write file contents yet.
- STOP immediately after the closing JSON code fence. The system will parse this JSON blueprint and generate hollow structural scaffolding for human review.

Example structure:

{{
  "{filename}": ["__init__", "process_order"],
  "some_specialized_module.py": ["validate_order"]
}}
"""

    return prompt


def setup_refactor_sandbox(source_filepath):
    """
    Copies the target file to a hidden sandbox directory to protect production code.
    """
    abs_source = os.path.abspath(source_filepath)
    base_dir = os.path.dirname(abs_source)
    sandbox_dir = os.path.join(base_dir, ".refactor_sandbox")

    if os.path.exists(sandbox_dir):
        shutil.rmtree(sandbox_dir)  # Clear previous attempts
    os.makedirs(sandbox_dir, exist_ok=True)

    sandbox_target = os.path.join(sandbox_dir, os.path.basename(abs_source))
    shutil.copy2(abs_source, sandbox_target)

    return sandbox_target, sandbox_dir


def _generate_scaffold(original_filepath, target_filename, blocks, all_files):
    """
    Helper for advisor mode: Generates hollow scaffolding of the blueprint.
    Recreates structural layout with `pass`, keeps root imports, and adds
    inter-file imports between the newly generated modules.
    """
    with open(original_filepath, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())

    new_body = []

    # 1. Preserve original imports
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            new_body.append(node)

    # 2. Add inter-file imports for other modules in the blueprint
    for other_file in all_files:
        if other_file != target_filename and other_file.endswith('.py'):
            mod_name = other_file[:-3]
            new_body.append(ast.Import(names=[ast.alias(name=mod_name, asname=None)]))

    # 3. Scaffold classes and functions out of the requested blocks
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            new_class_body = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in blocks:
                        item.body = [ast.Pass()]
                        new_class_body.append(item)
            if new_class_body:
                # Keep decorators, bases, and keywords intact
                new_class = ast.ClassDef(
                    name=node.name,
                    bases=node.bases,
                    keywords=node.keywords,
                    body=new_class_body,
                    decorator_list=node.decorator_list
                )
                new_body.append(new_class)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in blocks:
                node.body = [ast.Pass()]
                new_body.append(node)

    # Ensure the AST produces valid Python even if empty
    if not new_body:
        new_body.append(ast.Pass())

    new_tree = ast.Module(body=new_body, type_ignores=[])
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def handle_ast_extraction(content, split_file, sandbox_dir, execute_mode=None):
    """Intercepts JSON routing plan and extracts blocks or scaffolds deterministically."""
    match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    if not match:
        return False, None

    try:
        plan = json.loads(match.group(1))

        # Detect mode cleanly without polluting JSON blueprint keys
        if execute_mode is None:
            is_scaffold_marker = "[MODE: ADVISOR_SCAFFOLD]" in content
            execute_mode = not is_scaffold_marker

        valid_entries = {fn: blocks for fn, blocks in plan.items() if isinstance(blocks, list)}
        invalid_entries = {fn: blocks for fn, blocks in plan.items() if not isinstance(blocks, list)}

        results = []
        if not execute_mode:
            print("\n⚙️  [System] Intercepted JSON routing plan. Generating scaffold in advisor mode...")
            all_files = list(valid_entries.keys())
            for fn, blocks in valid_entries.items():
                try:
                    scaffold_code = _generate_scaffold(split_file, fn, blocks, all_files)
                    target_path = os.path.join(sandbox_dir, fn)
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(scaffold_code)
                    results.append(f"[{fn}]: Scaffold generated for {len(blocks)} components")
                except Exception as e:
                    results.append(f"[{fn}]: Error generating scaffold ({e})")
        else:
            print("\n⚙️  [System] Intercepted JSON routing plan. Executing AST extraction natively...")
            results = [
                f"[{fn}]: {extract_code_blocks(split_file, os.path.join(sandbox_dir, fn), blocks)}"
                for fn, blocks in valid_entries.items()
            ]

        report = "\n".join(results) if results else "(no valid entries were processed)"
        print(report)

        if invalid_entries:
            print(f"\n❌ [System] Rejected malformed blueprint entries: {invalid_entries}")
            return True, (
                "System Alert: Blueprint schema error. The following entries were REJECTED "
                f"because their value must be a LIST of function/method names, not a string "
                f"or other type: {invalid_entries}\n\n"
                + (f"Valid entries were still processed:\n{report}\n\n" if results else "")
                + "This ```json blueprint format is ONLY for extracting existing functions/methods "
                "by name into files. It is NOT for writing new code such as import statements. "
                "To add a missing import, use the `patch_file` or `append_file` tool directly on "
                "the target file instead.\n"
                "Do not output 'Refactor Phase Complete' until this is actually fixed."
            )

        return True, (
            "System Alert: AST Processing successfully executed.\n"
            f"Results:\n{report}\n\n"
            "Next Step: Review the extracted files with the available tools if needed. "
            "Do not attempt to recreate the extracted methods. "
            "When the refactor is complete, output 'Refactor Phase Complete'."
        )

    except json.JSONDecodeError:
        print("\n❌ [System] Failed to parse JSON plan.")
        return True, (
            "System Alert: Your JSON block was invalid. "
            "Please output ONLY valid JSON in the ```json block."
        )


def verify_refactor_integrity(original_filepath, generated_files_dir, expected_files=None):
    """
    Defensive Guardrail: Compares the AST components to ensure no logic is lost,
    and verifies all planned files from the blueprint exist.
    """
    original_filename = os.path.basename(original_filepath)

    # 1. ENFORCE BLUEPRINT PLAN: Prevent premature completion
    if expected_files:
        missing_files = [
            f for f in expected_files
            if not os.path.exists(os.path.join(generated_files_dir, f))
        ]

        if missing_files:
            next_file = missing_files[0]

            return False, (
                f"INCOMPLETE REFACTOR: `{next_file}` from your blueprint is missing.\n"
                f"Create this file now using `write_file`.\n"
                f"Do NOT output `Refactor Phase Complete` yet.\n"
                f"Do NOT use markdown code fences.\n"
                f"Do NOT output JSON outside the XML tool-call format.\n"
                f"After this tool call is executed, stop and wait for the next "
                f"verification pass.\n\n"
                f"<tool_call>\n"
                f"{{\n"
                f'    "name": "write_file",\n'
                f'    "args": {{"filepath": "{next_file}"}}\n'
                f"}}\n"
                f"</tool_call>\n"
                f"<payload>\n"
                f"import sys\n\n"
                f"class Skeleton:\n"
                f"    pass\n"
                f"</payload>"
            )

    try:
        with open(original_filepath, 'r', encoding='utf-8') as f:
            orig_tree = ast.parse(f.read())
    except Exception as e:
        return False, f"Failed to parse original file AST: {e}"

    original_methods = set()

    for node in ast.walk(orig_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            original_methods.add(node.name)

    all_sandbox_methods = set()

    for root, _, files in os.walk(generated_files_dir):
        for file in files:
            if file.endswith('.py') and not file.startswith('.'):
                filepath = os.path.join(root, file)

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        tree = ast.parse(f.read())

                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            all_sandbox_methods.add(node.name)

                except SyntaxError as e:
                    return False, (
                        f"Syntax Error in generated file '{file}': {e}"
                    )

    # 2. Check for dropped logic
    missing = original_methods - all_sandbox_methods

    if missing:
        return False, (
            f"CRITICAL ERROR: The following functions/methods were lost "
            f"during refactoring: {missing}"
        )

    # 3. Guard against zero action
    if (
        len(all_sandbox_methods) == len(original_methods)
        and len(os.listdir(generated_files_dir)) <= 1
    ):
        return False, (
            "INCOMPLETE REFACTOR: No new files were generated. "
            "Follow your blueprint."
        )

    return True, (
        "Integrity check passed. All planned components accounted for "
        "and syntactically valid."
    )
