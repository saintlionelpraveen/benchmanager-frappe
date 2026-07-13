#!/usr/bin/env bash
# ============================================================
# Bench Manager - One-Command Setup Script
# Checks for Docker, installs if missing, pulls latest image,
# and runs the Bench Manager container.
#
# Usage:
#   bash setup-bench-manager.sh
#   bash setup-bench-manager.sh --port 8080
#   bash setup-bench-manager.sh --interactive
#
# ============================================================

set -euo pipefail

# ── Configuration ────────────────────────────────────────────
DOCKER_IMAGE="messitebi/bench-manager:latest"
CONTAINER_NAME="bench-manager"
DEFAULT_PORT=9001
ADMIN_PASSWORD="${FRAPPE_ADMIN_PASSWORD:-admin}"
MYSQL_PASSWORD="${MYSQL_ROOT_PASSWORD:-admin}"

# ── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Helpers ──────────────────────────────────────────────────
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
log_error()   { echo -e "${RED}[✕]${NC} $1"; }
log_step()    { echo -e "\n${CYAN}${BOLD}── $1 ──${NC}"; }

# ── Parse Arguments ──────────────────────────────────────────
PORT=""
INTERACTIVE=false
SKIP_DOCKER_INSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --port|-p)
            PORT="$2"
            shift 2
            ;;
        --interactive|-i)
            INTERACTIVE=true
            shift
            ;;
        --password)
            ADMIN_PASSWORD="$2"
            shift 2
            ;;
        --mysql-password)
            MYSQL_PASSWORD="$2"
            shift 2
            ;;
        --skip-docker-install)
            SKIP_DOCKER_INSTALL=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -p, --port PORT          Port to expose Bench Manager (default: 9001)"
            echo "  -i, --interactive        Launch interactive setup page in browser"
            echo "      --password PASS      Frappe admin password (default: admin)"
            echo "      --mysql-password P   MySQL root password (default: admin)"
            echo "      --skip-docker-install Skip Docker installation check"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Banner ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     ${CYAN}Bench Manager${NC}${BOLD} - Setup Script              ║${NC}"
echo -e "${BOLD}║     Production-Ready Docker Deployment        ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════╝${NC}"
echo ""

# ── System Check ─────────────────────────────────────────────
log_step "System Check"

# Check OS
if [[ "$(uname)" != "Linux" ]] && [[ "$(uname)" != "Darwin" ]]; then
    log_error "This script supports Linux and macOS only."
    exit 1
fi

DISTRO="unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
    log_info "Detected OS: $PRETTY_NAME"
elif [[ "$(uname)" == "Darwin" ]]; then
    DISTRO="macos"
    log_info "Detected OS: macOS $(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
fi

# Check available disk space (need at least 5GB)
AVAIL_GB=$(df -BG / | awk 'NR==2{print $4}' | sed 's/G//' 2>/dev/null || echo "999")
if [ "$AVAIL_GB" -lt 5 ] 2>/dev/null; then
    log_warn "Low disk space: ${AVAIL_GB}GB available. Recommend at least 5GB."
fi

# Check available memory (need at least 1GB)
if command -v free &>/dev/null; then
    AVAIL_MEM=$(free -m | awk 'NR==2{print $7}' 2>/dev/null || echo "9999")
    if [ "$AVAIL_MEM" -lt 1024 ] 2>/dev/null; then
        log_warn "Low memory: ${AVAIL_MEM}MB available. Recommend at least 2GB."
    fi
fi

# ── Docker Check & Install ───────────────────────────────────
log_step "Docker Engine"

