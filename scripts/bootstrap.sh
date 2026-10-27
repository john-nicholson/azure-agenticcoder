#!/usr/bin/env bash
set -Eeuo pipefail
exec > >(tee -a /var/log/dev-bootstrap.log) 2>&1
trap 'echo "Bootstrap failed at line $LINENO" >&2' ERR
rm -f /var/lib/dev-bootstrap-ready
ROOT=/opt/dev-bootstrap
config=$ROOT/settings.json
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o DPkg::Lock::Timeout=300 install -y jq curl ca-certificates ufw
user=$(jq -r .adminUsername "$config")
vpn=$(jq -r .vpn "$config")
tools=$(jq -r .tools "$config")

# Azure SCSI LUN 0: never fall back to arbitrary /dev/sdX devices.
device=/dev/disk/azure/scsi1/lun0
for ((i=0; i<120; i++)); do [[ -b $device ]] && break; sleep 2; done
[[ -b $device ]] || { echo 'Workspace disk LUN 0 not available' >&2; exit 1; }
[[ $(lsblk -nr -o TYPE "$device") == disk ]] || { echo 'Partitioned disk: refusing automatic setup' >&2; exit 1; }
fstype=$(blkid -s TYPE -o value "$device" || true)
if [[ -z $fstype ]]; then
  [[ -z $(wipefs --noheadings "$device") ]] || { echo 'Unknown disk signatures; refusing format' >&2; exit 1; }
  mkfs.ext4 "$device"
elif [[ $fstype != ext4 ]]; then
  echo "Unsupported workspace filesystem: $fstype" >&2; exit 1
fi
uuid=$(blkid -s UUID -o value "$device")
mkdir -p /workspace
if ! grep -qE '^[^#]+[[:space:]]/workspace[[:space:]]' /etc/fstab; then
  printf 'UUID=%s /workspace ext4 defaults 0 2\n' "$uuid" >> /etc/fstab
fi
mountpoint -q /workspace || mount /workspace
[[ $(findmnt -n -o UUID --target /workspace) == "$uuid" ]] || { echo 'Unexpected workspace mount' >&2; exit 1; }
install -d -o "$user" -g "$user" /workspace/repos

# Use an early drop-in: OpenSSH uses the first value encountered.
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/00-dev-bootstrap.conf
/usr/sbin/sshd -t
systemctl reload ssh
ufw default deny incoming
ufw default allow outgoing
interface=tailscale0
[[ $vpn != wireguard ]] || interface=wg0
ufw allow in on "$interface" to any port 22 proto tcp
if [[ $vpn == wireguard ]]; then
  ufw allow "$(jq -r .wireguardPort "$config")/udp"
fi
ufw --force enable
bash "$ROOT/install-dev-tools.sh" --tools "$tools"

# Official standalone v2 installer; run as the unprivileged development user.
# No model-provider credentials are supplied by deployment.
if ! runuser -l "$user" -c 'test -x "$HOME/.opencode/bin/opencode"'; then
  installer=$(mktemp -d /tmp/opencode-install.XXXXXX)
  chmod 0755 "$installer"
  curl --retry 5 -fsSL https://opencode.ai/v2/install -o "$installer/install.sh"
  chmod 0644 "$installer/install.sh"
  runuser -l "$user" -c "bash '$installer/install.sh'"
  rm -r "$installer"
fi
runuser -l "$user" -c '"$HOME/.opencode/bin/opencode" --version'
source "$ROOT/vpn/$vpn.sh"
"setup_$vpn"
if jq -e .dockerGroup "$config" >/dev/null; then usermod -aG docker "$user"; fi
touch /var/lib/dev-bootstrap-ready
echo BOOTSTRAP_READY
