# ============================================================
# Bench Manager - All-in-One Docker Image (Production)
# Everything in ONE container: MariaDB + Redis + Frappe + Nginx
# Usage: docker run -d -p 9001:8080 messitebi/bench-manager:latest
# ============================================================

# Build-time arguments for CI/CD
ARG FRAPPE_VERSION=version-16

# ---- Stage 1: Builder - install bench_manager app & build assets ----
FROM frappe/erpnext:${FRAPPE_VERSION} AS builder

USER root
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

USER frappe
WORKDIR /home/frappe/frappe-bench

COPY --chown=frappe:frappe . apps/bench_manager/

RUN ls -la apps/bench_manager/ && \
    cat apps/bench_manager/pyproject.toml && \
    /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir setuptools wheel && \
    /home/frappe/frappe-bench/env/bin/pip install --no-cache-dir -e apps/bench_manager \
    && echo "bench_manager" >> sites/apps.txt

RUN cd apps/frappe && yarn --frozen-lockfile 2>/dev/null || yarn \
    && cd ../erpnext && yarn --frozen-lockfile 2>/dev/null || yarn \
    && cd /home/frappe/frappe-bench \
    && echo "frappe" > sites/apps.txt \
    && echo "erpnext" >> sites/apps.txt \
    && echo "bench_manager" >> sites/apps.txt \
    && cd sites \
    && /home/frappe/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe build \
    && cd .. \
    && mkdir -p sites/assets/bench_manager/images \
    && (cp -R apps/bench_manager/bench_manager/public/images/* sites/assets/bench_manager/images/ 2>/dev/null || true) \
    && cp -r sites/assets /home/frappe/builder_assets \
    && cp sites/apps.txt /home/frappe/builder_apps.txt


# ---- Stage 2: All-in-One Production Image ----
FROM frappe/erpnext:${FRAPPE_VERSION} AS production

# OCI standard labels for image metadata
LABEL org.opencontainers.image.title="Bench Manager"
LABEL org.opencontainers.image.description="All-in-One Frappe Bench Manager with MariaDB, Redis, and Nginx"
LABEL org.opencontainers.image.source="https://github.com/saintlionelpraveen/benchmanager-frappe"
LABEL org.opencontainers.image.vendor="saintlionelpraveen"

# Build arg for commit SHA (injected by CI/CD)
ARG BUILD_DATE
ARG GIT_SHA
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"

USER root
ENV BENCH_MANAGER_AIO=1

# ── Install runtime dependencies (no dev tools) ─────────────────────
RUN apt-get update && apt-get install --no-install-recommends -y \
    mariadb-server \
    redis-server \
    nginx \
    supervisor \
    curl \
    cron \
    gosu \
    && curl -fsSL https://code-server.dev/install.sh | sh \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default \
    && mkdir -p /var/log/supervisor /var/run/supervisor

# ── Security: Remove unnecessary SUID/SGID binaries ─────────────────
RUN find / -perm /6000 -type f -exec chmod a-s {} \; 2>/dev/null || true

# ── MariaDB hardening ───────────────────────────────────────────────
RUN mkdir -p /run/mysqld && chown mysql:mysql /run/mysqld && \
    # Disable remote root login, bind to localhost only
    echo "[mysqld]" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "bind-address = 127.0.0.1" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "skip-name-resolve" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "max_connections = 100" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "innodb_buffer_pool_size = 256M" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "innodb_log_file_size = 64M" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "innodb_flush_log_at_trx_commit = 1" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "innodb_flush_method = O_DIRECT" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "character-set-server = utf8mb4" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "collation-server = utf8mb4_unicode_ci" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "slow_query_log = 1" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "slow_query_log_file = /var/log/mysql/slow.log" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    echo "long_query_time = 2" >> /etc/mysql/mariadb.conf.d/99-bench-manager.cnf && \
    mkdir -p /var/log/mysql && chown mysql:mysql /var/log/mysql

# ── Redis hardening ─────────────────────────────────────────────────
RUN echo "maxmemory 256mb" >> /etc/redis/redis.conf 2>/dev/null || true && \
    echo "maxmemory-policy allkeys-lru" >> /etc/redis/redis.conf 2>/dev/null || true

# Copy config files
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY nginx.conf /etc/nginx/sites-enabled/frappe.conf
COPY entrypoint-aio.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

# Inject async wrapper for supervisorctl to prevent suicide during bench restart
RUN mv /usr/bin/supervisorctl /usr/bin/supervisorctl.real && \
    echo '#!/bin/bash' > /usr/bin/supervisorctl && \
    echo 'if [ "$1" = "restart" ]; then' >> /usr/bin/supervisorctl && \
    echo '    (sleep 2 && /usr/bin/supervisorctl.real "$@") >/dev/null 2>&1 &' >> /usr/bin/supervisorctl && \
    echo '    disown' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-web: stopped"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-socketio: stopped"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-worker-long: stopped"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-scheduler: stopped"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-web: started"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-socketio: started"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-worker-long: started"' >> /usr/bin/supervisorctl && \
    echo '    echo "frappe:frappe-scheduler: started"' >> /usr/bin/supervisorctl && \
    echo '    exit 0' >> /usr/bin/supervisorctl && \
    echo 'else' >> /usr/bin/supervisorctl && \
    echo '    exec /usr/bin/supervisorctl.real "$@"' >> /usr/bin/supervisorctl && \
    echo 'fi' >> /usr/bin/supervisorctl && \
    chmod +x /usr/bin/supervisorctl

# ── Create dedicated log directories ────────────────────────────────
RUN mkdir -p /home/frappe/frappe-bench/logs \
    && chown -R frappe:frappe /home/frappe/frappe-bench/logs

USER frappe
WORKDIR /home/frappe/frappe-bench

# Copy app, env, apps.txt, assets from builder
COPY --from=builder --chown=frappe:frappe /home/frappe/frappe-bench/apps apps/
COPY --from=builder --chown=frappe:frappe /home/frappe/frappe-bench/env env/
COPY --from=builder --chown=frappe:frappe /home/frappe/builder_apps.txt sites/apps.txt
COPY --from=builder --chown=frappe:frappe /home/frappe/builder_assets sites/assets/

# Patch api.py ONLY inside the image to use Nginx proxy path for code-server instead of local port
RUN sed -i 's|"url": f"http://{host}:{port}/?folder={bench_path}"|"url": f"/vscode/{port}/?folder={bench_path}"|g' apps/bench_manager/bench_manager/api.py \
    && sed -i 's|"url": f"http://{host}:{port}/?folder={inst_bench_path}"|"url": f"/vscode/{port}/?folder={inst_bench_path}"|g' apps/bench_manager/bench_manager/api.py


# Backup assets so they survive volume mounts
RUN cp -r sites/assets assets_baked

# Volumes for persistence
VOLUME ["/var/lib/mysql", "/home/frappe/frappe-bench/sites", "/home/frappe/frappe-bench/logs"]

EXPOSE 8080

# Health check: verify Nginx is responding with actual Frappe ping
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
  CMD curl -fsS http://localhost:8080/api/method/ping || exit 1

# ── Signal handling: entrypoint runs as root, drops to frappe for services
USER root

# Use exec form for proper signal propagation
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# ── Security metadata ──────────────────────────────────────────────
# Container runs Nginx on 8080 (unprivileged port)
# MariaDB listens on 127.0.0.1:3306 (not exposed externally)
# Redis listens on 127.0.0.1:6379 (not exposed externally)
# Supervisord manages all processes with proper user isolation