install_docker() {
    log_info "Installing Docker Engine..."
    
    case "$DISTRO" in
        ubuntu|debian|linuxmint|pop)
            log_info "Using apt-based installation for $DISTRO..."
            
            # Remove old versions
            sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
            
            # Install prerequisites
            sudo apt-get update -y
            sudo apt-get install -y \
                apt-transport-https \
                ca-certificates \
                curl \
                gnupg \
                lsb-release
            
            # Add Docker GPG key
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL "https://download.docker.com/linux/${DISTRO}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || \
                curl -fsSL "https://download.docker.com/linux/ubuntu/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            
            # Add Docker repository
            CODENAME=$(lsb_release -cs 2>/dev/null || echo "jammy")
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${DISTRO} ${CODENAME} stable" | \
                sudo tee /etc/apt/sources.list.d/docker.list > /dev/null 2>/dev/null || \
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" | \
                sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            
            # Install Docker
            sudo apt-get update -y
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
            
        centos|rhel|fedora|rocky|almalinux)
            log_info "Using yum/dnf-based installation for $DISTRO..."
            
            sudo yum remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true
            sudo yum install -y yum-utils 2>/dev/null || sudo dnf install -y dnf-plugins-core 2>/dev/null
            
            if [[ "$DISTRO" == "fedora" ]]; then
                sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            else
                sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null || \
                    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null
            fi
            
            sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null || \
                sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null
            ;;
            
        arch|manjaro)
            log_info "Using pacman-based installation for $DISTRO..."
            sudo pacman -S --noconfirm docker docker-compose
            ;;
            
        macos)
            log_error "Docker Desktop is required on macOS."
            log_info "Download from: https://docs.docker.com/desktop/install/mac-install/"
            log_info "After installing Docker Desktop, run this script again."
            exit 1
            ;;
            
        *)
            log_warn "Unrecognized distro '$DISTRO'. Trying convenience script..."
            curl -fsSL https://get.docker.com | sudo sh
            ;;
    esac
    
    # Start and enable Docker
    sudo systemctl start docker 2>/dev/null || true
    sudo systemctl enable docker 2>/dev/null || true
    
    # Add current user to docker group
    if ! groups "$USER" | grep -q docker 2>/dev/null; then
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        log_warn "Added $USER to docker group. You may need to log out and back in."
        # Use newgrp for this session
        NEED_NEWGRP=true
    fi
    
    log_success "Docker installed successfully!"
}

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | sed 's/,//')
    log_success "Docker is installed (v${DOCKER_VERSION})"
    
    NEED_SUDO=false
    # Verify Docker daemon is running and if we need sudo
    if ! docker info &>/dev/null; then
        if sudo docker info &>/dev/null; then
            NEED_SUDO=true
            log_info "Docker requires sudo privileges. Using sudo for docker commands."
        else
            log_warn "Docker daemon is not running. Attempting to start..."
            sudo systemctl start docker 2>/dev/null || {
                log_error "Failed to start Docker daemon. Please start it manually."
                exit 1
            }
            log_success "Docker daemon started."
            # Check again if we need sudo after starting
            if ! docker info &>/dev/null && sudo docker info &>/dev/null; then
                NEED_SUDO=true
                log_info "Docker requires sudo privileges. Using sudo for docker commands."
            fi
        fi
    fi

    # Create a wrapper function if sudo is needed
    if [ "$NEED_SUDO" = true ]; then
        docker() { command sudo docker "$@"; }
    fi
else
    log_warn "Docker is not installed on this machine."
    
    if [ "$SKIP_DOCKER_INSTALL" = true ]; then
        log_error "Docker is required but --skip-docker-install was specified."
        exit 1
    fi
    
    echo ""
    read -p "$(echo -e ${YELLOW}Install Docker now? [Y/n]:${NC} )" -n 1 -r REPLY
    echo ""
    
    if [[ "$REPLY" =~ ^[Nn]$ ]]; then
        log_error "Docker is required. Exiting."
        exit 1
    fi
    
    install_docker
fi

# Verify docker compose
if docker compose version &>/dev/null; then
    COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || echo "unknown")
    log_success "Docker Compose is available (v${COMPOSE_VERSION})"
elif command -v docker-compose &>/dev/null; then
    log_success "docker-compose (standalone) is available"
else
    log_warn "Docker Compose not found. Installing plugin..."
    sudo apt-get install -y docker-compose-plugin 2>/dev/null || \
        sudo yum install -y docker-compose-plugin 2>/dev/null || \
        sudo dnf install -y docker-compose-plugin 2>/dev/null || true
