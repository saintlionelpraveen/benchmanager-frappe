# VS Code Editor Integration

Bench Manager offers developers the ability to launch a fully-featured, web-based IDE directly from the browser, pointing exactly at the current bench directory. This is achieved by integrating with `code-server` (VS Code running in the browser).

## Architecture & Launching

When a user clicks "Start Editor" in the Bench Manager UI:

1. **Port Allocation**: Bench Manager searches for an available port starting at `9002` (up to `9100`) using standard Python socket binding tests.
2. **Binary Discovery**: It locates the `code-server` executable in the system path or `~/.local/bin/code-server`. If not found, it prompts the user to install it.
3. **Execution**: A background process is spawned running `code-server`. 
   - `--bind-addr 0.0.0.0:{port}` is used to expose the IDE to the web.
   - `--auth none` disables password authentication, relying on the fact that access to the Bench Manager implies authorization.
   - `--user-data-dir` is redirected to `.vscode-server-data` inside the bench to keep configurations isolated.
   - The current `bench_path` is passed as the final argument, opening the bench as the active workspace.

## Environment Variable Stripping (The IPC Delegation Problem)

A major technical hurdle when launching `code-server` from inside a Python subprocess (which itself might be running inside a native VS Code terminal) is IDE delegation. 

If `code-server` detects `VSCODE_` or `ELECTRON_` environment variables, it assumes the user is trying to open a file in an already running native VS Code instance. It will attempt to pass the directory path to the native IDE via an IPC (Inter-Process Communication) socket and immediately exit, failing to start the web server.

Bench Manager explicitly strips these variables (along with `TERM_PROGRAM` and `PORT`) from the `os.environ` payload before spawning the child process, forcing `code-server` to launch an independent web server instance.

## Process State Management

Unlike site servers, `code-server` does not easily track its own PIDs in a JSON file out-of-the-box. Bench Manager handles state management via direct system polling:

1. **Discovery**: `get_running_vscode_instances()` runs `ps -eo pid,command` on the host system.
2. **Parsing**: It parses the output, specifically looking for `code-server` and `--bind-addr`.
3. **Mapping**: By extracting the bound port and the trailing folder path argument from the command string, it maps the running editor to specific bench environments.
4. **UI Updates**: The frontend polls this API and displays active editors in a table, allowing the user to click "Open" (routing them to the parsed URL) or "Stop" (sending `SIGKILL` to the parsed PID).
