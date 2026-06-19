"""
Utility functions for Bench Manager.
Provides secure subprocess execution, input sanitization,
bench path detection, and command logging.
"""

import os
import re
import shutil
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
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error resolving bench path: {e}")

    # Fallback: check Bench Settings
    try:
        settings_path = frappe.db.get_single_value("Bench Settings", "bench_path")
        if settings_path and validate_bench_path(settings_path):
            return settings_path
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error reading Bench Settings: {e}")

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

    if value.startswith("-"):
        frappe.throw(f"Invalid {field_name}: cannot start with a hyphen")

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


def run_bench_command(command_parts, bench_path=None, realtime=True, user=None, stdin_data=None, site=None):
    """Run a bench CLI command with proper error handling and real-time output.

    Uses a pseudo-terminal (pty) so that subprocess output includes progress
    bars and all verbose output that bench/click would normally suppress
    when stdout is a pipe.

    Args:
        command_parts (list): List of command arguments (e.g., ['new-site', 'mysite']).
        bench_path (str, optional): Path to bench directory. Auto-detected if None.
        realtime (bool): Whether to publish real-time output updates.
        user (str, optional): User who initiated the command. Defaults to frappe.session.user.
        stdin_data (str, optional): Data to pipe to stdin for interactive commands.
        site (str, optional): Site context for background threads.
    """
    import pty
    import select
    import errno

    try:
        has_context = bool(getattr(frappe.local, "conf", None))
    except Exception:
        has_context = False
        frappe.local.flags = frappe._dict()

    if not has_context:
        current_site = site or getattr(frappe.local, "site", None)
        if current_site:
            try:
                frappe.init(current_site)
                frappe.connect()
                if user:
                    frappe.set_user(user)
            except Exception as e:
                with open("/tmp/bench_mgr_thread_err.log", "a") as f:
                    f.write(f"Context init error: {e}\n")

    if not user:
        try:
            user = frappe.session.user if frappe.has_active_session() else "Administrator"
        except Exception:
            user = "Administrator"

    if bench_path is None:
        from bench_manager.api import get_bench_path
        bench_path = get_bench_path()

    bench_exec = shutil.which("bench")
    if not bench_exec:
        bench_exec = "bench"

    cmd = [bench_exec] + command_parts
    command_str = " ".join(["bench"] + command_parts)

    frappe.logger("bench_manager").info(f"Executing: {command_str}")

    def _publish(msg, msg_type="stdout"):
        """Publish realtime message reliably from thread context.
        Dual-publishes via both Socket.IO (frappe.publish_realtime)
        and the in-memory SSE buffer for zero-latency live logs.
        """
        if not realtime:
            return

        # 1. Push to SSE buffer (always works, even without Socket.IO)
        try:
            from bench_manager.api import push_sse_event
            push_sse_event(msg, msg_type)
        except Exception:
            pass

        # 2. Publish via Socket.IO (frappe.publish_realtime)
        try:
            frappe.publish_realtime(
                "bench_console",
                {"message": msg, "msg_type": msg_type},
                room="all",
                after_commit=False,
            )
        except Exception as e:
            # Fallback: publish via Redis directly
            try:
                import json as _json
                r = frappe.cache()
                r.publish(
                    "events",
                    _json.dumps({
                        "event": "bench_console",
                        "message": {"message": msg, "msg_type": msg_type},
                        "room": "all",
                        "namespace": getattr(frappe.local, "site", None)
                    }),
                )
            except Exception:
                pass

    if realtime:
        _publish(f"$ {command_str}", "command")

    try:
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "TERM": "xterm-256color",
            "COLUMNS": "120"
        }

        master_fd, slave_fd = pty.openpty()

        process = subprocess.Popen(
            cmd,
            cwd=bench_path,
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )

        os.close(slave_fd)

        if stdin_data and process.stdin:
            process.stdin.write(stdin_data.encode() if isinstance(stdin_data, str) else stdin_data)
            process.stdin.close()

        output_lines = []
        buffer = ""

        # Read from PTY master until EIO (child closed its end).
        # Do NOT use process.poll() — it causes a race where we
        # break before reading all buffered PTY data.
        ansi_re = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?\x07|\x1b\(B")

        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except (ValueError, OSError):
                break

            if not ready:
                # Safety: if process exited AND no data for 1s, we're done
                if process.poll() is not None:
                    break
                continue

            try:
                data = os.read(master_fd, 4096)
            except OSError as e:
                if e.errno == errno.EIO:
                    break  # Normal PTY EOF — all data has been read
                raise

            if not data:
                break

            text = data.decode("utf-8", errors="replace")
            text = ansi_re.sub("", text)

            for char in text:
                if char in ("\n", "\r"):
                    clean_msg = buffer.strip()
                    if clean_msg:
                        output_lines.append(clean_msg)
                        _publish(clean_msg)
                    buffer = ""
                else:
                    buffer += char

        # Flush remaining buffer
        if buffer.strip():
            output_lines.append(buffer.strip())
            _publish(buffer.strip())

        try:
            os.close(master_fd)
        except OSError:
            pass
        process.wait(timeout=600)

        full_output = "\n".join(output_lines)
        status = "Success" if process.returncode == 0 else "Failed"

        log_command(command_str, full_output, "", status, user)

        if realtime:
            _publish(
                f"Command {'completed successfully' if status == 'Success' else 'failed'}",
                "success" if status == "Success" else "error"
            )

        return {
            "output": full_output,
            "error": "",
            "returncode": process.returncode,
        }

    except subprocess.TimeoutExpired:
        process.kill()
        log_command(command_str, "", "Command timed out after 600 seconds", "Failed", user)
        if realtime:
            _publish("Command timed out after 600 seconds", "error")
        frappe.throw("Command timed out after 600 seconds")

    except Exception as e:
        error_msg = str(e)
        log_command(command_str, "", error_msg, "Failed", user)
        frappe.logger("bench_manager").error(f"Command failed: {error_msg}")
        if realtime:
            _publish(f"Error: {error_msg}", "error")
        frappe.throw(f"Command execution failed: {error_msg}")


def log_command(command, output="", error="", status="Success", user="Administrator"):
    """Log a bench command execution to the Bench Command Log DocType.

    Args:
        command (str): The command that was executed.
        output (str): Standard output from the command.
        error (str): Standard error from the command.
        status (str): Execution status - Success, Failed, or Running.
        user (str): User who initiated the command.
    """
    try:
        log = frappe.get_doc(
            {
                "doctype": "Bench Command Log",
                "command": command[:1000],
                "output": output[:50000] if output else "",
                "error": error[:10000] if error else "",
                "status": status,
                "executed_by": user,
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