fi

# ── Port Selection ───────────────────────────────────────────
log_step "Port Configuration"

if [ -z "$PORT" ]; then
    if [ "$INTERACTIVE" = true ]; then
        # Launch the interactive web page
        SETUP_PAGE_PORT=18630
        log_info "Starting interactive setup page on port ${SETUP_PAGE_PORT}..."
        
        # Check if the setup page can bind to its port using bash sockets
        while (echo > /dev/tcp/127.0.0.1/${SETUP_PAGE_PORT}) >/dev/null 2>&1; do
            SETUP_PAGE_PORT=$((SETUP_PAGE_PORT + 1))
        done
        
        # Serve the interactive page using Python
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        SETUP_HTML="${SCRIPT_DIR}/setup-page.html"
        
        if [ ! -f "$SETUP_HTML" ]; then
            log_error "setup-page.html not found at ${SETUP_HTML}"
            log_info "Falling back to CLI mode."
            read -p "$(echo -e ${CYAN}Enter port for Bench Manager [${DEFAULT_PORT}]:${NC} )" INPUT_PORT
            PORT="${INPUT_PORT:-$DEFAULT_PORT}"
        else
            # Create a small Python HTTP server that serves the page and captures the port
            PORT_FILE=$(mktemp)
            
            python3 -c "
import http.server, socketserver, json, os, sys, signal, urllib.parse

class MyServer(socketserver.TCPServer):
    allow_reuse_address = True

PORT = ${SETUP_PAGE_PORT}
PORT_FILE = '${PORT_FILE}'
HTML_FILE = '${SETUP_HTML}'

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            with open(HTML_FILE, 'r') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(content.encode())
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        if self.path == '/api/start':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode()
            data = json.loads(body)
            port = data.get('port', ${DEFAULT_PORT})
            with open(PORT_FILE, 'w') as f:
                f.write(str(port))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'port': port}).encode())
            # Schedule server shutdown
            import threading
            threading.Timer(0.5, lambda: os._exit(0)).start()
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logs

with MyServer(('', PORT), Handler) as httpd:
    print(f'SETUP_URL=http://localhost:{PORT}')
    sys.stdout.flush()
    httpd.serve_forever()
" &
            SETUP_PID=$!
            sleep 1
            
            # Open browser
            SETUP_URL="http://localhost:${SETUP_PAGE_PORT}"
            log_info "Opening setup page: ${SETUP_URL}"
            
            if command -v xdg-open &>/dev/null; then
                xdg-open "$SETUP_URL" 2>/dev/null &
            elif command -v open &>/dev/null; then
                open "$SETUP_URL" 2>/dev/null &
            else
                log_info "Please open ${SETUP_URL} in your browser."
            fi
            
            log_info "Waiting for port selection from the setup page..."
            
            # Wait for the Python server to exit (user submitted form)
            wait $SETUP_PID 2>/dev/null || true
            
            if [ -s "$PORT_FILE" ]; then
                PORT=$(cat "$PORT_FILE")
                log_success "Port ${PORT} selected from setup page."
            else
                log_warn "No port received from setup page. Using default."
                PORT=$DEFAULT_PORT
            fi
            
            rm -f "$PORT_FILE"
        fi
    else
        PORT=$DEFAULT_PORT
        log_info "Using default port: ${PORT}"
    fi
fi

# Validate port
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    log_error "Invalid port: ${PORT}. Must be between 1 and 65535."
    exit 1
fi

# Check if port is already in use
if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || \
   netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    log_error "Port ${PORT} is already in use!"
    log_info "Choose a different port with: $0 --port <PORT>"
    exit 1
fi

log_success "Port ${PORT} is available."

# ── Pull Latest Image ────────────────────────────────────────
log_step "Pulling Latest Image"

log_info "Pulling ${DOCKER_IMAGE}..."
if docker pull "$DOCKER_IMAGE"; then
    log_success "Image pulled successfully."
else
    log_error "Failed to pull image. Check your internet connection."
    exit 1
