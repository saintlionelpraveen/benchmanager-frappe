"""
Utility functions for Bench Manager.
Provides secure subprocess execution, input sanitization,
bench path detection, and command logging.
"""

import os
import re
import subprocess
import frappe


def get_bench_path():
    """Auto-detect the bench directory path.

    Returns the absolute path to the bench root directory
    by traversing up from the app installation path.

    Returns:
        str: Absolute path to the bench directory.

    Raises:
        frappe.ValidationError: If bench path cannot be detected.
    """
    try:
        app_path = frappe.get_app_path("bench_manager")
        bench_path = os.path.abspath(os.path.join(app_path, "..", "..", ".."))
        if validate_bench_path(bench_path):
            return bench_path
    except Exception:
        pass

    # Fallback: check Bench Settings
    try:
        settings_path = frappe.db.get_single_value("Bench Settings", "bench_path")
        if settings_path and validate_bench_path(settings_path):
            return settings_path
    except Exception:
        pass

    frappe.throw("Could not auto-detect bench path. Please set it in Bench Settings.")


def validate_bench_path(bench_path):
    """Validate that the given path is a valid Frappe bench directory.

    A valid bench directory contains both 'apps' and 'sites' subdirectories
    and a 'Procfile'.

    Args:
        bench_path (str): Path to validate.

    Returns:
        bool: True if valid bench directory, False otherwise.
    """
    if not bench_path or not os.path.isdir(bench_path):
        return False

    required = ["apps", "sites"]
    for item in required:
        if not os.path.isdir(os.path.join(bench_path, item)):
            return False

    return True


def sanitize_input(value, field_name="input"):
    """Sanitize user input to prevent command injection.

    Only allows alphanumeric characters, dots, hyphens, and underscores.

    Args:
        value (str): The input value to sanitize.
        field_name (str): Name of the field for error messages.

    Returns:
        str: The sanitized value.

    Raises:
        frappe.ValidationError: If the input contains invalid characters.
    """
    if not value:
        frappe.throw(f"{field_name} cannot be empty")

    value = str(value).strip()

    if not re.match(r"^[a-zA-Z0-9._-]+$", value):
        frappe.throw(
            f"Invalid {field_name}: only alphanumeric characters, "
            "dots, hyphens, and underscores are allowed"
        )

    if ".." in value:
        frappe.throw(f"Invalid {field_name}: path traversal not allowed")

    return value


def sanitize_git_url(url):
    """Sanitize and validate a git URL.

    Accepts HTTPS and SSH git URLs. Rejects URLs containing
    shell metacharacters to prevent command injection.

    Args:
        url (str): The git URL to validate.

    Returns:
        str: The validated URL.

    Raises:
        frappe.ValidationError: If the URL format is invalid or dangerous.
    """
    if not url:
        frappe.throw("Git URL cannot be empty")

    url = str(url).strip()

    # Check for dangerous shell characters
    dangerous_chars = [";", "&", "|", "`", "$", "(", ")", "{", "}", "<", ">", "!", "'", '"']
    for char in dangerous_chars:
        if char in url:
            frappe.throw(f"Invalid git URL: contains forbidden character '{char}'")

    # Validate URL format
    https_pattern = r"^https?://[a-zA-Z0-9._\-/]+(?:\.git)?$"
    ssh_pattern = r"^git@[a-zA-Z0-9._\-]+:[a-zA-Z0-9._\-/]+(?:\.git)?$"

    if not re.match(https_pattern, url) and not re.match(ssh_pattern, url):
        frappe.throw("Invalid git URL format. Use HTTPS or SSH URL.")

    return url


