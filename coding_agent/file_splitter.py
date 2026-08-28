import ast
import os
import shutil


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
        prompt = f"""You are a senior Software Architect. Your task is to design a refactoring split blueprint.

[Context]
File Target: `{filename}`
Layout Footprint:
{context_framing}

[AST Map Output]
{structure_map}

[Architectural Rules]
{architectural_guidance}
- Ensure that core entry execution setups or initialization points remain clearly in the root file.

[Required Output Layout Format]
1. EXPLANATION: Write out your structural design reasoning out loud first. Justify your module division choices.
2. BLUEPRINT: Provide your file mapping inside a <blueprint> XML tag as a JSON dictionary (mapping recommended filenames to lists of their respective target classes/methods). DO NOT use markdown blocks.

CRITICAL INSTRUCTION: 
STOP after writing the <blueprint> tag. Do NOT use tools. Do NOT write any file contents. 
The system will use deterministic AST extraction to safely move the code blocks based on your blueprint automatically.
"""
    else:
        prompt = f"""You are a senior Software Architect. Your task is to design a refactoring split blueprint.

[Context]
File Target: `{filename}`
Layout Footprint:
{context_framing}

[AST Map Output]
{structure_map}

[Architectural Rules]
{architectural_guidance}
- Ensure that core entry execution setups or initialization points remain clearly in the root file.

[Required Output Layout Format]
1. EXPLANATION: Write out your structural design reasoning out loud first. Justify your module division choices using generic domain terms.
2. BLUEPRINT: Provide your file mapping inside a <blueprint> XML tag as a JSON dictionary (mapping filenames to target methods). DO NOT use markdown (```) blocks.
3. IMMEDIATE EXECUTION: You must immediately begin executing your plan. Use your `write_file` tool to create the FIRST file from your blueprint. 
   - CRITICAL: Write ONLY the structural boilerplate skeleton. 
   - You MUST use `pass` for every single method body to ensure perfectly valid Python syntax. 
   - Do NOT attempt to write the actual implementation logic yet.
4. ITERATIVE COMPLETION: You MUST continue using your `write_file` tool to scaffold EVERY remaining file listed in your blueprint.
   - Do NOT output "Refactor Phase Complete" until all planned files are successfully created.
   - DO NOT delete or modify the original `{filename}` file.

MANDATORY TOOL CALL FORMAT:
<tool_call>
{{
    "name": "write_file", 
    "args": {{"filepath": "filename_from_blueprint.py"}}
}}
</tool_call>
<payload>
import sys

class YourClassName:
    # YOU MUST INCLUDE ALL METHODS ASSIGNED TO THIS FILE IN THE BLUEPRINT
    def method_assigned_in_blueprint_1(self):
        pass
        
    def method_assigned_in_blueprint_2(self):
        pass
</payload>

Start executing the skeleton generation for the first file immediately after closing your <blueprint> tag.
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


def verify_refactor_integrity(original_filepath, generated_files_dir, expected_files=None):
    """
    Defensive Guardrail: Compares the AST components to ensure no logic is lost,
    and verifies all planned files from the blueprint exist.

    NOTE:
    The original root filename is intentionally INCLUDED in verification because
    AST extraction may replace that file inside the sandbox with the refactored
    version. Skipping it would incorrectly report root methods as missing.
    """
    original_filename = os.path.basename(original_filepath)

    # 1. ENFORCE BLUEPRINT PLAN: Prevent premature completion
    if expected_files:
        missing_files = [
            f for f in expected_files
            if not os.path.exists(os.path.join(generated_files_dir, f))
        ]

        if missing_files:
            return False, (
                f"INCOMPLETE REFACTOR: You proposed creating these files in your "
                f"<blueprint>, but they are missing: {missing_files}\n"
                f"You must scaffold ALL of them using `write_file` before outputting "
                f"'Refactor Phase Complete'.\n\n"
                f"CRITICAL REMINDER: You MUST use the exact XML format below. "
                f"Do NOT use markdown (```) blocks.\n\n"
                f"MANDATORY FORMAT:\n"
                f"<tool_call>\n"
                f"{{\n"
                f"    \"name\": \"write_file\",\n"
                f"    \"args\": {{\"filepath\": \"filename.py\"}}\n"
                f"}}\n"
                f"</tool_call>\n"
                f"<payload>\n"
                f"import sys\n\nclass Skeleton:\n    pass\n"
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
                    return False, f"Syntax Error in generated file '{file}': {e}"

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