fi

# ── Stop Existing Container ──────────────────────────────────
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log_step "Stopping Existing Container"
    log_info "Stopping and removing existing '${CONTAINER_NAME}' container..."
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
    log_success "Old container removed."
fi

# ── Launch Container ─────────────────────────────────────────
log_step "Starting Bench Manager"

log_info "Launching container on port ${PORT}..."

docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "${PORT}:8080" \
    -v bench_manager_mysql:/var/lib/mysql \
    -v bench_manager_sites:/home/frappe/frappe-bench/sites \
    -v bench_manager_logs:/home/frappe/frappe-bench/logs \
    -v bench_manager_supervisor_logs:/var/log/supervisor \
    -e "MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD}" \
    -e "FRAPPE_ADMIN_PASSWORD=${ADMIN_PASSWORD}" \
    --memory=4g \
    --memory-reservation=1g \
    --stop-timeout=60 \
    --log-driver=json-file \
    --log-opt max-size=50m \
    --log-opt max-file=5 \
    "$DOCKER_IMAGE"

log_success "Container started!"

# ── Wait for Health Check ────────────────────────────────────
log_step "Waiting for Services"

log_info "Bench Manager is initializing (this may take 2-4 minutes on first run)..."

HEALTHY=false
MAX_WAIT=300  # 5 minutes
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_NAME" 2>/dev/null || echo "starting")
    
    # Try manual check as fallback/verification
    if curl -fsS "http://localhost:${PORT}/api/method/ping" &>/dev/null; then
        HEALTHY=true
        break
    fi

    case "$STATUS" in
        healthy)
            HEALTHY=true
            break
            ;;
        unhealthy)
            # Log warning but continue manual polling just in case it recovers
            if [ $((ELAPSED % 30)) -eq 0 ]; then
                log_warn "Docker healthcheck says unhealthy, but still polling..."
            fi
            printf "."
            sleep 5
            ELAPSED=$((ELAPSED + 5))
            ;;
        *)
            # Show progress dots
            printf "."
            sleep 5
            ELAPSED=$((ELAPSED + 5))
            ;;
    esac
done

echo ""

if [ "$HEALTHY" = true ]; then
    echo ""
    echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║                                               ║${NC}"
    echo -e "${GREEN}${BOLD}║   ${NC}${CYAN}${BOLD}Bench Manager is Ready!${NC}${GREEN}${BOLD}                    ║${NC}"
    echo -e "${GREEN}${BOLD}║                                               ║${NC}"
    echo -e "${GREEN}${BOLD}║${NC}   URL:      ${BOLD}http://localhost:${PORT}${NC}${GREEN}${BOLD}             ║${NC}"
    echo -e "${GREEN}${BOLD}║${NC}   User:     ${BOLD}Administrator${NC}${GREEN}${BOLD}                      ║${NC}"
    echo -e "${GREEN}${BOLD}║${NC}   Password: ${BOLD}${ADMIN_PASSWORD}${NC}${GREEN}${BOLD}                            ║${NC}"
    echo -e "${GREEN}${BOLD}║                                               ║${NC}"
    echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════════╝${NC}"
    echo ""
    
    # Try to open browser
    BENCH_URL="http://localhost:${PORT}"
    if command -v xdg-open &>/dev/null; then
        xdg-open "$BENCH_URL" 2>/dev/null &
    elif command -v open &>/dev/null; then
        open "$BENCH_URL" 2>/dev/null &
    fi
    
    log_info "Useful commands:"
    echo "  docker logs -f ${CONTAINER_NAME}    # View logs"
    echo "  docker stop ${CONTAINER_NAME}       # Stop"
    echo "  docker start ${CONTAINER_NAME}      # Start"
    echo "  docker restart ${CONTAINER_NAME}    # Restart"
    echo ""
else
    log_warn "Container is still initializing. It may need more time."
    log_info "Check status with: docker logs -f ${CONTAINER_NAME}"
    log_info "URL will be available at: http://localhost:${PORT}"
fi
