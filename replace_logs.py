import os
import re

app_dir = "/home/gsus/proyectos/frappe-bench/apps/mint/mint"
files_to_check = []
for root, dirs, files in os.walk(app_dir):
    for f in files:
        if f.endswith(".py"):
            files_to_check.append(os.path.join(root, f))

def replace_in_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    modified = False

    # Check if needs import
    needs_import = False

    # Replace frappe.log_error(title="...", message=...)
    # We will just replace frappe.log_error(...) with log_mint_error(title=..., description=...)
    def log_error_replacer(m):
        nonlocal needs_import
        needs_import = True
        args = m.group(1)
        # basic text replacement
        args = args.replace("message=", "description=")
        return f"log_mint_error({args})"

    content = re.sub(r'frappe\.log_error\((.*?)\)', log_error_replacer, content, flags=re.DOTALL)

    # Replace frappe.logger("...").[level](...)
    def logger_replacer(m):
        nonlocal needs_import
        needs_import = True
        level = m.group(1).lower()
        args = m.group(2)
        if level == "error":
            return f'log_mint_error("Error", {args})'
        elif level == "warning":
            return f'log_mint_warning("Warning", {args})'
        elif level == "info":
            return f'log_mint_info("Info", {args})'
        else:
            return m.group(0)

    content = re.sub(r'frappe\.logger\([^)]*\)\.(error|warning|info)\((.*?)\)', logger_replacer, content, flags=re.DOTALL)

    if needs_import and content != original:
        # Add import at top if not present
        if "from mint.apis.mint_log import" not in content:
            import_str = "from mint.apis.mint_log import log_mint_error, log_mint_warning, log_mint_info\n"
            # find first import
            match = re.search(r'^import .*|^from .* import .*', content, re.MULTILINE)
            if match:
                idx = match.start()
                content = content[:idx] + import_str + content[idx:]
            else:
                content = import_str + content
                
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {file_path}")

for f in files_to_check:
    # exclude mint_log.py itself!
    if not f.endswith("mint_log.py"):
        replace_in_file(f)
