#!/usr/bin/env bash
# Shared lifecycle helpers for the localhost.run tunnel owned by this demo.

LOCAL_TUNNEL_STATE_DIR="${TMPDIR:-/tmp}/askdb-local-test-tunnel-${UID:-$(id -u)}"
LOCAL_TUNNEL_PID_FILE="$LOCAL_TUNNEL_STATE_DIR/ssh.pid"
LOCAL_TUNNEL_KEEPALIVE_PID_FILE="$LOCAL_TUNNEL_STATE_DIR/keepalive.pid"
LOCAL_TUNNEL_URL_FILE="$LOCAL_TUNNEL_STATE_DIR/url"
LOCAL_TUNNEL_LOG_FILE="$LOCAL_TUNNEL_STATE_DIR/ssh.log"
LOCAL_TUNNEL_INPUT_FIFO="$LOCAL_TUNNEL_STATE_DIR/ssh.stdin"
LOCAL_TUNNEL_URL=''
LOCAL_TUNNEL_SSH_PID=''

_local_tunnel_process_matches() {
  local pid="$1" expected_command="$2" command_line
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"$expected_command"* ]]
}

_local_tunnel_stop_process() {
  local pid="$1" expected_command="$2" attempt
  _local_tunnel_process_matches "$pid" "$expected_command" || return 0
  kill -TERM "$pid" 2>/dev/null || true
  for attempt in {1..10}; do
    _local_tunnel_process_matches "$pid" "$expected_command" || return 0
    sleep 0.5
  done
  if _local_tunnel_process_matches "$pid" "$expected_command"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

local_tunnel_stop() {
  local ssh_pid='' keepalive_pid=''
  if [[ -f "$LOCAL_TUNNEL_PID_FILE" ]]; then
    IFS= read -r ssh_pid < "$LOCAL_TUNNEL_PID_FILE" || true
  fi
  if [[ -f "$LOCAL_TUNNEL_KEEPALIVE_PID_FILE" ]]; then
    IFS= read -r keepalive_pid < "$LOCAL_TUNNEL_KEEPALIVE_PID_FILE" || true
  fi
  _local_tunnel_stop_process "$ssh_pid" 'nokey@localhost.run'
  _local_tunnel_stop_process "$keepalive_pid" 'tail -f /dev/null'
  rm -f -- "$LOCAL_TUNNEL_PID_FILE" "$LOCAL_TUNNEL_KEEPALIVE_PID_FILE" \
    "$LOCAL_TUNNEL_URL_FILE" "$LOCAL_TUNNEL_LOG_FILE" "$LOCAL_TUNNEL_INPUT_FIFO"
}

local_tunnel_start() {
  local timeout_seconds="${1:-30}" attempt url keepalive_pid ssh_pid
  command -v ssh >/dev/null 2>&1 || { printf '未找到 ssh；请安装 OpenSSH 客户端。\n' >&2; return 1; }
  command -v tail >/dev/null 2>&1 || { printf '未找到 tail，无法保持 SSH 隧道会话。\n' >&2; return 1; }
  mkdir -p -- "$LOCAL_TUNNEL_STATE_DIR"
  chmod 700 "$LOCAL_TUNNEL_STATE_DIR"
  local_tunnel_stop
  : > "$LOCAL_TUNNEL_LOG_FILE"
  chmod 600 "$LOCAL_TUNNEL_LOG_FILE"
  mkfifo "$LOCAL_TUNNEL_INPUT_FIFO"
  chmod 600 "$LOCAL_TUNNEL_INPUT_FIFO"

  # localhost.run prints the assigned hostname in its SSH login banner. Keep
  # stdin open so that the banner-producing session also keeps the tunnel alive.
  nohup tail -f /dev/null > "$LOCAL_TUNNEL_INPUT_FIFO" 2>/dev/null < /dev/null &
  keepalive_pid=$!
  printf '%s\n' "$keepalive_pid" > "$LOCAL_TUNNEL_KEEPALIVE_PID_FILE"
  chmod 600 "$LOCAL_TUNNEL_KEEPALIVE_PID_FILE"

  # BatchMode avoids leaving an unseen password or host-key prompt in a background process.
  # If this host is not trusted yet, connect once interactively and verify the SSH fingerprint.
  nohup ssh -T \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -R 80:127.0.0.1:3001 nokey@localhost.run \
    < "$LOCAL_TUNNEL_INPUT_FIFO" > "$LOCAL_TUNNEL_LOG_FILE" 2>&1 &
  ssh_pid=$!
  LOCAL_TUNNEL_SSH_PID="$ssh_pid"
  printf '%s\n' "$ssh_pid" > "$LOCAL_TUNNEL_PID_FILE"
  chmod 600 "$LOCAL_TUNNEL_PID_FILE"

  for ((attempt = 0; attempt < timeout_seconds; attempt++)); do
    url="$(grep -Eo 'https://[0-9a-f]{12,32}\.lhr\.life' "$LOCAL_TUNNEL_LOG_FILE" | tail -n 1 || true)"
    if [[ "$url" =~ ^https://[0-9a-f]{12,32}\.lhr\.life$ ]]; then
      LOCAL_TUNNEL_URL="$url"
      printf '%s\n' "$LOCAL_TUNNEL_URL" > "$LOCAL_TUNNEL_URL_FILE"
      chmod 600 "$LOCAL_TUNNEL_URL_FILE"
      return 0
    fi
    if ! _local_tunnel_process_matches "$ssh_pid" 'nokey@localhost.run'; then
      break
    fi
    sleep 1
  done

  printf 'localhost.run 隧道未能在 %s 秒内分配 HTTPS 地址。\n' "$timeout_seconds" >&2
  if [[ -s "$LOCAL_TUNNEL_LOG_FILE" ]]; then
    tail -n 20 "$LOCAL_TUNNEL_LOG_FILE" >&2
  fi
  local_tunnel_stop
  return 1
}
