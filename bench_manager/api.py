"""
API endpoints for Bench Manager.
All bench commands are executed via Python subprocess with proper
sanitization, error handling, and real-time output streaming.
Only System Manager role can access these endpoints.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time

import frappe
from bench_manager.utils import (
    get_app_list,
    get_bench_path,
    get_site_list,
    run_bench_command,
    sanitize_git_url,
    sanitize_input,
)


# ─── Site Management ────────────────────────────────────────────────


@frappe.whitelist()
def create_site(site_name, admin_password, db_password=None):
    """Create a new Frappe site.

    Args:
        site_name (str): Name of the site to create (e.g., 'mysite.localhost').
        admin_password (str): Administrator password for the new site.
        db_password (str, optional): Database root password.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")
    if not admin_password:
        frappe.throw("Admin password is required")

    cmd = ["new-site", site_name, "--admin-password", admin_password]
    if db_password:
        cmd.extend(["--db-root-password", db_password])

    import threading
    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Site creation for '{site_name}' has started."}


@frappe.whitelist()
def drop_site(site_name, db_root_password=None):
    """Drop/delete a Frappe site."""
    frappe.only_for("System Manager")
    site_name = sanitize_input(site_name, "Site Name")
    bench_path = get_bench_path()
    site_dir = os.path.join(bench_path, "sites", site_name)

    if not os.path.isdir(site_dir):
        frappe.throw(f"Site directory '{site_name}' does not exist")

    cmd = ["drop-site", site_name, "--force", "--no-backup"]
    if db_root_password:
        cmd.extend(["--db-root-password", db_root_password])

    import threading
    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Site deletion for '{site_name}' has started."}


# ─── Site Server Management ─────────────────────────────────────────
# Each non-bench-manager site runs on its own dev server (bench serve)
# on a unique port. This avoids the Frappe site-pinning problem where
# opening localhost always serves the bench_manager site regardless
# of the Host header.

_SITE_SERVERS_FILE = "site_servers.json"


def _get_site_servers_path():
    """Get path to the site servers tracking file."""
    bench_path = get_bench_path()
    return os.path.join(bench_path, _SITE_SERVERS_FILE)


def _read_site_servers():
    """Read the site servers tracking file.

    Returns:
        dict: Mapping of site_name -> {"pid": int, "port": int}
    """
    path = _get_site_servers_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_site_servers(data):
    """Write the site servers tracking file."""
    path = _get_site_servers_path()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _is_process_alive(pid):
    """Check if a process with the given PID is still running and not a zombie."""
    try:
        os.kill(pid, 0)
        # Also check if the process is a zombie
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("State:"):
                        if "Z" in line:  # Zombie state
                            return False
                        break
        except (FileNotFoundError, PermissionError):
            pass
        return True
    except (OSError, ProcessLookupError):
        return False


def _find_free_port():
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _cleanup_dead_servers():
    """Remove entries for servers whose processes are no longer running."""
    servers = _read_site_servers()
    cleaned = {}
    for site, info in servers.items():
        if _is_process_alive(info.get("pid", 0)):
            cleaned[site] = info
    if len(cleaned) != len(servers):
        _write_site_servers(cleaned)
    return cleaned


def _ensure_site_hostname_mapping(site_name, domains, site_map):
    """Ensure a site has a .localhost domain mapping for multi-tenancy."""
    if not site_name.endswith(".localhost"):
        hostname = f"{site_name}.localhost"
    else:
        hostname = site_name
    domains[hostname] = site_name
    site_map[hostname] = site_name


@frappe.whitelist()
def get_current_site():
    """Get the currently active site.

    For bench_manager, returns the bench_manager site (always active).
    For other sites, checks if a dev server subprocess is running.
    Returns the first running site found, or None.
    """
    frappe.only_for("System Manager")
    servers = _cleanup_dead_servers()
    # Return the first running site (there should ideally be 0 or 1)
    for site_name in servers:
        return site_name
    return None


@frappe.whitelist()
def check_site_active(site_name):
    """Check if a given site is currently active.

    For bench_manager site: always active.
    For other sites: checks if a dev server process is running.

    Args:
        site_name (str): Name of the site to check.

    Returns:
        dict: {"active": bool, "is_host_site": bool, "port": int|None}
    """
    frappe.only_for("System Manager")
    site_name = sanitize_input(site_name, "Site Name")

    # The site serving the dashboard is always active
    is_host_site = site_name == getattr(frappe.local, "site", None)
    if is_host_site:
        return {"active": True, "is_host_site": True, "port": None}

    # Check if a dev server is running for this site
    servers = _cleanup_dead_servers()
    if site_name in servers:
        return {"active": True, "is_host_site": False, "port": servers[site_name]["port"]}

    return {"active": False, "is_host_site": False, "port": None}


@frappe.whitelist()
def start_site_server(site_name):
    """Start a dedicated dev server for the given site.

    Spawns a `bench serve --port <port>` process with FRAPPE_SITE env set.
    The site gets its own isolated dev server on a unique port.
    """
    frappe.only_for("System Manager")
    site_name = sanitize_input(site_name, "Site Name")
    bench_path = get_bench_path()
    sites_path = os.path.join(bench_path, "sites")

    if not os.path.isdir(os.path.join(sites_path, site_name)):
        frappe.throw(f"Site '{site_name}' does not exist")

    # Don't allow starting bench_manager (it's always running)
    if site_name == frappe.local.site:
        frappe.throw("Bench Manager site is always active and cannot be started separately.")

    # Check if already running
    servers = _cleanup_dead_servers()
    if site_name in servers:
        return {
            "status": "already_running",
            "port": servers[site_name]["port"],
            "message": f"Site '{site_name}' is already running on port {servers[site_name]['port']}."
        }

    # Find a free port
    port = _find_free_port()

    # Spawn the dev server subprocess
    env = os.environ.copy()
    env["FRAPPE_SITE"] = site_name
    # Remove Werkzeug reloader env vars inherited from parent server
    # to prevent the child from reusing the parent's socket FD
    env.pop("WERKZEUG_SERVER_FD", None)
    env.pop("WERKZEUG_RUN_MAIN", None)

    log_dir = os.path.join(bench_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"site_server_{site_name}.log")

    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            ["bench", "serve", "--port", str(port)],
            cwd=bench_path,
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    # Track the server
    servers[site_name] = {"pid": proc.pid, "port": port}
    _write_site_servers(servers)

    return {
        "status": "started",
        "port": port,
        "pid": proc.pid,
        "message": f"Site '{site_name}' started on port {port}."
    }


@frappe.whitelist()
def stop_site_server(site_name):
    """Stop the dedicated dev server for the given site."""
    frappe.only_for("System Manager")
    site_name = sanitize_input(site_name, "Site Name")

    # Don't allow stopping bench_manager
    if site_name == frappe.local.site:
        frappe.throw("Bench Manager site cannot be stopped.")

    servers = _read_site_servers()
    if site_name not in servers:
        return {"status": "not_running", "message": f"Site '{site_name}' is not running."}

    pid = servers[site_name].get("pid")
    if pid and _is_process_alive(pid):
        try:
            # Kill the process group to ensure child processes are also terminated
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        # Give it a moment, then force kill if needed
        time.sleep(0.5)
        if _is_process_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    del servers[site_name]
    _write_site_servers(servers)

    return {"status": "stopped", "message": f"Site '{site_name}' stopped."}


@frappe.whitelist()
def get_site_open_url(site_name):
    """Get the correct URL to open a specific site in the browser.

    For bench_manager: returns the current browser URL (same server).
    For other sites: returns the URL of the dedicated dev server if running.
    """
    frappe.only_for("System Manager")
    site_name = sanitize_input(site_name, "Site Name")
    bench_path = get_bench_path()
    sites_path = os.path.join(bench_path, "sites")

    if not os.path.isdir(os.path.join(sites_path, site_name)):
        frappe.throw(f"Site '{site_name}' does not exist")

    # For bench_manager site, return the current server URL
    if site_name == frappe.local.site:
        port = 8000
        csc_path = os.path.join(sites_path, "common_site_config.json")
        try:
            with open(csc_path) as f:
                config = json.load(f)
                port = config.get("webserver_port", 8000)
        except Exception:
            pass
        return {
            "url": None,  # Signal to JS to use current window location
            "is_bench_manager": True,
            "port": port,
        }

    # For other sites, check if a dev server is running
    servers = _cleanup_dead_servers()
    if site_name not in servers:
        return {
            "url": None,
            "is_running": False,
            "message": "Site is not running. Start the site first.",
        }

    port = servers[site_name]["port"]
    return {
        "url": f"http://localhost:{port}",
        "is_running": True,
        "port": port,
    }


@frappe.whitelist()
def set_current_site(site_name):
    """Set the currently active site by starting its dev server.

    This is a convenience wrapper around start_site_server.
    """
    return start_site_server(site_name)


@frappe.whitelist()
def clear_current_site(site_name=None):
    """Stop the site's dedicated dev server."""
    if site_name:
        return stop_site_server(site_name)
    # If no site specified, stop all non-bench-manager site servers
    servers = _cleanup_dead_servers()
    for site in list(servers.keys()):
        stop_site_server(site)
    return {"status": "success", "message": "All site servers stopped."}



