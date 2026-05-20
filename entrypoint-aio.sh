#!/bin/bash
set -e

# ============================================================
# Bench Manager - All-in-One Auto-Provisioning Entrypoint
# Starts MariaDB, Redis, configures Frappe, creates site,
# then launches all services via supervisord.
# ============================================================

BENCH_DIR="/home/frappe/frappe-bench"
SITES_DIR="${BENCH_DIR}/sites"

echo "============================================"
echo "  Bench Manager - Starting All Services"
echo "============================================"

# ---- 1. Initialize MariaDB data directory if needed ----
if [ ! -d /var/lib/mysql/mysql ]; then
    echo "[1/5] Initializing MariaDB data directory..."
    mysql_install_db --user=mysql --datadir=/var/lib/mysql > /dev/null 2>&1
else
    echo "[1/5] MariaDB data directory exists. Skipping init."
fi

# ---- 2. Start MariaDB temporarily for setup ----
echo "[2/5] Starting MariaDB..."
/usr/sbin/mariadbd --user=mysql --datadir=/var/lib/mysql &
MARIADB_PID=$!

# Wait for MariaDB to be ready
for i in $(seq 1 30); do
    if mariadb -u root -e "SELECT 1" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Set root password and permissions on first run
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-admin}"
mariadb -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';" 2>/dev/null || true
mariadb -u root -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;" 2>/dev/null || true
echo "  MariaDB is ready."

# ---- 3. Start Redis ----
echo "[3/5] Starting Redis..."
redis-server --bind 127.0.0.1 --port 6379 --daemonize yes --save "" > /dev/null 2>&1
echo "  Redis is ready."

# ---- 4. Configure Frappe & create site (first run only) ----
if ! grep -q "db_host" "${SITES_DIR}/common_site_config.json" 2>/dev/null; then
    echo "[4/5] First run detected. Configuring Frappe..."

    # Ensure apps.txt
    cd "${BENCH_DIR}"
    ls -1 apps > "${SITES_DIR}/apps.txt"

    # Write bench config
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g db_host 127.0.0.1"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -gp db_port 3306"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_cache redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_queue redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -g redis_socketio redis://127.0.0.1:6379"
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench set-config -gp socketio_port 9000"

    ADMIN_PASSWORD="${FRAPPE_ADMIN_PASSWORD:-admin}"

    echo "  Creating site bench-manager.local..."
    su -s /bin/bash frappe -c "cd ${BENCH_DIR} && bench new-site bench-manager.local \
        --mariadb-user-host-login-scope='%' \
        --admin-password='${ADMIN_PASSWORD}' \
        --db-root-username=root \
        --db-root-password='${MYSQL_ROOT_PASSWORD}' \
        --install-app bench_manager \
        --set-default"
    echo "  Site created successfully!"
else
    echo "[4/5] Site already configured. Skipping."
fi

# ---- 5. Ensure assets are available ----
echo "[5/5] Copying assets..."
ASSETS_BAKED="${BENCH_DIR}/assets_baked"
ASSETS_TARGET="${SITES_DIR}/assets"
if [ -d "${ASSETS_BAKED}" ]; then
    cp -a "${ASSETS_BAKED}"/. "${ASSETS_TARGET}"/ || true
fi

# Ensure apps.txt has bench_manager
if ! grep -q "bench_manager" "${SITES_DIR}/apps.txt" 2>/dev/null; then
    echo "bench_manager" >> "${SITES_DIR}/apps.txt"
fi

# ---- Stop temporary MariaDB & Redis (supervisord will manage them) ----
kill $MARIADB_PID 2>/dev/null || true
wait $MARIADB_PID 2>/dev/null || true
killall redis-server 2>/dev/null || true
sleep 1

echo ""
echo "============================================"
echo "  Bench Manager is ready!"
echo "  URL:      http://localhost:9001"
echo "  User:     Administrator"
echo "  Password: ${FRAPPE_ADMIN_PASSWORD:-admin}"
echo "============================================"
echo ""

# ---- Launch all services via supervisord ----
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