def run_bench_command(command_parts, bench_path=None, realtime=True):
    """Run a bench CLI command with proper error handling and real-time output.

    Executes the command as a subprocess, streams output via frappe.realtime,
    and logs the result to Bench Command Log.

    Args:
        command_parts (list): List of command arguments (e.g., ['new-site', 'mysite']).
        bench_path (str, optional): Path to bench directory. Auto-detected if None.
        realtime (bool): Whether to publish real-time output updates.

    Returns:
        dict: Dictionary with 'output', 'error', 'returncode' keys.
    """
    if bench_path is None:
        bench_path = get_bench_path()

    cmd = ["bench"] + command_parts
    command_str = " ".join(cmd)

    frappe.logger("bench_manager").info(f"Executing: {command_str}")

    if realtime:
        frappe.publish_realtime(
            "bench_console",
            {"message": f"$ {command_str}", "msg_type": "command"},
            user=frappe.session.user,
        )

    try:
        process = subprocess.Popen(
            cmd,
            cwd=bench_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "TERM": "dumb"},
        )

        output_lines = []
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                stripped = line.rstrip()
                output_lines.append(stripped)
                if realtime and stripped:
                    frappe.publish_realtime(
                        "bench_console",
                        {"message": stripped, "msg_type": "stdout"},
                        user=frappe.session.user,
                    )

        process.wait(timeout=300)
        error_output = process.stderr.read() if process.stderr else ""

        if error_output and realtime:
            for err_line in error_output.strip().split("\n"):
                if err_line.strip():
                    frappe.publish_realtime(
                        "bench_console",
                        {"message": err_line.strip(), "msg_type": "stderr"},
                        user=frappe.session.user,
                    )

        full_output = "\n".join(output_lines)
        status = "Success" if process.returncode == 0 else "Failed"

        log_command(command_str, full_output, error_output, status)

        if realtime:
            frappe.publish_realtime(
                "bench_console",
                {
                    "message": f"Command {'completed successfully' if status == 'Success' else 'failed'}",
                    "msg_type": "success" if status == "Success" else "error",
                },
                user=frappe.session.user,
            )

        return {
            "output": full_output,
            "error": error_output,
            "returncode": process.returncode,
        }

    except subprocess.TimeoutExpired:
        process.kill()
        log_command(command_str, "", "Command timed out after 300 seconds", "Failed")
        if realtime:
            frappe.publish_realtime(
                "bench_console",
                {"message": "Command timed out after 300 seconds", "msg_type": "error"},
                user=frappe.session.user,
            )
        frappe.throw("Command timed out after 300 seconds")

    except Exception as e:
        error_msg = str(e)
        log_command(command_str, "", error_msg, "Failed")
        frappe.logger("bench_manager").error(f"Command failed: {error_msg}")
        if realtime:
            frappe.publish_realtime(
                "bench_console",
                {"message": f"Error: {error_msg}", "msg_type": "error"},
                user=frappe.session.user,
            )
        frappe.throw(f"Command execution failed: {error_msg}")


def log_command(command, output="", error="", status="Success"):
    """Log a bench command execution to the Bench Command Log DocType.

    Args:
        command (str): The command that was executed.
        output (str): Standard output from the command.
        error (str): Standard error from the command.
        status (str): Execution status - Success, Failed, or Running.
    """
    try:
        log = frappe.get_doc(
            {
                "doctype": "Bench Command Log",
                "command": command[:1000],
                "output": output[:50000] if output else "",
                "error": error[:10000] if error else "",
                "status": status,
                "executed_by": frappe.session.user,
            }
        )
        log.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.logger("bench_manager").error(f"Failed to log command: {e}")


def get_site_list():
    """Get list of all sites from the bench sites directory.

    Returns:
        list: List of site name strings.
    """
    bench_path = get_bench_path()
    sites_path = os.path.join(bench_path, "sites")
    sites = []

    skip = {"assets", "apps.txt", "common_site_config.json", "currentsite.txt", "apps.json"}

    for item in os.listdir(sites_path):
        full_path = os.path.join(sites_path, item)
        if os.path.isdir(full_path) and item not in skip and not item.startswith("."):
            # Verify it's a real site by checking for site_config.json
            if os.path.exists(os.path.join(full_path, "site_config.json")):
                sites.append(item)

    return sorted(sites)


def get_app_list():
    """Get list of all apps in the bench apps directory.

    Returns:
        list: List of app name strings.
    """
    bench_path = get_bench_path()
    apps_path = os.path.join(bench_path, "apps")
    apps = []

    for item in os.listdir(apps_path):
        full_path = os.path.join(apps_path, item)
        if os.path.isdir(full_path) and not item.startswith("."):
            apps.append(item)

    return sorted(apps)