@frappe.whitelist()
def list_sites():
    """List all existing sites with their status and current site info."""
    frappe.only_for("System Manager")

    sites = get_site_list()
    bench_path = get_bench_path()
    site_data = []

    # Get running site servers to determine status
    running_servers = _cleanup_dead_servers()
    bench_manager_site = frappe.local.site

    for site in sites:
        site_config_path = os.path.join(bench_path, "sites", site, "site_config.json")
        status = "Inactive"
        port = None

        try:
            with open(site_config_path, "r") as f:
                config = json.load(f)
                if config.get("maintenance_mode", 0):
                    status = "Maintenance"
                elif site == bench_manager_site:
                    status = "Active"  # Bench manager is always active
                elif site in running_servers:
                    status = "Active"
                    port = running_servers[site].get("port")
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error reading site config for {site}: {e}")
            status = "Unknown"

        apps = []
        try:
            apps = _get_site_apps(site, bench_path)
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error fetching apps for {site}: {e}")

        site_data.append({
            "site_name": site,
            "status": status,
            "is_current": site == bench_manager_site or site in running_servers,
            "is_bench_manager": site == bench_manager_site,
            "port": port,
            "apps": apps,
        })

    return site_data


def _get_site_apps(site_name, bench_path):
    """Get installed apps for a site by reading apps.json or apps.txt.

    Args:
        site_name (str): The site name.
        bench_path (str): The bench directory path.

    Returns:
        list: List of installed app names.
    """
    apps = []

    # Try apps.json first (Frappe v15+)
    apps_json = os.path.join(bench_path, "sites", site_name, "apps.json")
    if os.path.exists(apps_json):
        try:
            with open(apps_json, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    apps = list(data.keys())
                elif isinstance(data, list):
                    apps = data
                return apps
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error reading apps.json for {site_name}: {e}")
            pass

    # Fallback to apps.txt
    apps_txt = os.path.join(bench_path, "sites", site_name, "apps.txt")
    if os.path.exists(apps_txt):
        try:
            with open(apps_txt, "r") as f:
                apps = [line.strip() for line in f if line.strip()]
                if apps: return apps
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error reading apps.txt for {site_name}: {e}")
            pass

    # Final fallback: site_config.json
    site_config = os.path.join(bench_path, "sites", site_name, "site_config.json")
    if os.path.exists(site_config):
        try:
            with open(site_config, "r") as f:
                data = json.load(f)
                apps = data.get("installed_apps", [])
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error reading site_config.json for {site_name}: {e}")
            pass

    return apps


@frappe.whitelist()
def migrate_site(site_name):
    """Run bench migrate on a specific site.

    Args:
        site_name (str): Name of the site to migrate.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")

    import threading
    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": ["--site", site_name, "migrate"],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Migration for '{site_name}' has started."}


@frappe.whitelist()
def backup_site(site_name, with_files=1, backup_path=None):
    """Create a backup of a site.

    Args:
        site_name (str): Name of the site to backup.
        with_files (int, optional): Whether to backup files (default: 1).
        backup_path (str, optional): Custom path to save the backup.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")
    
    cmd_parts = ["--site", site_name, "backup"]
    if frappe.utils.cint(with_files):
        cmd_parts.append("--with-files")
    if backup_path and str(backup_path).strip():
        cmd_parts.extend(["--backup-path", str(backup_path).strip()])

    import threading
    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd_parts,
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Backup for '{site_name}' has started."}


@frappe.whitelist()
def toggle_maintenance_mode(site_name, enable):
    """Enable or disable maintenance mode for a site.

    Args:
        site_name (str): Name of the site.
        enable (str): '1' to enable, '0' to disable.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")
    mode = "on" if str(enable) == "1" else "off"

    result = run_bench_command(
        ["--site", site_name, "set-config", "maintenance_mode", "1" if mode == "on" else "0"],
        user=frappe.session.user,
        site=frappe.local.site
    )

    return {
        "status": "success",
        "message": f"Maintenance mode {'enabled' if mode == 'on' else 'disabled'} for '{site_name}'.",
    }


# ─── App Management ─────────────────────────────────────────────────


@frappe.whitelist()
def create_new_app(app_name, title="", description="", publisher="", email=""):
    """Create a new custom Frappe app.

    Args:
        app_name (str): Name of the app (snake_case).
        title (str, optional): Human-readable app title.
        description (str, optional): App description.
        publisher (str, optional): Publisher name.
        email (str, optional): Publisher email.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    app_name = sanitize_input(app_name, "App Name")

    cmd = ["new-app", app_name]

    # bench new-app prompts interactively for:
    # 1. App Title, 2. App Description, 3. App Publisher,
    # 4. App Email, 5. App License (MIT), 6. Create GitHub workflow (n)
    stdin_answers = "\n".join([
        title or app_name,
        description or "Custom App",
        publisher or "Administrator",
        email or "admin@example.com",
        "mit",
        "n",
        "",  # trailing newline
    ])

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "stdin_data": stdin_answers,
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"App creation for '{app_name}' has started."}


@frappe.whitelist()
def get_app(git_url, branch="master"):
    """Get an app from a Git repository URL.

    Args:
        git_url (str): Git repository URL (HTTPS or SSH).
        branch (str, optional): Branch to checkout. Defaults to 'master'.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    git_url = sanitize_git_url(git_url)
    if branch:
        branch = sanitize_input(branch, "Branch")

    cmd = ["get-app", git_url]
    if branch:
        cmd.extend(["--branch", branch])

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Fetching app from '{git_url}' has started."}


@frappe.whitelist()
def install_app(site_name, app_name):
    """Install an app on a specific site.

    Args:
        site_name (str): Target site name.
        app_name (str): App to install.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")
    app_name = sanitize_input(app_name, "App Name")

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": ["--site", site_name, "install-app", app_name],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Installing '{app_name}' on '{site_name}' has started."}


@frappe.whitelist()
def uninstall_app(site_name, app_name):
    """Uninstall an app from a specific site.

    Args:
        site_name (str): Target site name.
        app_name (str): App to uninstall.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")
    app_name = sanitize_input(app_name, "App Name")

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": ["--site", site_name, "uninstall-app", app_name, "--yes"],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Uninstalling '{app_name}' from '{site_name}' has started."}


@frappe.whitelist()
def remove_app(app_name):
    """Remove an app folder entirely from the bench.

    Args:
        app_name (str): Name of the app to remove.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    app_name = sanitize_input(app_name, "App Name")

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": ["remove-app", app_name, "--force"],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Removing app '{app_name}' from bench has started."}


