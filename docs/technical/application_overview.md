# Bench Manager: Application Overview

Bench Manager is a Frappe-based application designed to bring the traditional CLI-based `bench` utility into a rich, web-based graphical user interface (GUI). It acts as an administration dashboard for developers and system managers to manage their Frappe/ERPNext environments without needing direct terminal access.

## Core Architecture

The application follows the standard Frappe architecture but heavily utilizes system-level integrations to control the underlying host server.

- **Backend (Python)**: Exposes Frappe Whitelisted APIs (in `api.py`) that serve as proxies for `bench` CLI commands.
- **Frontend (JS/HTML/CSS)**: Provides the interactive Dashboard (`bench_dashboard`) which communicates with the API endpoints and renders real-time data.
- **System Layer**: Uses Python's `subprocess`, `os`, and `pty` modules to execute shell commands securely and track system processes.

## Key Features & Technologies Used

1. **System & Path Detection**: Auto-detects the bench directory path (`get_bench_path`) by traversing up from the app installation or reading Frappe's database settings.
2. **Process Management**: Tracks background processes for site servers and code editors using system Process IDs (PIDs).
3. **Real-time WebSockets**: Uses Frappe's Socket.IO (`frappe.publish_realtime`) and Server-Sent Events (SSE) to push live terminal output directly to the browser.
4. **Security Hardening**: Implements strict input sanitization (`sanitize_input`, `sanitize_git_url`) to prevent shell injection attacks, stripping dangerous characters from user inputs before they reach the shell.

## Typical Workflow

When a user clicks "Install App" or "Create Site" in the UI:
1. The frontend sends an Ajax request to the Python API.
2. The Python API validates the input, ensures the user is a "System Manager", and constructs a safe shell command array.
3. The command is spawned in a background daemon thread using a pseudo-terminal.
4. Standard output is captured and streamed back to the frontend in real-time, displaying a live terminal view to the user.
5. The final output is saved to the database in a `Bench Command Log` document for historical auditing.
