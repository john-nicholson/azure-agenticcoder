install_docker() {
  mountpoint -q /workspace || { echo '/workspace must be mounted before installing Docker' >&2; return 1; }
  install -d -m 0711 /workspace/docker
  install -d /etc/docker /etc/systemd/system/docker.service.d
  python3 - <<'PY'
import json, pathlib
p = pathlib.Path('/etc/docker/daemon.json')
config = json.loads(p.read_text()) if p.exists() else {}
old = config.get('data-root', '/var/lib/docker')
if old != '/workspace/docker' and pathlib.Path(old).exists() and any(pathlib.Path(old).iterdir()):
    raise SystemExit('Existing Docker data found. Migrate it explicitly before changing data-root.')
config['data-root'] = '/workspace/docker'
p.write_text(json.dumps(config, indent=2) + '\n')
PY
  printf '[Unit]\nRequiresMountsFor=/workspace\n[Service]\nExecStartPre=/usr/bin/mountpoint -q /workspace\n' \
    > /etc/systemd/system/docker.service.d/workspace.conf
  # Containerd 2.x may keep image content outside Docker's data-root. Keep its
  # state on the managed disk too, with the same mount dependency.
  install -d /etc/containerd /etc/systemd/system/containerd.service.d /workspace/containerd
  if [[ -d /var/lib/containerd ]] && [[ -n $(ls -A /var/lib/containerd) ]] && \
     ! grep -q 'root = "/workspace/containerd"' /etc/containerd/config.toml 2>/dev/null; then
    echo 'Existing containerd data found; migrate it explicitly before changing its root.' >&2
    return 1
  fi
  if [[ -e /etc/containerd/config.toml ]] && ! grep -q 'root = "/workspace/containerd"' /etc/containerd/config.toml; then
    echo 'Existing containerd configuration found; configure its persistent root explicitly.' >&2
    return 1
  fi
  printf 'version = 2\nroot = "/workspace/containerd"\n' > /etc/containerd/config.toml
  printf '[Unit]\nRequiresMountsFor=/workspace\n[Service]\nExecStartPre=/usr/bin/mountpoint -q /workspace\n' \
    > /etc/systemd/system/containerd.service.d/workspace.conf
  signed_repo docker https://download.docker.com/linux/ubuntu/gpg \
    'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable'
  systemctl daemon-reload
  apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now containerd docker
  docker info --format '{{.DockerRootDir}}' | grep -Fx /workspace/docker
  docker compose version
}
