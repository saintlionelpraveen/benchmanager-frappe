"""Hooks configuration for Bench Manager app."""

app_name = "bench_manager"
app_title = "Bench Manager"
app_publisher = "Praveen"
app_description = "A Frappe app to manage Bench operations via GUI"
app_email = "admin@example.com"
app_license = "MIT"
app_version = "0.0.1"

# Required apps
required_apps = ["frappe"]

# Each module has its own set of doctypes
# Modules are listed in modules.txt

# Website
# -------
website_route_rules = []

# Home Pages
# ----------
# role_home_page = {"System Manager": "bench-dashboard"}

# Permissions evaluated in scripted ways
# --------------------------------------
permission_query_conditions = {}
has_permission = {}

# DocType Class
# ---------------
override_doctype_class = {}

# Document Events
# ----------------
doc_events = {}

# Scheduled Tasks
# ----------------
scheduler_events = {}

# Jinja Environment
# ------------------
jinja = {}

# Installation
# ------------
# before_install = "bench_manager.install.before_install"
# after_install = "bench_manager.install.after_install"

# Desk Notifications
# -------------------
notification_config = "bench_manager.notification.get_notification_config"

# Override Methods
# ----------------
override_whitelisted_methods = {}
