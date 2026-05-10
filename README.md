# Bench Manager

A Frappe custom app that provides a full GUI inside Frappe Desk to manage Frappe Bench — replacing all terminal commands with a clean UI.

## Features

- **Site Management**: Create, delete, migrate, backup sites. Toggle maintenance mode.
- **App Management**: Create new apps, install from GitHub, install/uninstall on sites.
- **Bench Management**: View bench status, version info, run migrations.
- **Real-Time Logs**: Live streaming of command output with color-coded status.

## Installation

```bash
cd /path/to/frappe-bench
bench get-app ./apps/bench_manager
bench --site your-site install-app bench_manager
bench migrate
```

## Usage

Navigate to `/app/bench-dashboard` in your Frappe Desk.

## Permissions

Only users with the **System Manager** role can access Bench Manager.

## License

MIT