@frappe.whitelist()
def list_apps(site_name=None):
    """List all apps in the bench, optionally filtered by site.

    Args:
        site_name (str, optional): If provided, list apps installed on this site.

    Returns:
        list: List of app information dicts.
    """
    frappe.only_for("System Manager")

    bench_path = get_bench_path()

    if site_name:
        site_name = sanitize_input(site_name, "Site Name")
        apps = _get_site_apps(site_name, bench_path)
        return [{"app_name": a, "installed_on": site_name} for a in apps]

    all_apps = get_app_list()
    app_data = []

    for app in all_apps:
        app_info = {"app_name": app, "git_url": "", "branch": ""}

        # Try to get git info
        app_path = os.path.join(bench_path, "apps", app)
        try:
            import subprocess

            git_exec = shutil.which("git") or "git"

            result = subprocess.run(
                [git_exec, "remote", "get-url", "origin"],
                cwd=app_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                app_info["git_url"] = result.stdout.strip()

            result = subprocess.run(
                [git_exec, "branch", "--show-current"],
                cwd=app_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                app_info["branch"] = result.stdout.strip()
        except Exception as e:
            frappe.logger("bench_manager").error(f"Error fetching git info for app {app}: {e}")
            pass

        app_data.append(app_info)

    return app_data


@frappe.whitelist()
def update_bench():
    """Run bench update to update all apps.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    import threading
    from bench_manager.utils import run_bench_command as _run_bench_command
    thread = threading.Thread(
        target=_run_bench_command,
        kwargs={
            "command_parts": ["update", "--no-backup"],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": "Bench update has started."}


# ─── Bench Management ───────────────────────────────────────────────


@frappe.whitelist()
def get_bench_status():
    """Get comprehensive bench status information.

    Returns:
        dict: Bench status including path, sites count, apps count, etc.
    """
    frappe.only_for("System Manager")

    bench_path = get_bench_path()
    sites = get_site_list()
    apps = get_app_list()

    # Check if bench is in dev mode
    common_config_path = os.path.join(bench_path, "sites", "common_site_config.json")
    dev_mode = False
    try:
        with open(common_config_path, "r") as f:
            config = json.load(f)
            dev_mode = bool(config.get("developer_mode", 0))
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error reading common_site_config.json: {e}")
        pass

    # Check Procfile
    procfile_exists = os.path.exists(os.path.join(bench_path, "Procfile"))

    return {
        "bench_path": bench_path,
        "sites_count": len(sites),
        "apps_count": len(apps),
        "sites": sites,
        "apps": apps,
        "developer_mode": dev_mode,
        "procfile_exists": procfile_exists,
    }


@frappe.whitelist()
def get_bench_version():
    """Get bench, frappe, and Python version information.

    Returns:
        dict: Version information.
    """
    frappe.only_for("System Manager")

    import subprocess

    versions = {}

    # Bench version
    try:
        bench_exec = shutil.which("bench") or "bench"
        result = subprocess.run(
            [bench_exec, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=get_bench_path(),
        )
        versions["bench"] = result.stdout.strip() if result.returncode == 0 else "Unknown"
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error fetching bench version: {e}")
        versions["bench"] = "Unknown"

    # Frappe version
    try:
        versions["frappe"] = frappe.__version__
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error fetching frappe version: {e}")
        versions["frappe"] = "Unknown"

    # Python version
    import sys
    versions["python"] = sys.version.split()[0]

    # Node version
    try:
        node_exec = shutil.which("node") or "node"
        result = subprocess.run(
            [node_exec, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        versions["node"] = result.stdout.strip() if result.returncode == 0 else "Unknown"
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error fetching node version: {e}")
        versions["node"] = "Unknown"

    return versions


@frappe.whitelist()
def bench_migrate_all():
    """Run bench migrate across all sites.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    import threading
    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": ["migrate"],
            "user": frappe.session.user,
            "site": frappe.local.site
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": "Bench migrate (all sites) has started."}


# ─── Site Apps (via bench CLI) ───────────────────────────────────────


@frappe.whitelist()
def get_site_apps(site_name):
    """Get installed apps for a specific site using bench CLI.

    This is more reliable than the file-based approach because it
    queries the actual site database via `bench --site <name> list-apps`.

    Args:
        site_name (str): Name of the site.

    Returns:
        list: List of dicts with app_name, version, and branch.
    """
    frappe.only_for("System Manager")

    import subprocess

    site_name = sanitize_input(site_name, "Site Name")
    bench_path = get_bench_path()

    # Verify site exists
    if not os.path.isdir(os.path.join(bench_path, "sites", site_name)):
        frappe.throw(f"Site '{site_name}' does not exist")

    apps = []
    try:
        bench_exec = shutil.which("bench") or "bench"
        result = subprocess.run(
            [bench_exec, "--site", site_name, "list-apps"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=bench_path,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                app_info = {"app_name": parts[0]}
                if len(parts) > 1:
                    app_info["version"] = parts[1]
                if len(parts) > 2:
                    app_info["branch"] = parts[2]
                apps.append(app_info)
        else:
            # Fallback to file-based approach
            file_apps = _get_site_apps(site_name, bench_path)
            apps = [{"app_name": a} for a in file_apps]
    except Exception as e:
        frappe.logger("bench_manager").error(
            f"Error running list-apps for {site_name}: {e}"
        )
        # Fallback to file-based approach
        file_apps = _get_site_apps(site_name, bench_path)
        apps = [{"app_name": a} for a in file_apps]

    return apps


@frappe.whitelist()
def get_app_sites(app_name, bench_path=None):
    """Get all sites where a specific app is installed.

    Args:
        app_name (str): Name of the app.
        bench_path (str, optional): Target bench to check.

    Returns:
        list: List of site names.
    """
    frappe.only_for("System Manager")
    
    app_name = sanitize_input(app_name, "App Name")
    if bench_path:
        bench_path = _validate_bench_path(bench_path)
    else:
        bench_path = get_bench_path()
        
    sites = _get_bench_sites(bench_path) if 'bench_path' in locals() and bench_path != get_bench_path() else get_site_list()
    if bench_path != get_bench_path():
        from bench_manager.api import list_bench_sites
        sites = [s['site_name'] for s in list_bench_sites(bench_path)]
    installed_sites = []
    from concurrent.futures import ThreadPoolExecutor

    import subprocess
    import shutil
    import os
    
    bench_exec = shutil.which("bench")
    if not bench_exec:
        if os.path.exists("/usr/local/bin/bench"):
            bench_exec = "/usr/local/bin/bench"
        elif os.path.exists(os.path.expanduser("~/.local/bin/bench")):
            bench_exec = os.path.expanduser("~/.local/bin/bench")
        else:
            bench_exec = "bench"

    def check_site(site):
        try:
            result = subprocess.run(
                [bench_exec, "--site", site, "list-apps"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=bench_path,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if not line.strip(): continue
                    if line.split()[0] == app_name:
                        return site
        except Exception as e:
            frappe.logger("bench_manager").error(f"Thread error check_site {site}: {str(e)}")
            pass
        return None

    installed_sites = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(check_site, sites)
        installed_sites = [s for s in results if s is not None]
            
    return installed_sites


# ─── Multi-Bench Management ─────────────────────────────────────────


@frappe.whitelist()
def discover_benches():
    """Discover all bench installations in the parent directory of the current bench.

    Scans the parent directory (e.g., /home/praveen/) for directories
    that look like valid Frappe bench installations (contain 'apps' and 'sites').

    Returns:
        list: List of discovered bench dicts with name, path, version, status, is_host.
    """
    frappe.only_for("System Manager")

    from bench_manager.utils import validate_bench_path

    bench_path = get_bench_path()
    parent_dir = os.path.dirname(bench_path)
    benches = []

    try:
        for item in sorted(os.listdir(parent_dir)):
            candidate = os.path.join(parent_dir, item)
            if not os.path.isdir(candidate):
                continue
            if not validate_bench_path(candidate):
                continue

            is_host = os.path.abspath(candidate) == os.path.abspath(bench_path)

            # Read Frappe version from the installed frappe package
            frappe_version = "Unknown"
            try:
                version_file = os.path.join(candidate, "apps", "frappe", "frappe", "__init__.py")
                if os.path.exists(version_file):
                    with open(version_file, "r") as f:
                        content = f.read()
                        import re as _re
                        match = _re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                        if match:
                            frappe_version = match.group(1)
            except Exception:
                pass

            # Determine status: check if the bench has a running process on its webserver_port
            status = "Offline"
            if is_host:
                status = "Online"
            else:
                try:
                    csc = os.path.join(candidate, "sites", "common_site_config.json")
                    port = 8000
                    if os.path.exists(csc):
                        import json as _json
                        with open(csc) as f:
                            cfg = _json.load(f)
                            port = cfg.get("webserver_port", 8000)
                    import socket
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        if s.connect_ex(('127.0.0.1', int(port))) == 0:
                            status = "Online"
                except Exception:
                    pass

            # Count sites and apps
            sites_count = 0
            apps_count = 0
            try:
                sites_dir = os.path.join(candidate, "sites")
                for s in os.listdir(sites_dir):
                    sp = os.path.join(sites_dir, s)
                    if os.path.isdir(sp) and os.path.exists(os.path.join(sp, "site_config.json")):
                        sites_count += 1
                apps_dir = os.path.join(candidate, "apps")
                for a in os.listdir(apps_dir):
                    if os.path.isdir(os.path.join(apps_dir, a)) and not a.startswith("."):
                        apps_count += 1
            except Exception:
                pass

            benches.append({
                "name": item,
                "path": candidate,
                "version": f"v{frappe_version}" if frappe_version != "Unknown" else "—",
                "status": status,
                "is_host": is_host,
                "sites_count": sites_count,
                "apps_count": apps_count,
            })
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error discovering benches: {e}")

    return benches


@frappe.whitelist()
def init_bench(bench_name, frappe_branch="version-15", site_name="", admin_password="admin", mariadb_root_password=""):
    """Initialize a new Frappe bench, optionally creating a site after.

    Args:
        bench_name (str): Name of the new bench folder.
        frappe_branch (str): Frappe branch to use.
        site_name (str): Optional site to create after init.
        admin_password (str): Admin password for the site.
        mariadb_root_password (str): MariaDB root password.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    bench_name = sanitize_input(bench_name, "Bench Name")
    frappe_branch = sanitize_input(frappe_branch, "Frappe Branch")

    # Validate branch
    allowed_branches = ["version-15", "version-16"]
    if frappe_branch not in allowed_branches:
        frappe.throw(f"Invalid branch. Allowed: {', '.join(allowed_branches)}")

    bench_path = get_bench_path()
    parent_dir = os.path.dirname(bench_path)
    new_bench_path = os.path.join(parent_dir, bench_name)

    if os.path.exists(new_bench_path):
        frappe.throw(f"Directory '{bench_name}' already exists at {parent_dir}")

    # Detect python executable
    import sys
    import shutil
    python_exec = sys.executable  # e.g., /usr/bin/python3

    if frappe_branch == "version-16":
        if shutil.which("python3.12"):
            python_exec = shutil.which("python3.12")
        else:
            frappe.throw("Frappe version-16 strictly requires Python 3.12 or higher. Your system does not have Python 3.12 installed. Please use version-15 instead.")

    # Build the bench init command
    # Note: we run `bench init` directly, not through run_bench_command
    # because `bench init` operates on a NEW directory, not inside an existing bench
    import threading

    def _run_init():
        import pty
        import select
        import errno

        try:
            has_context = bool(getattr(frappe.local, "conf", None))
        except Exception:
            has_context = False
            frappe.local.flags = frappe._dict()

        current_site = frappe.local.site if hasattr(frappe.local, "site") else None
        if not has_context and current_site:
            try:
                frappe.init(current_site)
                frappe.connect()
                frappe.set_user(frappe.session.user if frappe.has_active_session() else "Administrator")
            except Exception:
                pass

        def _publish(msg, msg_type="stdout"):
            try:
                push_sse_event(msg, msg_type)
            except Exception:
                pass
            try:
                frappe.publish_realtime(
                    "bench_console",
                    {"message": msg, "msg_type": msg_type},
                    room="all",
                    after_commit=False,
                )
            except Exception:
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

        bench_exec = shutil.which("bench") or "bench"
        cmd = [bench_exec, "init", new_bench_path, "--frappe-branch", frappe_branch, "--python", python_exec]
        command_str = f"bench init {bench_name} --frappe-branch {frappe_branch} --python {python_exec}"

        _publish(f"$ {command_str}", "command")
        _publish(f"Initializing new bench at {new_bench_path}...", "info")
        _publish(f"Frappe branch: {frappe_branch} | Python: {python_exec}", "info")

        try:
            env = {
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "TERM": "xterm-256color",
                "COLUMNS": "120",
            }

            ansi_re = __import__("re").compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?\x07|\x1b\(B")
            master_fd, slave_fd = pty.openpty()

            process = subprocess.Popen(
                cmd,
                cwd=parent_dir,
                stdin=subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=env,
            )

            os.close(slave_fd)

            output_lines = []
            buffer = ""

            while True:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 1.0)
                except (ValueError, OSError):
                    break

                if not ready:
                    if process.poll() is not None:
                        break
                    continue

                try:
                    data = os.read(master_fd, 4096)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
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

            if buffer.strip():
                output_lines.append(buffer.strip())
                _publish(buffer.strip())

            try:
                os.close(master_fd)
            except OSError:
                pass
            process.wait(timeout=1200)  # 20 min timeout for bench init

            status = "Success" if process.returncode == 0 else "Failed"
            from bench_manager.utils import log_command
            log_command(command_str, "\n".join(output_lines), "", status, "Administrator")

            if status == "Success":
                _publish(f"✓ Bench '{bench_name}' initialized successfully at {new_bench_path}", "success")
                # Chain site creation if requested
                if site_name and "." in site_name:
                    _publish(f"\nNow creating site '{site_name}' on the new bench...", "info")
                    try:
                        site_cmd = [bench_exec, "new-site", site_name, "--admin-password", admin_password]
                        if mariadb_root_password:
                            site_cmd.extend(["--mariadb-root-password", mariadb_root_password])
                        sr = subprocess.run(site_cmd, cwd=new_bench_path, capture_output=True, text=True, timeout=300, env=env)
                        for line in sr.stdout.strip().split("\n"):
                            if line.strip():
                                _publish(line.strip())
                        if sr.returncode == 0:
                            _publish(f"✓ Site '{site_name}' created successfully!", "success")
                            # Set as default site
                            subprocess.run([bench_exec, "use", site_name], cwd=new_bench_path, capture_output=True, timeout=30)
                        else:
                            _publish(f"✕ Site creation failed: {sr.stderr[:300]}", "error")
                    except Exception as se:
                        _publish(f"Site creation error: {str(se)}", "error")
            else:
                _publish(f"✕ Bench initialization failed with exit code {process.returncode}", "error")
                _publish("Cleaning up incomplete bench directory...", "info")
                try:
                    if os.path.exists(new_bench_path):
                        shutil.rmtree(new_bench_path)
                        _publish("Cleanup complete. You can try again.", "info")
                except Exception as ce:
                    _publish(f"Cleanup failed: {str(ce)}", "error")

        except Exception as e:
            _publish(f"Error: {str(e)}", "error")
            from bench_manager.utils import log_command
            log_command(command_str, "", str(e), "Failed", "Administrator")

    thread = threading.Thread(target=_run_init)
    thread.daemon = True
    thread.start()

    msg = f"Bench initialization for '{bench_name}' has started. This may take 5-10 minutes."
    if site_name:
        msg += f" Site '{site_name}' will be created after."

    return {
        "status": "started",
        "message": msg,
        "path": new_bench_path,
    }


# ─── Bench Operations (P0–P3) ───────────────────────────────────────


def _validate_bench_path(bench_path):
    """Validate that bench_path is a real bench and within the allowed parent dir."""
    from bench_manager.utils import validate_bench_path as _vbp
    if not bench_path or not _vbp(bench_path):
        frappe.throw("Invalid bench path")
    host = get_bench_path()
    parent = os.path.dirname(host)
    if not os.path.abspath(bench_path).startswith(os.path.abspath(parent)):
        frappe.throw("Bench path outside allowed directory")
    return os.path.abspath(bench_path)


@frappe.whitelist()
def get_bench_details(bench_path):
    """Get sites, apps, and config for any bench on the server."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    sites, apps = [], []
    # Sites
    sites_dir = os.path.join(bench_path, "sites")
    for item in sorted(os.listdir(sites_dir)):
        sc = os.path.join(sites_dir, item, "site_config.json")
        if os.path.isdir(os.path.join(sites_dir, item)) and os.path.exists(sc):
            try:
                with open(sc) as f:
                    cfg = json.load(f)
                sites.append({"name": item, "db": cfg.get("db_name", ""), "maintenance": bool(cfg.get("maintenance_mode", 0))})
            except Exception:
                sites.append({"name": item, "db": "", "maintenance": False})
    # Apps
    apps_dir = os.path.join(bench_path, "apps")
    for item in sorted(os.listdir(apps_dir)):
        if os.path.isdir(os.path.join(apps_dir, item)) and not item.startswith("."):
            apps.append(item)
    return {"sites": sites, "apps": apps, "path": bench_path}


@frappe.whitelist()
def start_bench(bench_path, port=8000):
    """Start a bench in background with proper daemonization."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    port = int(port)
    bench_name = os.path.basename(bench_path)
    # Check if already running
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return {"status": "already_running", "message": f"{bench_name} is already running on port {port}."}
    except Exception:
        pass
    
    # Check if Procfile exists
    if not os.path.exists(os.path.join(bench_path, "Procfile")):
        return {"status": "error", "message": f"Procfile not found. '{bench_name}' might be broken or incomplete."}
    # Set ports in common_site_config.json so bench start uses them
    csc_path = os.path.join(bench_path, "sites", "common_site_config.json")
    try:
        with open(csc_path) as f:
            config = json.load(f)
            
        def get_free_port(start_port):
            import socket
            p = int(start_port)
            while True:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    if s.connect_ex(('127.0.0.1', p)) != 0:
                        return p
                p += 1

        config["webserver_port"] = get_free_port(port)
        
        if "socketio_port" in config:
            config["socketio_port"] = get_free_port(config["socketio_port"])
            
        if "file_watcher_port" in config:
            config["file_watcher_port"] = get_free_port(config["file_watcher_port"])
            
        if "redis_cache" in config:
            current_port = int(config["redis_cache"].split(":")[-1])
            new_port = get_free_port(current_port)
            config["redis_cache"] = f"redis://127.0.0.1:{new_port}"
            config["redis_socketio"] = f"redis://127.0.0.1:{new_port}"
            
        if "redis_queue" in config:
            current_port = int(config["redis_queue"].split(":")[-1])
            new_port = get_free_port(current_port)
            config["redis_queue"] = f"redis://127.0.0.1:{new_port}"

        with open(csc_path, "w") as f:
            json.dump(config, f, indent=2)
            
        # Remove existing Procfile to bypass the interactive overwrite prompt
        procfile_path = os.path.join(bench_path, "Procfile")
        if os.path.exists(procfile_path):
            os.remove(procfile_path)
            
        bench_exec = shutil.which("bench") or "bench"
        # Regenerate configs so Procfile and redis confs use the new free ports
        subprocess.run([bench_exec, "setup", "redis"], cwd=bench_path, check=False)
        subprocess.run([bench_exec, "setup", "socketio"], cwd=bench_path, check=False)
        subprocess.run([bench_exec, "setup", "procfile"], cwd=bench_path, check=False)
    except Exception:
        pass
    # Start bench with proper process group detachment
    log_file = os.path.join(bench_path, "logs", "bench_start.log")
    os.makedirs(os.path.join(bench_path, "logs"), exist_ok=True)
    
    # Use full path to bench if possible
    bench_exec = shutil.which("bench") or "bench"
    
    # Build a clean env: remove werkzeug FD/reloader vars to prevent the child
    # bench's werkzeug from inheriting a stale socket FD from the host bench.
    # WERKZEUG_SERVER_FD: host bench's socket FD — invalid in the child process.
    # WERKZEUG_RUN_MAIN: prevents werkzeug from thinking it's a reloader child.
    start_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    start_env.pop("WERKZEUG_SERVER_FD", None)
    start_env.pop("WERKZEUG_RUN_MAIN", None)
    
    # Open the log file *without* a with-block. The FD must stay open for the
    # lifetime of the detached child process tree (honcho → redis/web/worker).
    # Closing it prematurely (as `with` would) invalidates stdout for all
    # children and causes "Bad file descriptor" crashes.
    lf = open(log_file, "a")
    lf.write(f"\n--- Starting bench at {time.ctime()} on port {port} ---\n")
    lf.flush()
    proc = subprocess.Popen(
        [bench_exec, "start"],
        cwd=bench_path,
        stdout=lf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=False,          # let child inherit the log FD
        start_new_session=True,
        env=start_env,
    )
    # Detach the log FD from this process — the OS keeps it open for the child
    # via the dup'd descriptor in the Popen internals.
    lf.close()
    # Check if process failed immediately
    time.sleep(3.0)  # Wait longer to ensure everything (Redis, Web, etc.) started
    if proc.poll() is not None:
        error_detail = ""
        try:
            if os.path.exists(log_file):
                with open(log_file) as f:
                    log_content = f.read()
                    if log_content:
                        # Get the last 10 lines for better context
                        lines = log_content.strip().split("\n")
                        error_detail = "\n".join(lines[-10:])
        except Exception:
            pass
        
        return {
            "status": "error",
            "message": f"{bench_name} failed to start (exit code {proc.returncode}).",
            "detail": error_detail
        }
    return {"status": "started", "message": f"{bench_name} starting on port {port}...", "port": port}


@frappe.whitelist()
def stop_bench(bench_path):
    """Stop a running bench by killing its honcho process tree and cleaning up ports."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    bench_name = os.path.basename(bench_path)
    
    # Get ports to clean up later if needed
    ports = []
    csc_path = os.path.join(bench_path, "sites", "common_site_config.json")
    if os.path.exists(csc_path):
        try:
            with open(csc_path) as f:
                cfg = json.load(f)
                ports.append(cfg.get("webserver_port", 8000))
                # Add Redis ports from config strings like "redis://127.0.0.1:13000"
                for key in ["redis_cache", "redis_queue", "redis_socketio"]:
                    val = cfg.get(key)
                    if val and ":" in val:
                        try:
                            ports.append(int(val.split(":")[-1]))
                        except: pass
        except: pass

    try:
        # 1. Kill Honcho
        r = subprocess.run(["pgrep", "-f", f"honcho.*{bench_name}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            pids = r.stdout.strip().split("\n")
            for pid in pids:
                subprocess.run(["kill", "-TERM", pid.strip()], timeout=5)
        
        # 2. Wait and check ports, kill anything still lingering
        time.sleep(1.0)
        
        for port in set(ports):
            try:
                # Use fuser to kill anything on these ports
                subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=5)
            except:
                pass

        return {"status": "stopped", "message": f"{bench_name} stopped and ports cleared."}
    except Exception as e:
        frappe.throw(f"Failed to stop bench: {e}")


@frappe.whitelist()
def delete_bench(bench_path, confirm="no"):
    """Delete a bench directory entirely."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    host = get_bench_path()
    if os.path.abspath(bench_path) == os.path.abspath(host):
        frappe.throw("Cannot delete the host bench (the one running this app).")
    if confirm != "yes":
        frappe.throw("Must confirm deletion with confirm='yes'")
    bench_name = os.path.basename(bench_path)
    # Stop first
    try:
        stop_bench(bench_path)
    except Exception:
        pass
    import shutil as _shutil
    _shutil.rmtree(bench_path, ignore_errors=True)
    return {"status": "deleted", "message": f"Bench '{bench_name}' deleted."}


@frappe.whitelist()
def backup_all_bench_sites(bench_path):
    """Backup all sites on a given bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    bench_name = os.path.basename(bench_path)
    bench_exec = shutil.which("bench") or "bench"
    # Discover sites
    sites = []
    sites_dir = os.path.join(bench_path, "sites")
    for item in sorted(os.listdir(sites_dir)):
        if os.path.isdir(os.path.join(sites_dir, item)) and os.path.exists(os.path.join(sites_dir, item, "site_config.json")):
            sites.append(item)
    if not sites:
        return {"status": "empty", "message": f"No sites found on {bench_name}."}

    import threading
    def _run():
        from bench_manager.utils import log_command
        try:
            frappe.init(frappe.local.site)
            frappe.connect()
        except Exception:
            pass
        def _pub(msg, t="stdout"):
            try:
                push_sse_event(msg, t)
            except Exception:
                pass
            try:
                frappe.publish_realtime("bench_console", {"message": msg, "msg_type": t}, room="all", after_commit=False)
            except Exception:
                pass
        _pub(f"Backing up {len(sites)} sites on {bench_name}...", "command")
        results = []
        for i, site in enumerate(sites):
            _pub(f"[{i+1}/{len(sites)}] Backing up {site}...", "stdout")
            try:
                r = subprocess.run([bench_exec, "--site", site, "backup", "--with-files"], cwd=bench_path, capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    _pub(f"  ✓ {site} backed up", "stdout")
                    results.append({"site": site, "status": "ok"})
                else:
                    _pub(f"  ✕ {site} failed: {r.stderr[:200]}", "stderr")
                    results.append({"site": site, "status": "failed"})
            except Exception as e:
                _pub(f"  ✕ {site} error: {str(e)[:200]}", "stderr")
                results.append({"site": site, "status": "error"})
        ok = sum(1 for r in results if r["status"] == "ok")
        _pub(f"Backup complete: {ok}/{len(sites)} sites backed up successfully.", "success" if ok == len(sites) else "error")
        log_command(f"backup-all ({bench_name})", f"{ok}/{len(sites)} succeeded", "", "Success" if ok == len(sites) else "Failed", "Administrator")
    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    return {"status": "started", "message": f"Backup of {len(sites)} sites on {bench_name} started."}


@frappe.whitelist()
def update_remote_bench(bench_path):
    """Run bench update on a remote bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    bench_name = os.path.basename(bench_path)
    import threading
    def _run():
        from bench_manager.utils import log_command
        try:
            frappe.init(frappe.local.site)
            frappe.connect()
        except Exception:
            pass
        def _pub(msg, t="stdout"):
            try:
                push_sse_event(msg, t)
            except Exception:
                pass
            try:
                frappe.publish_realtime("bench_console", {"message": msg, "msg_type": t}, room="all", after_commit=False)
            except Exception:
                pass
        bench_exec = shutil.which("bench") or "bench"
        cmd = [bench_exec, "update", "--no-backup"]
        _pub(f"$ bench update --no-backup  (in {bench_name})", "command")
        try:
            r = subprocess.run(cmd, cwd=bench_path, capture_output=True, text=True, timeout=600)
            for line in r.stdout.strip().split("\n"):
                if line.strip():
                    _pub(line.strip())
            if r.returncode == 0:
                _pub(f"Bench update completed for {bench_name}.", "success")
            else:
                _pub(f"Bench update failed for {bench_name}.", "error")
            log_command(f"bench update ({bench_name})", r.stdout[:5000], r.stderr[:2000], "Success" if r.returncode == 0 else "Failed", "Administrator")
        except Exception as e:
            _pub(f"Error: {str(e)}", "error")
    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    return {"status": "started", "message": f"Bench update started for {bench_name}."}


@frappe.whitelist()
def bench_health_check(bench_path):
    """Get health info: disk usage, venv status, last update."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    info = {"path": bench_path, "name": os.path.basename(bench_path)}
    # Disk usage
    try:
        r = subprocess.run(["du", "-sh", bench_path], capture_output=True, text=True, timeout=30)
        info["disk_usage"] = r.stdout.split()[0] if r.returncode == 0 else "Unknown"
    except Exception:
        info["disk_usage"] = "Unknown"
    # Venv status
    venv_python = os.path.join(bench_path, "env", "bin", "python")
    info["venv_valid"] = os.path.exists(venv_python)
    # Python version in venv
    try:
        r = subprocess.run([venv_python, "--version"], capture_output=True, text=True, timeout=5)
        info["python_version"] = r.stdout.strip() if r.returncode == 0 else "Unknown"
    except Exception:
        info["python_version"] = "Unknown"
    # Node version
    try:
        node_exec = os.path.join(bench_path, "env", "bin", "node")
        if not os.path.exists(node_exec):
            node_exec = shutil.which("node") or "node"
        r = subprocess.run([node_exec, "--version"], capture_output=True, text=True, timeout=5)
        info["node_version"] = r.stdout.strip() if r.returncode == 0 else "Unknown"
    except Exception:
        info["node_version"] = "Unknown"
    # Procfile exists
    info["procfile"] = os.path.exists(os.path.join(bench_path, "Procfile"))
    # Sites count
    sites_dir = os.path.join(bench_path, "sites")
    info["sites_count"] = sum(1 for s in os.listdir(sites_dir) if os.path.isdir(os.path.join(sites_dir, s)) and os.path.exists(os.path.join(sites_dir, s, "site_config.json")))
    # Apps count
    apps_dir = os.path.join(bench_path, "apps")
    info["apps_count"] = sum(1 for a in os.listdir(apps_dir) if os.path.isdir(os.path.join(apps_dir, a)) and not a.startswith("."))
    return info


@frappe.whitelist()
def sync_app_to_bench(app_name, target_bench_path):
    """Install an app from the host bench onto another bench using local path."""
    frappe.only_for("System Manager")
    target_bench_path = _validate_bench_path(target_bench_path)
    app_name = sanitize_input(app_name, "App Name")
    host = get_bench_path()
    app_source = os.path.join(host, "apps", app_name)
    if not os.path.isdir(app_source):
        frappe.throw(f"App '{app_name}' not found in host bench.")
    bench_exec = shutil.which("bench") or "bench"
    target_name = os.path.basename(target_bench_path)
    import threading
    def _run():
        from bench_manager.utils import log_command
        try:
            frappe.init(frappe.local.site)
            frappe.connect()
        except Exception:
            pass
        def _pub(msg, t="stdout"):
            try:
                push_sse_event(msg, t)
            except Exception:
                pass
            try:
                frappe.publish_realtime("bench_console", {"message": msg, "msg_type": t}, room="all", after_commit=False)
            except Exception:
                pass
        _pub(f"$ bench get-app {app_source}  (target: {target_name})", "command")
        try:
            r = subprocess.run([bench_exec, "get-app", app_source], cwd=target_bench_path, capture_output=True, text=True, timeout=600)
            for line in r.stdout.strip().split("\n"):
                if line.strip():
                    _pub(line.strip())
            if r.returncode == 0:
                _pub(f"App '{app_name}' synced to {target_name}.", "success")
            else:
                _pub(f"Failed to sync app: {r.stderr[:300]}", "error")
            log_command(f"sync-app {app_name} -> {target_name}", r.stdout[:5000], r.stderr[:2000], "Success" if r.returncode == 0 else "Failed", "Administrator")
        except Exception as e:
            _pub(f"Error: {str(e)}", "error")
    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    return {"status": "started", "message": f"Syncing '{app_name}' to {target_name}..."}


@frappe.whitelist()
def create_site_on_bench(bench_path, site_name, admin_password="admin", mariadb_root_password=""):
    """Create a new site on any bench with realtime output."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    bench_name = os.path.basename(bench_path)

    # Validate site name has a dot (e.g., mysite.localhost)
    if "." not in site_name:
        frappe.throw("Site name must contain a dot (e.g., mysite.localhost)")

    # Check site doesn't already exist
    site_dir = os.path.join(bench_path, "sites", site_name)
    if os.path.exists(site_dir):
        frappe.throw(f"Site '{site_name}' already exists on {bench_name}")

    import threading

    def _run():
        from bench_manager.utils import log_command
        try:
            frappe.init(frappe.local.site)
            frappe.connect()
        except Exception:
            pass

        def _pub(msg, t="stdout"):
            try:
                push_sse_event(msg, t)
            except Exception:
                pass
            try:
                frappe.publish_realtime("bench_console", {"message": msg, "msg_type": t}, room="all", after_commit=False)
            except Exception:
                pass

        bench_exec = shutil.which("bench") or "bench"
        cmd = [bench_exec, "new-site", site_name, "--admin-password", admin_password]
        if mariadb_root_password:
            cmd.extend(["--mariadb-root-password", mariadb_root_password])

        command_str = f"bench new-site {site_name} (on {bench_name})"
        _pub(f"$ {command_str}", "command")
        _pub(f"Creating site {site_name} on {bench_name}...", "info")

        try:
            import pty, select, errno
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "xterm-256color", "COLUMNS": "120"}
            ansi_re = __import__("re").compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?\x07|\x1b\(B")
            master_fd, slave_fd = pty.openpty()

            process = subprocess.Popen(
                cmd, cwd=bench_path, stdin=subprocess.DEVNULL,
                stdout=slave_fd, stderr=slave_fd, close_fds=True, env=env,
            )
            os.close(slave_fd)
            output_lines = []
            buffer = ""

            while True:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 1.0)
                except (ValueError, OSError):
                    break
                if not ready:
                    if process.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(master_fd, 4096)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                text = ansi_re.sub("", data.decode("utf-8", errors="replace"))
                for char in text:
                    if char in ("\n", "\r"):
                        clean = buffer.strip()
                        if clean:
                            output_lines.append(clean)
                            _pub(clean)
                        buffer = ""
                    else:
                        buffer += char

            if buffer.strip():
                output_lines.append(buffer.strip())
                _pub(buffer.strip())

            try:
                os.close(master_fd)
            except OSError:
                pass
            process.wait(timeout=300)

            status = "Success" if process.returncode == 0 else "Failed"
            log_command(command_str, "\n".join(output_lines), "", status, "Administrator")
            if status == "Success":
                _pub(f"✓ Site '{site_name}' created on {bench_name}.", "success")
            else:
                _pub(f"✕ Site creation failed.", "error")
        except Exception as e:
            _pub(f"Error: {str(e)}", "error")
            log_command(command_str, "", str(e), "Failed", "Administrator")

    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    return {"status": "started", "message": f"Creating site '{site_name}' on {bench_name}..."}


@frappe.whitelist()
def get_bench_port(bench_path):
    """Get the configured webserver port for a bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    csc_path = os.path.join(bench_path, "sites", "common_site_config.json")
    port = 8000
    try:
        with open(csc_path) as f:
            config = json.load(f)
        port = config.get("webserver_port", 8000)
    except Exception:
        pass
    return {"port": port}


@frappe.whitelist()
def set_default_site_on_bench(bench_path, site_name):
    """Set a site as the default on a bench.

    Writes both currentsite.txt and default_site in common_site_config.json
    so that Frappe serves this site regardless of the hostname in the request.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    bench_name = os.path.basename(bench_path)
    # Verify site exists
    site_dir = os.path.join(bench_path, "sites", site_name)
    if not os.path.isdir(site_dir):
        frappe.throw(f"Site '{site_name}' does not exist on {bench_name}")

    sites_path = os.path.join(bench_path, "sites")
    errors = []

    # 1. Write currentsite.txt
    try:
        with open(os.path.join(sites_path, "currentsite.txt"), "w") as f:
            f.write(site_name)
    except Exception as e:
        errors.append(f"currentsite.txt: {e}")

    # 2. Update default_site and hostname mappings in common_site_config.json
    csc_path = os.path.join(sites_path, "common_site_config.json")
    try:
        with open(csc_path) as f:
            config = json.load(f)

        config["default_site"] = site_name

        # Build hostname mappings for multi-tenancy:
        # Each site gets a {name}.localhost entry so Frappe routes by Host header.
        # Do NOT map localhost/127.0.0.1 — that would hijack all traffic
        # and break the bench manager site.
        domains = config.get("domains", {})
        site_map = config.get("sites", {})

        # Add mapping for the target site
        _ensure_site_hostname_mapping(site_name, domains, site_map)

        # Also ensure mappings exist for ALL other sites so multi-tenancy works
        for entry in os.listdir(sites_path):
            entry_path = os.path.join(sites_path, entry)
            if os.path.isdir(entry_path) and entry not in (
                "assets", "__pycache__", ".cache"
            ):
                _ensure_site_hostname_mapping(entry, domains, site_map)

        config["domains"] = domains
        config["sites"] = site_map

        with open(csc_path, "w") as f:
            json.dump(config, f, indent=1)
    except Exception as e:
        errors.append(f"common_site_config.json: {e}")

    if errors:
        return {"status": "error", "message": "Failed: " + "; ".join(errors)}

    # Determine the URL for opening the site
    port = 8000
    try:
        with open(csc_path) as f:
            cfg = json.load(f)
            port = cfg.get("webserver_port", 8000)
    except Exception:
        pass

    hostname = f"{site_name}.localhost" if not site_name.endswith(".localhost") else site_name

    return {
        "status": "success",
        "message": f"'{site_name}' set as default on {bench_name}.",
        "open_url": f"http://{hostname}:{port}",
    }


@frappe.whitelist()
def install_app_on_site_remote(bench_path, site_name, app_name):
    """Install an app on a specific site of a remote bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    app_name = sanitize_input(app_name, "App Name")
    bench_name = os.path.basename(bench_path)

    cmd = ["--site", site_name, "install-app", app_name]
    
    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()
    return {"status": "started", "message": f"Installing '{app_name}' on {site_name}..."}


@frappe.whitelist()
def uninstall_app_from_site_remote(bench_path, site_name, app_name):
    """Uninstall an app from a specific site of a remote bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    app_name = sanitize_input(app_name, "App Name")
    bench_name = os.path.basename(bench_path)

    cmd = ["--site", site_name, "uninstall-app", app_name, "--yes"]

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()
    return {"status": "started", "message": f"Uninstalling '{app_name}' from {site_name}..."}


@frappe.whitelist()
def remove_app_remote(bench_path, app_name):
    """Remove an app from a remote bench."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    app_name = sanitize_input(app_name, "App Name")
    bench_name = os.path.basename(bench_path)

    cmd = ["remove-app", app_name, "--force"]

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()
    return {"status": "started", "message": f"Removing '{app_name}' from {bench_name}..."}


@frappe.whitelist()
def get_app_compatibility(app_name, target_bench_path):
    """Check if an app from the host bench is compatible with the target bench version."""
    frappe.only_for("System Manager")
    target_bench_path = _validate_bench_path(target_bench_path)
    host_bench = get_bench_path()
    host_name = os.path.basename(host_bench)
    target_name = os.path.basename(target_bench_path)

    # Get host bench Frappe version
    def _get_frappe_version(bp):
        try:
            ver_file = os.path.join(bp, "apps", "frappe", "frappe", "__init__.py")
            with open(ver_file) as f:
                for line in f:
                    if line.startswith("__version__"):
                        return line.split("=")[1].strip().strip("'\"")
        except Exception:
            pass
        return "unknown"

    def _get_major(ver):
        try:
            return int(ver.split(".")[0])
        except Exception:
            return 0

    host_ver = _get_frappe_version(host_bench)
    target_ver = _get_frappe_version(target_bench_path)
    host_major = _get_major(host_ver)
    target_major = _get_major(target_ver)

    # Check if the app exists on host
    app_path = os.path.join(host_bench, "apps", app_name)
    if not os.path.isdir(app_path):
        frappe.throw(f"App '{app_name}' not found on host bench")

    compatible = (host_major == target_major)
    return {
        "compatible": compatible,
        "host_version": host_ver,
        "target_version": target_ver,
        "host_major": host_major,
        "target_major": target_major,
        "host_bench": host_name,
        "target_bench": target_name,
    }


# ─── Multi-Bench Context APIs ───────────────────────────────────────


@frappe.whitelist()
def list_bench_sites(bench_path):
    """List all sites on any bench, with status and apps info.

    Args:
        bench_path (str): Path to the target bench.

    Returns:
        list: List of site dicts with site_name, status, apps, is_current.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    sites_dir = os.path.join(bench_path, "sites")
    site_data = []

    # Read current/default site from common_site_config.json
    current_site = None
    csc_path = os.path.join(sites_dir, "common_site_config.json")
    if os.path.exists(csc_path):
        try:
            with open(csc_path, "r") as f:
                config = json.load(f)
                current_site = config.get("default_site") or None
                for key in ("domains", "sites"):
                    mapping = config.get(key, {})
                    for hostname, mapped_site in mapping.items():
                        if hostname in ("localhost", "127.0.0.1"):
                            current_site = mapped_site
                            break
        except Exception:
            pass

    try:
        for item in sorted(os.listdir(sites_dir)):
            site_dir = os.path.join(sites_dir, item)
            site_config_path = os.path.join(site_dir, "site_config.json")
            if not os.path.isdir(site_dir) or not os.path.exists(site_config_path):
                continue

            status = "Inactive"
            try:
                with open(site_config_path, "r") as f:
                    sc = json.load(f)
                    if sc.get("maintenance_mode", 0):
                        status = "Maintenance"
                    elif item == current_site:
                        status = "Active"
            except Exception:
                status = "Unknown"

            apps = []
            try:
                apps = _get_site_apps(item, bench_path)
            except Exception:
                pass

            site_data.append({
                "site_name": item,
                "status": status,
                "is_current": item == current_site,
                "apps": apps,
            })
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error listing sites on {bench_path}: {e}")

    return site_data


@frappe.whitelist()
def list_bench_apps(bench_path):
    """List all apps on any bench with git info.

    Args:
        bench_path (str): Path to the target bench.

    Returns:
        list: List of app dicts with app_name, git_url, branch.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    apps_dir = os.path.join(bench_path, "apps")
    app_data = []

    try:
        for item in sorted(os.listdir(apps_dir)):
            app_path = os.path.join(apps_dir, item)
            if not os.path.isdir(app_path) or item.startswith("."):
                continue

            app_info = {"app_name": item, "git_url": "", "branch": ""}

            try:
                git_exec = shutil.which("git") or "git"
                result = subprocess.run(
                    [git_exec, "remote", "get-url", "origin"],
                    cwd=app_path, capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    app_info["git_url"] = result.stdout.strip()

                result = subprocess.run(
                    [git_exec, "branch", "--show-current"],
                    cwd=app_path, capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    app_info["branch"] = result.stdout.strip()
            except Exception:
                pass

            app_data.append(app_info)
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error listing apps on {bench_path}: {e}")

    return app_data


@frappe.whitelist()
def get_app_on_bench(bench_path, git_url, branch="master"):
    """Clone an app from a Git repository onto any bench.

    Args:
        bench_path (str): Path to the target bench.
        git_url (str): Git repository URL (HTTPS or SSH).
        branch (str, optional): Branch to checkout.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    git_url = sanitize_git_url(git_url)
    if branch:
        branch = sanitize_input(branch, "Branch")

    bench_name = os.path.basename(bench_path)
    bench_name = os.path.basename(bench_path)
    cmd = ["get-app", git_url]
    if branch:
        cmd.extend(["--branch", branch])

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Cloning app onto {bench_name}..."}


@frappe.whitelist()
def create_new_app_on_bench(bench_path, app_name, title="", description="", publisher="", email=""):
    """Create a new Frappe app on any bench.

    Args:
        bench_path (str): Path to the target bench.
        app_name (str): Name of the new app (snake_case).
        title, description, publisher, email: Optional metadata.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    app_name = sanitize_input(app_name, "App Name")
    bench_name = os.path.basename(bench_path)

    # Check if app already exists
    if os.path.exists(os.path.join(bench_path, "apps", app_name)):
        frappe.throw(f"App '{app_name}' already exists on {bench_name}")

    cmd = ["new-app", "--no-git", app_name]
    input_text = f"{title or app_name}\n{description or ''}\n{publisher or ''}\n{email or ''}\n"

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "stdin_data": input_text,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Creating app '{app_name}' on {bench_name}..."}


@frappe.whitelist()
def create_site_on_bench(bench_path, site_name, admin_password, db_password=None):
    """Create a new Frappe site on any bench.

    Args:
        bench_path (str): Path to the target bench.
        site_name (str): Name of the site to create.
        admin_password (str): Administrator password for the new site.
        db_password (str, optional): Database root password.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    bench_name = os.path.basename(bench_path)

    if not admin_password:
        frappe.throw("Admin password is required")

    cmd = ["new-site", site_name, "--admin-password", admin_password]
    if db_password:
        cmd.extend(["--db-root-password", db_password])

    import threading
    from bench_manager.utils import run_bench_command

    thread = threading.Thread(
        target=run_bench_command,
        kwargs={
            "command_parts": cmd,
            "bench_path": bench_path,
            "user": frappe.session.user,
            "site": getattr(frappe.local, "site", None)
        }
    )
    thread.daemon = True
    thread.start()

    return {"status": "started", "message": f"Creating site '{site_name}' on {bench_name}..."}


# ─── Logs ────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_command_logs(limit=50):
    """Get recent command execution logs.

    Args:
        limit (int): Maximum number of logs to return. Default 50.

    Returns:
        list: List of log entries.
    """
    frappe.only_for("System Manager")

    try:
        limit = min(int(limit), 200)
    except (ValueError, TypeError):
        limit = 50

    logs = frappe.get_all(
        "Bench Command Log",
        fields=["name", "command", "status", "executed_by", "creation", "output", "error"],
        order_by="creation desc",
        limit_page_length=limit,
    )

    return logs


@frappe.whitelist()
def clear_logs():
    """Clear all command logs.

    Returns:
        dict: Status message.
    """
    frappe.only_for("System Manager")

    frappe.db.delete("Bench Command Log")
    frappe.db.commit()

    return {"status": "success", "message": "All command logs cleared."}


# ─── SSE (Server-Sent Events) for Live Activity ─────────────────────


# In-memory ring buffer for SSE events (shared across threads)
_sse_buffer = []
_sse_buffer_max = 500
_sse_event_id = 0
import threading
_sse_lock = threading.Lock()


def push_sse_event(message, msg_type="stdout"):
    """Push an event to the SSE buffer for live streaming.
    Called from background threads to publish log lines.
    """
    global _sse_event_id
    with _sse_lock:
        _sse_event_id += 1
        event = {
            "id": _sse_event_id,
            "time": time.strftime("%H:%M:%S"),
            "message": message,
            "msg_type": msg_type,
        }
        _sse_buffer.append(event)
        if len(_sse_buffer) > _sse_buffer_max:
            _sse_buffer.pop(0)


@frappe.whitelist()
def get_sse_events(last_id=0):
    """Get SSE events since a given event ID (polling fallback).

    Args:
        last_id (int): Last event ID the client has seen.

    Returns:
        dict: New events and latest event ID.
    """
    frappe.only_for("System Manager")
    try:
        last_id = int(last_id)
    except (ValueError, TypeError):
        last_id = 0

    with _sse_lock:
        new_events = [e for e in _sse_buffer if e["id"] > last_id]

    return {
        "events": new_events,
        "last_id": new_events[-1]["id"] if new_events else last_id,
    }


@frappe.whitelist()
def stream_bench_log(bench_path, lines=50):
    """Read the last N lines of a bench's startup log file.

    Args:
        bench_path (str): Path to the bench.
        lines (int): Number of lines to return (default 50).

    Returns:
        dict: Log content and metadata.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    log_file = os.path.join(bench_path, "logs", "bench_start.log")

    try:
        lines = min(int(lines), 500)
    except (ValueError, TypeError):
        lines = 50

    if not os.path.exists(log_file):
        return {"content": "", "exists": False, "lines": 0}

    try:
        with open(log_file, "r") as f:
            all_lines = f.readlines()
            tail = all_lines[-lines:]
            return {
                "content": "".join(tail),
                "exists": True,
                "lines": len(tail),
                "total_lines": len(all_lines),
            }
    except Exception as e:
        return {"content": f"Error reading log: {e}", "exists": True, "lines": 0}


# ─── VS Code Editor (code-server) ──────────────────────────────────


@frappe.whitelist()
def get_running_vscode_instances():
    """Get all running code-server instances on the host."""
    frappe.only_for("System Manager")
    
    instances = []
    try:
        import subprocess
        result = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "code-server" in line and "--bind-addr" in line:
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        pid_str, cmd = parts
                        pid = int(pid_str)
                        port = None
                        bench_path = None
                        
                        cmd_parts = cmd.split()
                        for i, part in enumerate(cmd_parts):
                            if part == "--bind-addr" and i + 1 < len(cmd_parts):
                                addr = cmd_parts[i+1]
                                if ":" in addr:
                                    port = int(addr.split(":")[1])
                        
                        if cmd_parts:
                            bench_path = cmd_parts[-1]
                        
                        if port and bench_path and not bench_path.startswith("-"):
                            bench_name = os.path.basename(bench_path)
                            host = frappe.utils.get_url().split('//')[-1].split(':')[0]
                            if host in ["localhost", "127.0.0.1", "0.0.0.0"]:
                                host = "127.0.0.1" # fallback to be replaced by frontend
                            instances.append({
                                "pid": pid,
                                "port": port,
                                "bench_path": bench_path,
                                "bench_name": bench_name,
                                "url": f"http://{host}:{port}/?folder={bench_path}",
                                "status": "running"
                            })
    except Exception as e:
        frappe.logger("bench_manager").error(f"Error fetching vscode instances: {e}")
    
    return instances


@frappe.whitelist()
def stop_code_server(pid):
    """Stop a running code-server instance by PID."""
    frappe.only_for("System Manager")
    
    try:
        pid = int(pid)
        os.kill(pid, 9)
        return {"status": "success", "message": f"VS Code editor stopped successfully (PID: {pid})."}
    except ProcessLookupError:
        return {"status": "success", "message": f"VS Code editor (PID: {pid}) was already stopped."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to stop VS Code editor: {e}"}


@frappe.whitelist()
def launch_code_server(bench_path, port=9002):
    """Launch code-server (VS Code in browser) for a given bench directory.

    Starts code-server bound to 127.0.0.1 on the specified port, opening
    the bench directory as the workspace. Requires code-server to be
    installed on the system.

    Args:
        bench_path (str): Path to the bench directory to open.
        port (int): Port to run code-server on (1024-65535). Defaults to 9002.

    Returns:
        dict: Status and URL to access the editor.
    """
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)

    # Check if this bench is already running a code-server
    running_instances = get_running_vscode_instances()
    for instance in running_instances:
        if instance["bench_path"] == bench_path:
            return {
                "status": "already_running",
                "message": f"Editor is already running for {instance['bench_name']}.",
                "url": instance["url"],
                "port": instance["port"],
                "pid": instance["pid"]
            }

    # Find an available port if default is taken or not specified properly
    import socket
    
    def is_port_in_use(p):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return False
            except socket.error:
                return True
            
    try:
        port = int(port)
    except (ValueError, TypeError):
        port = 9002
        
    if port < 1024 or port > 65535:
        port = 9002

    while is_port_in_use(port) and port < 9100:
        port += 1
        
    if port >= 9100:
         return {
            "status": "error",
            "message": "Could not find an available port for code-server.",
         }

    # Check if code-server is installed
    code_server_exec = shutil.which("code-server")
    if not code_server_exec:
        local_bin_path = os.path.expanduser("~/.local/bin/code-server")
        if os.path.exists(local_bin_path):
            code_server_exec = local_bin_path
        else:
            return {
                "status": "not_installed",
                "message": (
                    "code-server is not installed. Install it with:\n"
                    "curl -fsSL https://code-server.dev/install.sh | sh"
                ),
            }

    user_data_dir = os.path.join(bench_path, ".vscode-server-data")
    
    # Use --config /dev/null to prevent ~/.config/code-server/config.yaml
    # from overriding CLI args (e.g. bind-addr, auth)
    cmd = [
        code_server_exec,
        "--config", "/dev/null",
        "--bind-addr", f"0.0.0.0:{port}",
        "--auth", "none",
        "--disable-telemetry",
        "--user-data-dir", user_data_dir,
        bench_path,
    ]

    env = os.environ.copy()
    env["HOME"] = "/home/frappe"
    env["USER"] = "frappe"
    # Strip VS Code / Electron env vars that cause code-server to detect
    # the running IDE and delegate to it via IPC socket (then exit silently)
    for key in list(env.keys()):
        if (
            key.startswith("VSCODE_")
            or key.startswith("ELECTRON_")
            or key == "TERM_PROGRAM"
            or key == "PORT"
        ):
            del env[key]

    local_bin = os.path.expanduser("~/.local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin")

    import tempfile
    stderr_path = os.path.join(
        tempfile.gettempdir(), f"code-server-{port}-stderr.log"
    )

    try:
        stderr_file = open(stderr_path, "w")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )

        # Brief check: wait a moment to see if the process exited immediately
        # (e.g. due to IPC delegation to an existing VS Code instance)
        time.sleep(2)
        exit_code = process.poll()
        if exit_code is not None:
            stderr_file.close()
            # Read stderr for diagnostics
            stderr_output = ""
            try:
                with open(stderr_path, "r") as f:
                    stderr_output = f.read(2000).strip()
            except Exception:
                pass

            frappe.logger("bench_manager").warning(
                f"code-server exited with code {exit_code}: {stderr_output}"
            )
            return {
                "status": "error",
                "message": (
                    f"VS Code editor exited immediately (code {exit_code}). "
                    f"{stderr_output or 'No error details available.'}"
                ),
            }

        bench_name = os.path.basename(bench_path)
        frappe.logger("bench_manager").info(
            f"code-server launched for '{bench_name}' on port {port} (PID: {process.pid})"
        )

        host = frappe.utils.get_url().split('//')[-1].split(':')[0]
        if host in ["localhost", "127.0.0.1", "0.0.0.0"]:
            host = "127.0.0.1"

        return {
            "status": "launching",
            "message": f"VS Code editor launching for '{bench_name}' on port {port}...",
            "url": f"http://{host}:{port}/?folder={bench_path}",
            "port": port,
            "pid": process.pid,
        }
    except Exception as e:
        frappe.logger("bench_manager").error(f"Failed to start code-server: {e}")
        return {
            "status": "error",
            "message": f"Failed to start VS Code editor: {e}",
        }

@frappe.whitelist()
def check_code_server_status(port=9002):
    """Check if code-server is running on the given port.

    Args:
        port (int): Port to check. Defaults to 9002.

    Returns:
        dict: Running status, port, and URL.
    """
    frappe.only_for("System Manager")

    try:
        port = int(port)
    except (ValueError, TypeError):
        port = 9002

    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        try:
            s.bind(("0.0.0.0", port))
            is_running = False
        except socket.error:
            is_running = True

    host = frappe.utils.get_url().split('//')[-1].split(':')[0]
    if host in ["localhost", "127.0.0.1", "0.0.0.0"]:
        host = "127.0.0.1"

    return {
        "running": is_running,
        "port": port,
        "url": f"http://{host}:{port}" if is_running else None,
    }

# ─── Database Browser ──────────────────────────────────────────────

def get_site_db_connection(bench_path, site_name):
    import json
    import os
    import frappe
    site_config_path = os.path.join(bench_path, "sites", site_name, "site_config.json")
    if not os.path.exists(site_config_path):
        frappe.throw(f"Site config not found for {site_name}")
    
    with open(site_config_path, "r") as f:
        conf = json.load(f)
    
    import pymysql
    conn = pymysql.connect(
        host=conf.get("db_host", "127.0.0.1"),
        port=conf.get("db_port", 3306),
        user=conf.get("db_name"),
        password=conf.get("db_password"),
        database=conf.get("db_name"),
        cursorclass=pymysql.cursors.DictCursor
    )
    return conn, conf.get("db_name")

@frappe.whitelist()
def get_database_tables(bench_path, site_name):
    """Get list of tables for a specific site."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    
    try:
        conn, db_name = get_site_db_connection(bench_path, site_name)
        with conn.cursor() as cursor:
            sql = """
                SELECT 
                    TABLE_NAME as name, 
                    TABLE_ROWS as `rows`, 
                    DATA_LENGTH as size,
                    CREATE_TIME as creation
                FROM information_schema.tables 
                WHERE table_schema = %s AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME ASC
            """
            cursor.execute(sql, (db_name,))
            tables = cursor.fetchall()
        conn.close()
        
        # Convert datetime objects to string for JSON serialization
        for t in tables:
            if t.get('creation'):
                t['creation'] = str(t['creation'])
                
        return {"status": "success", "tables": tables}
    except Exception as e:
        frappe.log_error(f"Error fetching DB tables for {site_name}: {str(e)}")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_table_schema(bench_path, site_name, table_name):
    """Get column schema for a specific table."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    import re
    if not re.match(r'^[a-zA-Z0-9_ \-\.]+$', table_name):
        frappe.throw("Invalid table name")
        
    try:
        conn, db_name = get_site_db_connection(bench_path, site_name)
        with conn.cursor() as cursor:
            sql = """
                SELECT 
                    COLUMN_NAME as Field, 
                    COLUMN_TYPE as Type, 
                    IS_NULLABLE as `Null`, 
                    COLUMN_KEY as `Key`, 
                    COLUMN_DEFAULT as `Default`, 
                    EXTRA as Extra
                FROM information_schema.columns 
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ORDINAL_POSITION
            """
            cursor.execute(sql, (db_name, table_name))
            schema = cursor.fetchall()
        conn.close()
        return {"status": "success", "schema": schema}
    except Exception as e:
        frappe.log_error(f"Error fetching schema for {table_name}: {str(e)}")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_table_data(bench_path, site_name, table_name, limit=50, start=0, search=""):
    """Get row data for a specific table."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    import re
    if not re.match(r'^[a-zA-Z0-9_ \-\.]+$', table_name):
        frappe.throw("Invalid table name")
        
    try:
        limit = min(int(limit), 500)
        start = max(int(start), 0)
    except (ValueError, TypeError):
        limit = 50
        start = 0
        
    try:
        conn, db_name = get_site_db_connection(bench_path, site_name)
        with conn.cursor() as cursor:
            where_clause = ""
            args = []
            if search:
                cursor.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND DATA_TYPE IN ('varchar', 'text', 'longtext', 'mediumtext', 'char')", (db_name, table_name))
                cols = [r['COLUMN_NAME'] for r in cursor.fetchall()]
                if cols:
                    where_clause = "WHERE " + " OR ".join([f"`{c}` LIKE %s" for c in cols])
                    args = [f"%{search}%"] * len(cols)
            
            sql = f"SELECT * FROM `{table_name}` {where_clause} LIMIT %s OFFSET %s"
            args.extend([limit, start])
            cursor.execute(sql, tuple(args))
            rows = cursor.fetchall()
            
            count_sql = f"SELECT COUNT(*) as total FROM `{table_name}` {where_clause}"
            cursor.execute(count_sql, tuple(args[:-2]) if args else ())
            total = cursor.fetchone()['total']
            
        conn.close()
        
        # Serialize datetime and timedelta to string
        import datetime
        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date, datetime.timedelta)):
                    row[k] = str(v)
                elif isinstance(v, bytes):
                    try:
                        row[k] = v.decode('utf-8')
                    except:
                        row[k] = v.hex()
                        
        return {"status": "success", "rows": rows, "total": total}
    except Exception as e:
        frappe.log_error(f"Error fetching data for {table_name}: {str(e)}")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def execute_custom_query(bench_path, site_name, query):
    """Execute raw SQL query."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    
    if not query or not query.strip():
        frappe.throw("Query cannot be empty")
        
    try:
        conn, _ = get_site_db_connection(bench_path, site_name)
        with conn.cursor() as cursor:
            cursor.execute(query)
            if query.strip().upper().startswith("SELECT") or query.strip().upper().startswith("SHOW") or query.strip().upper().startswith("DESCRIBE"):
                rows = cursor.fetchall()
            else:
                conn.commit()
                rows = [{"affected_rows": cursor.rowcount}]
                
        conn.close()
        
        # Serialize datetime and timedelta to string
        import datetime
        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date, datetime.timedelta)):
                    row[k] = str(v)
                elif isinstance(v, bytes):
                    try:
                        row[k] = v.decode('utf-8')
                    except:
                        row[k] = v.hex()
                        
        return {"status": "success", "rows": rows}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def update_table_row(bench_path, site_name, table_name, pk_field, pk_value, updates):
    """Update a specific row in a table."""
    frappe.only_for("System Manager")
    bench_path = _validate_bench_path(bench_path)
    site_name = sanitize_input(site_name, "Site Name")
    
    import re
    if not re.match(r'^[a-zA-Z0-9_ \-\.]+$', table_name):
        frappe.throw("Invalid table name")
        
    import json
    if isinstance(updates, str):
        updates = json.loads(updates)
        
    if not updates:
        return {"status": "success"}
        
    try:
        conn, _ = get_site_db_connection(bench_path, site_name)
        with conn.cursor() as cursor:
            set_clause = ", ".join([f"`{k}` = %s" for k in updates.keys()])
            values = list(updates.values())
            
            sql = f"UPDATE `{table_name}` SET {set_clause} WHERE `{pk_field}` = %s"
            values.append(pk_value)
            
            cursor.execute(sql, tuple(values))
            conn.commit()
            
        conn.close()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

