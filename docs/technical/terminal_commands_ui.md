# Translating Terminal Commands to UI

A core technical challenge in Bench Manager is executing shell commands (like `bench new-site` or `bench get-app`) in the background and presenting their output natively in the web UI as if the user were watching a real terminal.

## The Problem with Subprocess Pipes

By default, when Python's `subprocess` module executes a CLI tool (like `bench` or `click`-based apps) and pipes standard output (`stdout`), these tools detect they are not running in a real interactive terminal. As a result, they suppress verbose output, strip ANSI colors, and hide progress bars. 

## The Solution: Pseudo-Terminals (PTY)

Bench Manager solves this by executing commands using a Pseudo-Terminal (`pty`).
Located in `bench_manager.utils.run_bench_command`:

1. **PTY Initialization**: The system calls `pty.openpty()` to create a master/slave file descriptor pair.
2. **Subprocess Spawning**: `subprocess.Popen` is executed with the slave file descriptor mapped to `stdout` and `stderr`. The CLI tool believes it is running inside a real terminal (like `xterm-256color`) and outputs full, rich terminal text, including colors and progress updates.
3. **Asynchronous Reading**: A dedicated daemon thread (`threading.Thread`) uses the `select` module to perform non-blocking reads from the master file descriptor.

## Real-Time Streaming to UI

As text is read from the PTY, Bench Manager must relay it to the browser with zero latency:

1. **Regex Cleaning**: ANSI escape sequences (e.g., color codes, cursor movements) are stripped out using regex (`ansi_re.sub`).
2. **Noise Suppression**: High-frequency lines, such as git clone progress percentages (which flood WebSockets), are filtered out using the `_is_git_progress` regex.
3. **Dual-Publishing System**:
   - **Socket.IO**: Data is pushed via Frappe's real-time events (`frappe.publish_realtime`) to the "bench_console" room.
   - **SSE (Server-Sent Events)**: As a fallback and for zero-latency buffering, messages are also pushed directly to an in-memory SSE queue (`push_sse_event`).
4. **Frontend Reception**: The Javascript Dashboard listens to the Socket event and appends lines to a stylized, auto-scrolling terminal UI component.

## Execution Safety

Commands are never executed blindly:
- Every user input (site name, app name) is passed through `sanitize_input` which only allows alphanumeric characters, dots, hyphens, and underscores, strictly preventing shell injection.
- Git URLs are validated against complex regex patterns (`sanitize_git_url`) to ensure they don't contain shell metacharacters (e.g., `;`, `&`, `|`, `$`).
- Every command is permanently recorded in the database via the `Bench Command Log` DocType for full administrative auditing.
