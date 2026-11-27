import os
import platform
import re
import subprocess


# --- NATIVE HANDLER: /requirements ---
def generate_requirements_native(target_dir, no_version=False):
    try:
        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        if not os.path.isdir(abs_target_dir):
            return f"Error: Resolved path '{abs_target_dir}' is not a valid system directory."

        is_windows = platform.system() == "Windows"
        pip_bin = None

        for venv_name in (".venv", "venv", "env"):
            potential_path = os.path.join(abs_target_dir, venv_name)
            if os.path.isdir(potential_path):
                test_pip = (
                    os.path.join(potential_path, "Scripts", "pip.exe")
                    if is_windows
                    else os.path.join(potential_path, "bin", "pip")
                )
                if os.path.isfile(test_pip):
                    pip_bin = test_pip
                    break

        if not pip_bin:
            return f"Error: No virtual environment found inside '{abs_target_dir}'."

        final_output_path = os.path.join(abs_target_dir, "requirements.txt")
        print(f"   [Backend] Executing: '{pip_bin}' freeze")

        result = subprocess.run(
            f'"{pip_bin}" freeze',
            shell=True,
            capture_output=True,
            text=True,
            cwd=abs_target_dir,
            timeout=15,
        )

        if result.returncode != 0:
            return f"Error: Pip execution failed. Stderr: {result.stderr}"

        raw_packages = result.stdout.strip()
        if not raw_packages:
            raw_packages = "# No dependencies found. The virtual environment is empty."
        elif no_version:
            processed_lines = []
            for line in raw_packages.splitlines():
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("#"):
                    processed_lines.append(line_strip)
                    continue
                pkg_name = re.split(r"==|>=|<=| @ ", line_strip)[0].strip()
                if pkg_name:
                    processed_lines.append(pkg_name)
            raw_packages = "\n".join(processed_lines)

        with open(final_output_path, "w", encoding="utf-8") as f:
            f.write(raw_packages + "\n")

        return f"SUCCESS: Natively generated target file: '{final_output_path}'"

    except Exception as e:
        return f"Error executing native requirements handler: {e}"
