# Multi-Site Handling in Bench Manager

Managing multiple Frappe sites concurrently in a local development environment presents a specific technical challenge, which Bench Manager solves through process isolation.

## The Problem: Site-Pinning

Frappe handles multi-tenancy dynamically by inspecting the `Host` header of incoming HTTP requests. In a local environment, developers typically access sites via `localhost`. If `bench_manager` is the default site, Frappe will "pin" all `localhost` requests to the `bench_manager` site. This makes it difficult to open or test other newly created sites without modifying system `/etc/hosts` files or running complex proxy configurations.

## The Solution: Isolated Dev Servers

To bypass Frappe's native multi-tenant routing, Bench Manager allocates dedicated development servers for each individual site.

When a user attempts to "start" or "open" a specific site from the Bench Manager dashboard:
1. **Port Allocation**: The system finds an available TCP port using Python's `socket` library (`_find_free_port`).
2. **Process Spawning**: Bench Manager spawns a dedicated background process running `bench serve --port <free_port>`.
3. **Environment Isolation**: The environment variable `FRAPPE_SITE=<site_name>` is injected into the subprocess. This forces the Frappe framework to bypass host-header resolution and strictly serve the specified site.
4. **Werkzeug Cleanup**: To prevent socket conflicts, Werkzeug reloader environment variables inherited from the main bench manager process (`WERKZEUG_SERVER_FD`, `WERKZEUG_RUN_MAIN`) are explicitly stripped from the child process.

## Process Tracking

Because these dev servers run in the background as long-lived processes, Bench Manager must maintain their state:

- **State File**: It utilizes a JSON state file (`site_servers.json`) located in the root of the bench directory.
- **Data Stored**: This file maps `site_name` to a dictionary containing the process ID (`pid`) and active `port`.
- **Zombie Process Cleanup**: Before returning a list of active sites to the UI, the backend runs `_cleanup_dead_servers()`. This function checks the system's process tree (`os.kill(pid, 0)` and `/proc/{pid}/status`) to verify if the process is genuinely alive. If a process was killed manually via terminal or died unexpectedly, Bench Manager detects the "zombie" state and automatically removes it from the tracking file.

## Graceful Termination

When a user clicks "Stop Site", Bench Manager reads the PID from the state file and sends a `SIGTERM` signal to the entire process group (`os.killpg(os.getpgid(pid), signal.SIGTERM)`), ensuring all child workers spawned by `bench serve` are cleanly terminated. It follows up with a `SIGKILL` if the process does not shut down gracefully.
