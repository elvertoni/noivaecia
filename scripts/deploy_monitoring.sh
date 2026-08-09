#!/bin/bash
set -euo pipefail

# ── Noivas & Cia — deploy independente da stack de monitoramento ──
# Uso: ./scripts/deploy_monitoring.sh [--clean]

STACK_NAME="monitoring"
STACK_FILE="monitoring-stack.yml"
CLEAN=0
[ "${1:-}" = "--clean" ] && CLEAN=1

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $1"; }
error() { echo -e "${RED}[ERRO]${NC} $1"; exit 1; }

load_env() {
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|\#*) continue ;; esac
        [ "${line#*=}" = "$line" ] && continue
        key="${line%%=*}"; value="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export "$key=$value"
    done < .env
}

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
[ -f "$STACK_FILE" ] || error "$STACK_FILE não encontrado em $REPO_DIR"

echo "=== Noivas & Cia — Deploy da monitoria ($STACK_NAME) ==="

# Mantém os arquivos da VPS atualizados. Falha de rede ou árvore suja não
# interrompe a reconciliação; SKIP_GIT_PULL=1 é o escape hatch deliberado.
if [ "${SKIP_GIT_PULL:-0}" != "1" ] \
   && command -v git >/dev/null 2>&1 && [ -d .git ]; then
    echo "--- Atualizando o repositório (git pull --ff-only) ---"
    git pull --ff-only \
        && info "Repositório atualizado" \
        || warn "git pull falhou — seguindo com os arquivos atuais."
fi

command -v docker >/dev/null 2>&1 || error "Docker não encontrado."
docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q active \
    || error "Este nó não está em um Swarm ativo."
[ "$(docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null)" = "true" ] \
    || error "Execute este script em um nó manager do Swarm."

[ -f .env ] || error "Arquivo .env não encontrado em $REPO_DIR"
load_env
info ".env carregado com parser seguro"

[ -n "${GRAFANA_ADMIN_PASSWORD:-}" ] \
    || error "GRAFANA_ADMIN_PASSWORD está vazio no .env."
[[ "$GRAFANA_ADMIN_PASSWORD" != troque-* ]] \
    || error "Troque a senha de exemplo em GRAFANA_ADMIN_PASSWORD."
[ -n "${GRAFANA_DOMAIN:-}" ] \
    || error "GRAFANA_DOMAIN está vazio no .env."

for variable in MCP_DOMAIN GRAFANA_SERVICE_ACCOUNT_TOKEN MCP_BASICAUTH_USERS; do
    value="${!variable:-}"
    [ -n "$value" ] && [ "$value" != 'glsa_...' ] && [[ "$value" != *'...' ]] \
        || warn "$variable vazio — somente o grafana-mcp ficará inoperante."
done

export MONITORING_CONFIG_DIR="$REPO_DIR/monitoring"
export PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-15d}"
export LOKI_RETENTION="${LOKI_RETENTION:-360h}"
export GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"

for file in \
    monitoring/prometheus/prometheus.yml \
    monitoring/prometheus/alert_rules.yml \
    monitoring/loki/loki-config.yml \
    monitoring/promtail/promtail-config.yml \
    monitoring/grafana/provisioning/datasources/datasources.yml \
    monitoring/grafana/provisioning/dashboards/dashboards.yml \
    monitoring/grafana/dashboards/noivas-cia-overview.json; do
    [ -f "$file" ] || error "Arquivo obrigatório ausente: $file"
done

docker network ls --format '{{.Name}}' | grep -qx monitoring \
    || docker network create --driver overlay --attachable monitoring >/dev/null
docker network ls --format '{{.Name}}' | grep -qx traefik_public \
    || error "Rede 'traefik_public' ausente; rode setup_deploy.sh primeiro."
info "Redes externas disponíveis"

if [ "$CLEAN" = 1 ]; then
    warn "Removendo a stack; os volumes nomeados serão preservados."
    docker stack rm "$STACK_NAME" || true
    sleep 15
fi

docker stack deploy -c "$STACK_FILE" "$STACK_NAME"
info "Stack reconciliada"

if [ "$CLEAN" = 0 ]; then
    for service in prometheus grafana grafana-mcp loki promtail node-exporter cadvisor; do
        docker service update --force "${STACK_NAME}_${service}" >/dev/null 2>&1 \
            && info "rollout: ${STACK_NAME}_${service}" \
            || warn "rollout pendente: ${STACK_NAME}_${service}"
    done
fi

sleep 10

echo "--- Status ---"
docker service ls --format "table {{.Name}}\t{{.Mode}}\t{{.Replicas}}\t{{.Image}}" \
    | grep -E "^NAME|^${STACK_NAME}_" || true

echo ""
echo "Grafana: https://${GRAFANA_DOMAIN} (usuário ${GRAFANA_ADMIN_USER})"
[ -n "${MCP_DOMAIN:-}" ] && echo "MCP: https://${MCP_DOMAIN}/mcp"
echo "Logs: docker service logs -f ${STACK_NAME}_prometheus"
