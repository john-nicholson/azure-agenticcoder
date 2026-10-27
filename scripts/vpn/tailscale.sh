setup_tailscale() {
  curl --retry 5 -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl --retry 5 -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
  apt-get -o Acquire::Retries=5 update
  apt-get -o DPkg::Lock::Timeout=300 install -y tailscale
  systemctl enable --now tailscaled
}
