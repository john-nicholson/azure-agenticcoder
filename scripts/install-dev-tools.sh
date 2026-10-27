#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tools=''
while (($#)); do
  case "$1" in
    --tools) [[ $# -ge 2 ]] || { echo 'Missing --tools value' >&2; exit 2; }; tools=$2; shift 2 ;;
    --help) echo 'Usage: sudo install-dev-tools.sh [--tools docker,python,dotnet,terraform,node]'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -z $tools || $tools =~ ^(docker|python|dotnet|terraform|node)(,(docker|python|dotnet|terraform|node))*$ ]] || { echo 'Invalid tool selection' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
exec 9>/var/lock/dev-bootstrap.lock
flock -w 600 9
exec > >(tee -a /var/log/dev-tools.log) 2>&1
trap 'echo "Tool installation failed at line $LINENO" >&2' ERR
export DEBIAN_FRONTEND=noninteractive
apt_install() {
  apt-get -o DPkg::Lock::Timeout=300 \
    -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install -y "$@"
}
signed_repo() {
  local name=$1 key_url=$2 repo=$3
  install -d -m 0755 /etc/apt/keyrings
  curl --retry 5 -fsSL "$key_url" -o "/etc/apt/keyrings/$name.asc"
  chmod 0644 "/etc/apt/keyrings/$name.asc"
  printf '%s\n' "$repo" > "/etc/apt/sources.list.d/$name.list"
  apt-get -o Acquire::Retries=5 update
}
apt-get -o Acquire::Retries=5 update
source "$ROOT/tools/base.sh"
install_base
declare -a completed=()
IFS=',' read -r -a requested <<< "$tools"
for tool in "${requested[@]}"; do
  [[ -n $tool ]] || continue
  [[ " ${completed[*]} " != *" $tool "* ]] || continue
  source "$ROOT/tools/$tool.sh"
  echo "Installing $tool"
  "install_$tool"
  completed+=("$tool")
done
printf 'Completed: base %s\n' "${completed[*]}"
