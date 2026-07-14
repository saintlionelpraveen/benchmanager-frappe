#!/bin/bash
set -euo pipefail

# ============================================================
# Bench Manager - Production All-in-One Entrypoint
# Starts MariaDB, Redis, configures Frappe, creates site,
# then launches all services via supervisord with graceful
# shutdown handling.
# ============================================================

BENCH_DIR="/home/frappe/frappe-bench"
SITES_DIR="${BENCH_DIR}/sites"
LOGS_DIR="${BENCH_DIR}/logs"

# ── Graceful Shutdown Handler ────────────────────────────────
_shutdown() {
    echo ""
    echo "[shutdown] Received termination signal. Gracefully stopping..."
    
    # Stop supervisor (which manages all child processes)
    if [ -f /var/run/supervisord.pid ]; then
        PID=$(cat /var/run/supervisord.pid 2>/dev/null || echo "")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            /usr/bin/supervisorctl.real stop all 2>/dev/null || true
            kill -TERM "$PID" 2>/dev/null || true
            
            # Wait up to 30 seconds for clean shutdown
            for i in $(seq 1 30); do
                kill -0 "$PID" 2>/dev/null || break
                sleep 1
            done
            
            # Force kill if still running
            kill -0 "$PID" 2>/dev/null && kill -KILL "$PID" 2>/dev/null || true
        fi
    fi
    
    # Flush MariaDB data
    if pgrep -x mariadbd >/dev/null 2>&1; then
        echo "[shutdown] Flushing MariaDB tables..."
        mariadb -u root -p"${MYSQL_ROOT_PASSWORD:-root}" -e "FLUSH TABLES WITH READ LOCK; FLUSH LOGS;" 2>/dev/null || true
    fi
    
    echo "[shutdown] Shutdown complete."
    exit 0
}

trap '_shutdown' SIGTERM SIGINT SIGQUIT

echo "============================================"
echo "  Bench Manager - Starting All Services"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "============================================"

# ── Ensure log directories exist ─────────────────────────────
mkdir -p "${LOGS_DIR}" /var/log/supervisor /var/run/supervisor /var/log/mysql
chown -R frappe:frappe "${LOGS_DIR}" 2>/dev/null || true
chown mysql:mysql /var/log/mysql 2>/dev/null || true

# ── 1. Initialize MariaDB data directory if needed ───────────
if [ ! -d /var/lib/mysql/mysql ]; then
    echo "[1/6] Initializing MariaDB data directory..."
    mysql_install_db --user=mysql --datadir=/var/lib/mysql > /dev/null 2>&1
else
    echo "[1/6] MariaDB data directory exists. Skipping init."
fi

# ── 2. Start MariaDB temporarily for setup ───────────────────
echo "[2/6] Starting MariaDB..."
/usr/sbin/mariadbd --user=mysql --datadir=/var/lib/mysql &
MARIADB_PID=$!

# Wait for MariaDB to be ready (up to 60 seconds for slow storage)
MARIADB_READY=0
for i in $(seq 1 60); do
    if mariadb -u root -e "SELECT 1" > /dev/null 2>&1; then
        MARIADB_READY=1
        break
    fi
    sleep 1
done

if [ "$MARIADB_READY" -ne 1 ]; then
    echo "[ERROR] MariaDB failed to start within 60 seconds."
    exit 1
fi

# Set root password and permissions on first run
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"
mariadb -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';" 2>/dev/null || true
mariadb -u root -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;" 2>/dev/null || true

# Remove anonymous users and test database for security
mariadb -u root -p"${MYSQL_ROOT_PASSWORD}" -e "
DELETE FROM mysql.user WHERE User='';
DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');
DROP DATABASE IF EXISTS test;
DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';
FLUSH PRIVILEGES;
" 2>/dev/null || true

echo "  MariaDB is ready."

# ── 3. Start Redis ───────────────────────────────────────────
echo "[3/6] Starting Redis..."
redis-server --bind 127.0.0.1 --port 6379 --daemonize yes \
    --save "" \
    --maxmemory 256mb \
    --maxmemory-policy allkeys-lru \
    --protected-mode yes \
    --loglevel warning \
    > /dev/null 2>&1

# Wait for Redis
for i in $(seq 1 15); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        break
    fi
    sleep 1
done
echo "  Redis is ready."

# ── 4. Configure Frappe & create site (first run only) ───────
if ! grep -q "db_host" "${SITES_DIR}/common_site_config.json" 2>/dev/null; then
    echo "[4/6] First run detected. Configuring Frappe..."

    # Ensure apps.txt
    cd "${BENCH_DIR}"
    ls -1 apps > "${SITES_DIR}/apps.txt"

    # Write bench config
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g db_host 127.0.0.1"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -gp db_port 3306"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_cache redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_queue redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_socketio redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g restart_supervisor_on_update 0"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g restart_systemd_on_update 0"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -gp socketio_port 9000"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g root_login root"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g root_password '${MYSQL_ROOT_PASSWORD}'"

    ADMIN_PASSWORD="${FRAPPE_ADMIN_PASSWORD:-admin}"

    echo "  Creating site bench-manager.local..."
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench new-site bench-manager.local \
        --mariadb-user-host-login-scope='%' \
        --admin-password='${ADMIN_PASSWORD}' \
        --db-root-username=root \
        --db-root-password='${MYSQL_ROOT_PASSWORD}' \
        --install-app bench_manager \
        --set-default"
    
    # Bypass the setup wizard since Bench Manager doesn't need ERPNext-like setup
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench --site bench-manager.local set-config setup_complete 1"

    echo "  Site created successfully!"
else
    echo "[4/6] Site already configured. Skipping."
fi

# ── 5. Run pending migrations (upgrade safety) ──────────────
echo "[5/6] Running migrations..."
su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench --site bench-manager.local migrate --skip-failing" 2>/dev/null || {
    echo "  [WARN] Migration had warnings (non-fatal)."
}

# ── 6. Ensure assets are available ──────────────────────────
echo "[6/6] Syncing assets..."
ASSETS_BAKED="${BENCH_DIR}/assets_baked"
ASSETS_TARGET="${SITES_DIR}/assets"
if [ -d "${ASSETS_BAKED}" ]; then
    cp -a "${ASSETS_BAKED}"/. "${ASSETS_TARGET}"/ || true
fi

# Ensure apps.txt has bench_manager
if ! grep -q "bench_manager" "${SITES_DIR}/apps.txt" 2>/dev/null; then
    echo "bench_manager" >> "${SITES_DIR}/apps.txt"
fi

# ── Stop temporary MariaDB & Redis (supervisord will manage them) ──
kill $MARIADB_PID 2>/dev/null || true
wait $MARIADB_PID 2>/dev/null || true
killall redis-server 2>/dev/null || true
sleep 2

echo ""
echo "============================================"
echo "  Bench Manager is ready!"
echo "  URL:      http://localhost:${BENCH_MANAGER_PORT:-8080}"
echo "  User:     Administrator"
echo "  Password: ${FRAPPE_ADMIN_PASSWORD:-admin}"
echo "============================================"
echo ""

# ── Launch all services via supervisord ─────────────────────
# Use exec to replace this shell with supervisord for proper
# PID 1 signal handling
exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
