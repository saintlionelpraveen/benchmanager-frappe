"""
API endpoints for Bench Manager.
All bench commands are executed via Python subprocess with proper
sanitization, error handling, and real-time output streaming.
Only System Manager role can access these endpoints.
"""

import json
import os

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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=cmd,
        queue="long",
        timeout=600,
    )

    return {"status": "queued", "message": f"Site creation for '{site_name}' has been queued."}


@frappe.whitelist()
def drop_site(site_name):
    """Drop/delete a Frappe site.

    Args:
        site_name (str): Name of the site to delete.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")

    existing_sites = get_site_list()
    if site_name not in existing_sites:
        frappe.throw(f"Site '{site_name}' does not exist")

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["drop-site", site_name, "--force"],
        queue="long",
        timeout=300,
    )

    return {"status": "queued", "message": f"Site deletion for '{site_name}' has been queued."}


@frappe.whitelist()
def list_sites():
    """List all existing sites with their status.

    Returns:
        list: List of dicts with site_name and status.
    """
    frappe.only_for("System Manager")

    sites = get_site_list()
    bench_path = get_bench_path()
    site_data = []

    for site in sites:
        site_config_path = os.path.join(bench_path, "sites", site, "site_config.json")
        status = "Active"
        db_name = ""

        try:
            with open(site_config_path, "r") as f:
                config = json.load(f)
                db_name = config.get("db_name", "")
                if config.get("maintenance_mode", 0):
                    status = "Maintenance"
        except Exception:
            status = "Unknown"

        # Check installed apps
        apps = []
        try:
            apps_file = os.path.join(bench_path, "sites", site, "site_config.json")
            result = _get_site_apps(site, bench_path)
            apps = result
        except Exception:
            pass

        site_data.append({
            "site_name": site,
            "status": status,
            "db_name": db_name,
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
        except Exception:
            pass

    # Fallback to apps.txt
    apps_txt = os.path.join(bench_path, "sites", site_name, "apps.txt")
    if os.path.exists(apps_txt):
        try:
            with open(apps_txt, "r") as f:
                apps = [line.strip() for line in f if line.strip()]
        except Exception:
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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["--site", site_name, "migrate"],
        queue="long",
        timeout=600,
    )

    return {"status": "queued", "message": f"Migration for '{site_name}' has been queued."}


@frappe.whitelist()
def backup_site(site_name):
    """Create a backup of a site.

    Args:
        site_name (str): Name of the site to backup.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    site_name = sanitize_input(site_name, "Site Name")

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["--site", site_name, "backup", "--with-files"],
        queue="long",
        timeout=600,
    )

    return {"status": "queued", "message": f"Backup for '{site_name}' has been queued."}


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
        ["--site", site_name, "set-config", "maintenance_mode", "1" if mode == "on" else "0"]
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

    cmd = ["new-app", "--no-git", app_name]

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=cmd,
        queue="long",
        timeout=300,
    )

    return {"status": "queued", "message": f"App creation for '{app_name}' has been queued."}


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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=cmd,
        queue="long",
        timeout=600,
    )

    return {"status": "queued", "message": f"Fetching app from '{git_url}' has been queued."}


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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["--site", site_name, "install-app", app_name],
        queue="long",
        timeout=300,
    )

    return {"status": "queued", "message": f"Installing '{app_name}' on '{site_name}' has been queued."}


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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["--site", site_name, "uninstall-app", app_name, "--yes"],
        queue="long",
        timeout=300,
    )

    return {"status": "queued", "message": f"Uninstalling '{app_name}' from '{site_name}' has been queued."}


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

            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=app_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                app_info["git_url"] = result.stdout.strip()

            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=app_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                app_info["branch"] = result.stdout.strip()
        except Exception:
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

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["update", "--no-backup"],
        queue="long",
        timeout=1200,
    )

    return {"status": "queued", "message": "Bench update has been queued."}


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
    except Exception:
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
        result = subprocess.run(
            ["bench", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=get_bench_path(),
        )
        versions["bench"] = result.stdout.strip() if result.returncode == 0 else "Unknown"
    except Exception:
        versions["bench"] = "Unknown"

    # Frappe version
    try:
        versions["frappe"] = frappe.__version__
    except Exception:
        versions["frappe"] = "Unknown"

    # Python version
    import sys
    versions["python"] = sys.version.split()[0]

    # Node version
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        versions["node"] = result.stdout.strip() if result.returncode == 0 else "Unknown"
    except Exception:
        versions["node"] = "Unknown"

    return versions


@frappe.whitelist()
def bench_migrate_all():
    """Run bench migrate across all sites.

    Returns:
        dict: Command execution result.
    """
    frappe.only_for("System Manager")

    frappe.enqueue(
        "bench_manager.utils.run_bench_command",
        command_parts=["migrate"],
        queue="long",
        timeout=1200,
    )

    return {"status": "queued", "message": "Bench migrate (all sites) has been queued."}


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
