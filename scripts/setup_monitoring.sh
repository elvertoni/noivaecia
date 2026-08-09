#!/usr/bin/env bash
# =============================================================================
# setup_monitoring.sh — guia idempotente da monitoria do Noivas & Cia
# =============================================================================
set -Eeuo pipefail
if [[ ! -t 0 && -e /dev/tty ]]; then exec </dev/tty; fi

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 \
   && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD="$(tput bold)"; RESET="$(tput sgr0)"
  RED="$(tput setaf 1)"; GREEN="$(tput setaf 2)"; YELLOW="$(tput setaf 3)"
  BLUE="$(tput setaf 4)"; CYAN="$(tput setaf 6)"; GREY="$(tput setaf 8)"
else
  BOLD=""; RESET=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; GREY=""
fi

LOG_FILE="${HOME:-/root}/setup_monitoring.log"
STEP=0
PHASE_LABEL="MONITORIA"

_logfile() { printf '%s %s\n' "[$(date '+%F %H:%M:%S')]" "$1" >>"$LOG_FILE" 2>/dev/null || true; }
banner() {
  echo ""
  echo "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
  printf "${BOLD}${CYAN}║${RESET} %-60s ${BOLD}${CYAN}║${RESET}\n" "$1"
  echo "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
}
step() {
  STEP=$((STEP + 1)); echo ""
  echo "${BOLD}${BLUE}▶ ${PHASE_LABEL} — PASSO ${STEP}:${RESET} ${BOLD}$1${RESET}"
  echo "${GREY}──────────────────────────────────────────────────────────────${RESET}"
  _logfile "PASSO ${STEP}: $1"
}
info()    { echo "  ${BLUE}ℹ${RESET}  $1"; _logfile "INFO  $1"; }
ok()      { echo "  ${GREEN}✔${RESET}  $1"; _logfile "OK    $1"; }
warn()    { echo "  ${YELLOW}⚠${RESET}  $1"; _logfile "WARN  $1"; }
skip()    { echo "  ${GREEN}✓${RESET}  ${GREY}$1 (já existe — pulando)${RESET}"; _logfile "SKIP  $1"; }
working() { echo "  ${CYAN}⏳${RESET} $1..."; _logfile "WORK  $1"; }
action_box() {
  echo ""; echo "${BOLD}${YELLOW}  ┌──────────────────────── AÇÃO NECESSÁRIA ───────────────────────┐${RESET}"
  while [[ $# -gt 0 ]]; do echo "${BOLD}${YELLOW}  │${RESET} $1"; shift; done
  echo "${BOLD}${YELLOW}  └────────────────────────────────────────────────────────────────┘${RESET}"; echo ""
}
pause_enter() { echo ""; read -r -p "  ${BOLD}${GREEN}➜ Pressione ENTER para continuar...${RESET} " _ || true; }

on_error() {
  local exit_code=$1 line=$2 cmd=$3
  echo ""; echo "${BOLD}${RED}ERRO: o setup parou no passo ${STEP}, linha ${line}.${RESET}"
  echo "  Comando: $cmd"; echo "  Código: $exit_code"; echo "  Log: $LOG_FILE"
  _logfile "ERRO exit=$exit_code line=$line cmd=<$cmd>"
  exit "$exit_code"
}
trap 'on_error $? ${LINENO} "$BASH_COMMAND"' ERR

have() { command -v "$1" >/dev/null 2>&1; }
ask() {
  local prompt="$1" default="${2:-}" answer
  if [[ -n "$default" ]]; then
    read -r -p "  ${CYAN}❯ ${prompt}${RESET} [${default}]: " answer || true
    printf '%s' "${answer:-$default}"
  else
    read -r -p "  ${CYAN}❯ ${prompt}${RESET}: " answer || true
    printf '%s' "$answer"
  fi
}
ask_secret() { local answer; read -r -s -p "  ${CYAN}❯ $1${RESET}: " answer || true; echo "" >&2; printf '%s' "$answer"; }
gen_secret() { openssl rand -base64 36 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c "${1:-24}" || true; }
get_env_var() { grep -m1 "^$1=" .env 2>/dev/null | cut -d= -f2- || true; }
set_env_var() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp)"
  [[ -f .env ]] && grep -v "^${key}=" .env >"$temporary" || true
  printf '%s=%s\n' "$key" "$value" >>"$temporary"
  mv "$temporary" .env
  chmod 600 .env
}
load_env() {
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"; case "$line" in ''|\#*) continue ;; esac
    [[ "${line#*=}" == "$line" ]] && continue
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
detect_public_ip() {
  local ip=""
  ip="$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  [[ -n "$ip" ]] || ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  printf '%s' "$ip" | tr -d '[:space:]'
}

banner "NOIVAS & CIA — MONITORIA, OBSERVABILIDADE E LOGS"
echo "  Esta stack é separada da aplicação e pode ser atualizada sem redeploy do site."

# 1. Localizar o projeto.
step "Localizando a pasta do projeto"
REPO_DIR=""
if [[ -n "${BASH_SOURCE:-}" ]] \
   && [[ -f "$(dirname "${BASH_SOURCE[0]}")/../monitoring-stack.yml" ]]; then
  REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
elif [[ -f monitoring-stack.yml ]]; then
  REPO_DIR="$(pwd)"
else
  REPO_DIR="$(ask "Caminho completo do projeto" "$HOME/noivaecia")"
fi
cd "$REPO_DIR"
[[ -f monitoring-stack.yml ]] || { echo "  ${RED}monitoring-stack.yml não encontrado.${RESET}"; exit 1; }
[[ -f .env ]] || { echo "  ${RED}.env ausente; rode setup_deploy.sh primeiro.${RESET}"; exit 1; }
ok "Projeto: $REPO_DIR"

# 2. Docker, manager e rede já usada pelo Traefik.
step "Verificando Docker, Swarm manager e Traefik"
have docker || { echo "  ${RED}Docker ausente; rode setup_deploy.sh primeiro.${RESET}"; exit 1; }
docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q active \
  || { echo "  ${RED}Swarm inativo; rode setup_deploy.sh primeiro.${RESET}"; exit 1; }
[[ "$(docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null)" == "true" ]] \
  || { echo "  ${RED}Este nó não é manager do Swarm.${RESET}"; exit 1; }
if ! docker network ls --format '{{.Name}}' | grep -qx traefik_public; then
  action_box "A rede traefik_public não existe." "Rode primeiro: bash scripts/setup_deploy.sh"
  exit 1
fi
ok "Swarm manager e traefik_public disponíveis"

# 3. Domínio base.
step "Lendo o domínio do projeto"
DOMAIN_BASE="$(get_env_var DOMAIN)"
if [[ -z "$DOMAIN_BASE" ]]; then
  DOMAIN_BASE="$(ask "Domínio base do projeto" "noivaseciabandeirantes.com.br")"
  set_env_var DOMAIN "$DOMAIN_BASE"
fi
ok "DOMAIN = $DOMAIN_BASE"

# 4. Grafana e MCP: domínios, credenciais e duas camadas de autenticação.
step "Configurando Grafana e servidor MCP"
GRAFANA_DOMAIN_CUR="$(get_env_var GRAFANA_DOMAIN)"
GRAFANA_DOMAIN_VAL="$(ask "Domínio do Grafana" "${GRAFANA_DOMAIN_CUR:-grafana.${DOMAIN_BASE}}")"
set_env_var GRAFANA_DOMAIN "$GRAFANA_DOMAIN_VAL"

GRAFANA_USER_CUR="$(get_env_var GRAFANA_ADMIN_USER)"
GRAFANA_USER_VAL="$(ask "Usuário administrador do Grafana" "${GRAFANA_USER_CUR:-admin}")"
set_env_var GRAFANA_ADMIN_USER "$GRAFANA_USER_VAL"

GRAFANA_PASS_VAL="$(get_env_var GRAFANA_ADMIN_PASSWORD)"
if [[ -z "$GRAFANA_PASS_VAL" || "$GRAFANA_PASS_VAL" == troque-* ]]; then
  GRAFANA_PASS_INPUT="$(ask_secret "Senha do Grafana (ENTER gera uma forte)")"
  if [[ -z "$GRAFANA_PASS_INPUT" ]]; then
    GRAFANA_PASS_VAL="$(gen_secret 24)"
    action_box "Senha do Grafana gerada (guarde agora): ${BOLD}${GRAFANA_PASS_VAL}${RESET}"
  else
    GRAFANA_PASS_VAL="$GRAFANA_PASS_INPUT"
  fi
  set_env_var GRAFANA_ADMIN_PASSWORD "$GRAFANA_PASS_VAL"
else
  skip "Senha do Grafana"
fi

MCP_DOMAIN_CUR="$(get_env_var MCP_DOMAIN)"
MCP_DOMAIN_VAL="$(ask "Domínio do servidor MCP" "${MCP_DOMAIN_CUR:-mcp.${DOMAIN_BASE}}")"
set_env_var MCP_DOMAIN "$MCP_DOMAIN_VAL"

MCP_AUTH_CUR="$(get_env_var MCP_BASICAUTH_USERS)"
if [[ -z "$MCP_AUTH_CUR" || "$MCP_AUTH_CUR" == *troque* \
   || "$MCP_AUTH_CUR" == *'...' ]]; then
  if ! have htpasswd; then
    action_box "Instale o gerador bcrypt:" "sudo apt-get update && sudo apt-get install -y apache2-utils"
    pause_enter
  fi
  if have htpasswd; then
    MCP_USER="$(ask "Usuário do Basic Auth do MCP" "mcp")"
    MCP_PASSWORD="$(ask_secret "Senha do Basic Auth (ENTER gera uma forte)")"
    if [[ -z "$MCP_PASSWORD" ]]; then
      MCP_PASSWORD="$(gen_secret 24)"
      action_box "Senha MCP gerada (guarde agora): ${BOLD}${MCP_PASSWORD}${RESET}" \
        "O cliente envia usuario:senha em Base64; nunca envie o hash bcrypt."
    fi
    MCP_HASH="$(htpasswd -nbB "$MCP_USER" "$MCP_PASSWORD")"
    set_env_var MCP_BASICAUTH_USERS "$MCP_HASH"
    MCP_BASIC_HEADER="$(printf '%s:%s' "$MCP_USER" "$MCP_PASSWORD" | base64 | tr -d '\n')"
    ok "Basic Auth bcrypt configurado; o hash foi salvo com $ simples."
  else
    set_env_var MCP_BASICAUTH_USERS ""
    warn "htpasswd ainda ausente; o router MCP permanecerá inoperante."
  fi
else
  skip "Basic Auth do MCP"
  MCP_BASIC_HEADER=""
fi

MCP_TOKEN_CUR="$(get_env_var GRAFANA_SERVICE_ACCOUNT_TOKEN)"
if [[ -z "$MCP_TOKEN_CUR" || "$MCP_TOKEN_CUR" == 'glsa_...' ]]; then
  set_env_var GRAFANA_SERVICE_ACCOUNT_TOKEN ""
  action_box "Depois que o Grafana subir, crie um Service Account Viewer:" \
    "Administration → Users and access → Service accounts → Add token" \
    "Grave o glsa_... em GRAFANA_SERVICE_ACCOUNT_TOKEN e rode deploy_monitoring.sh."
else
  skip "Token do Service Account do Grafana"
fi
ok "GRAFANA_DOMAIN=$GRAFANA_DOMAIN_VAL e MCP_DOMAIN=$MCP_DOMAIN_VAL"

# 5. Retenção.
step "Definindo retenção de métricas e logs"
[[ -n "$(get_env_var PROMETHEUS_RETENTION)" ]] || set_env_var PROMETHEUS_RETENTION 15d
[[ -n "$(get_env_var LOKI_RETENTION)" ]] || set_env_var LOKI_RETENTION 360h
ok "Prometheus=$(get_env_var PROMETHEUS_RETENTION); Loki=$(get_env_var LOKI_RETENTION)"

# 6. DNS dos dois endpoints públicos.
step "Orientando os registros DNS"
PUBLIC_IP="$(detect_public_ip)"
action_box "Crie/confirme dois registros DNS do tipo A para ${PUBLIC_IP:-<IP da VPS>}:" \
  "${GRAFANA_DOMAIN_VAL}" "${MCP_DOMAIN_VAL}" \
  "O TLS DNS-01 pode levar 1–2 minutos após a propagação."
pause_enter

# 7. Rede privada compartilhada apenas pela monitoria.
step "Criando a rede monitoring"
if docker network ls --format '{{.Name}}' | grep -qx monitoring; then
  skip "Rede monitoring"
else
  docker network create --driver overlay --attachable monitoring >>"$LOG_FILE" 2>&1
  ok "Rede monitoring criada"
fi

# 8. Configurações e bind mounts absolutos.
step "Validando os arquivos de configuração"
for file in \
  monitoring/prometheus/prometheus.yml \
  monitoring/prometheus/alert_rules.yml \
  monitoring/loki/loki-config.yml \
  monitoring/promtail/promtail-config.yml \
  monitoring/grafana/provisioning/datasources/datasources.yml \
  monitoring/grafana/provisioning/dashboards/dashboards.yml \
  monitoring/grafana/dashboards/noivas-cia-overview.json; do
  [[ -f "$file" ]] || { echo "  ${RED}Arquivo ausente: $file${RESET}"; exit 1; }
done
set_env_var MONITORING_CONFIG_DIR "$REPO_DIR/monitoring"
ok "MONITORING_CONFIG_DIR=$REPO_DIR/monitoring"

# 9. Instrumentação é independente do primeiro deploy da monitoria.
step "Explicando a ativação do /metrics"
info "A monitoria sobe sem rebuild do app; o alvo django ficará DOWN até a lib estar na imagem."
action_box "Quando puder, ative /metrics com um deploy normal do app:" \
  "bash scripts/deploy.sh" "O endpoint não é publicado pelo Traefik."

# 10. Parser seguro e deploy da stack separada.
step "Publicando a stack monitoring"
load_env
export MONITORING_CONFIG_DIR="$REPO_DIR/monitoring"
export PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-15d}"
export LOKI_RETENTION="${LOKI_RETENTION:-360h}"
for variable in MCP_DOMAIN GRAFANA_SERVICE_ACCOUNT_TOKEN MCP_BASICAUTH_USERS; do
  [[ -n "${!variable:-}" ]] || warn "$variable vazio; o grafana-mcp ficará inoperante."
