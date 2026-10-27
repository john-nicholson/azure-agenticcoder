setup_wireguard() {
  apt-get -o DPkg::Lock::Timeout=300 install -y wireguard
  install -d -m 0700 /etc/wireguard
  umask 077
  if [[ ! -f /etc/wireguard/server.private ]]; then wg genkey > /etc/wireguard/server.private; fi
  if [[ ! -f /etc/wireguard/wg0.conf ]]; then
    {
      printf '[Interface]\nAddress = %s\nListenPort = %s\nPrivateKey = %s\n' \
        "$(jq -r .wireguardAddress "$config")" "$(jq -r .wireguardPort "$config")" "$(</etc/wireguard/server.private)"
    } > /etc/wireguard/wg0.conf
  fi
  bash "$ROOT/wireguard-peer.sh" add initial \
    "$(jq -r .wireguardClientAddress "$config")" "$(jq -r .wireguardClientPublicKey "$config")"
  systemctl enable --now wg-quick@wg0
}