done
working "docker stack deploy -c monitoring-stack.yml monitoring"
docker stack deploy -c monitoring-stack.yml monitoring >>"$LOG_FILE" 2>&1
ok "Stack monitoring publicada"

# 11. Estado dos serviços.
step "Conferindo os serviços"
docker service ls --format "table {{.Name}}\t{{.Mode}}\t{{.Replicas}}\t{{.Image}}" \
  | grep -E '^NAME|^monitoring_' || true
info "A inicialização e a emissão TLS podem levar alguns minutos."

# 12. Handoff operacional.
step "Mostrando como acessar e validar"
banner "MONITORIA CONFIGURADA"
echo "  Grafana: https://${GRAFANA_DOMAIN_VAL}"
echo "  Dashboard: pasta 'Noivas & Cia' → 'Noivas & Cia — Visão Geral'"
echo "  Alvos: Explore → Prometheus → up"
echo "  Logs: Explore → Loki → {stack=\"noivaecia\"}"
echo "  Dashboards: 1860, 14282, 21154, 13496, 9528 ou 17658"
echo "  MCP: https://${MCP_DOMAIN_VAL}/mcp"
if [[ -n "${MCP_BASIC_HEADER:-}" ]]; then
  echo "  Header: Authorization: Basic ${MCP_BASIC_HEADER}"
fi
echo "  Documentação: docs/monitoring.md"
echo "  Redeploy: bash scripts/deploy_monitoring.sh"
echo "  Log deste guia: $LOG_FILE"
_logfile "Setup concluído"
