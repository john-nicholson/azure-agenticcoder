#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
action=${1:-help}
case "$action" in
  add) [[ $# == 4 ]] || { echo 'Usage: wireguard-peer.sh add NAME IPV4/32 PUBLIC_KEY' >&2; exit 2; } ;;
  revoke) [[ $# == 2 ]] || exit 2 ;;
  list) [[ $# == 1 ]] || exit 2 ;;
  *) echo 'Usage: wireguard-peer.sh {add NAME IPV4/32 PUBLIC_KEY|revoke NAME|list}'; exit 2 ;;
esac
exec 8>/var/lock/wireguard-peers.lock
flock -w 30 8
python3 - "$@" <<'PY'
import base64, ipaddress, os, pathlib, re, subprocess, sys, tempfile
action = sys.argv[1]
p = pathlib.Path('/etc/wireguard/wg0.conf')
text = p.read_text()
if action == 'list':
    for name, address in re.findall(r'# peer: ([\w-]+)\n\[Peer\]\nPublicKey = [^\n]+\nAllowedIPs = ([^\n]+)', text):
        print(name, address)
    sys.exit()
name = sys.argv[2]
if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name):
    raise SystemExit('Invalid peer name')
pattern = r'\n# peer: ' + re.escape(name) + r'\n\[Peer\]\nPublicKey = [^\n]+\nAllowedIPs = [^\n]+\n'
match = re.search(pattern, text)
if action == 'revoke':
    if not match: raise SystemExit('Unknown peer')
    updated = text[:match.start()] + text[match.end():]
else:
    address, key = sys.argv[3:5]
    addr = ipaddress.ip_interface(address)
    server = ipaddress.ip_interface(re.search(r'^Address = (.+)$', text, re.M)[1])
    if addr.version != 4 or addr.network.prefixlen != 32 or addr.ip not in server.network or addr.ip in (server.ip, server.network.network_address, server.network.broadcast_address):
        raise SystemExit('Client must be a distinct usable /32 within the server subnet')
    try: valid_key = len(base64.b64decode(key, validate=True)) == 32
    except ValueError: valid_key = False
    if not valid_key: raise SystemExit('Invalid public key')
    block = f'\n# peer: {name}\n[Peer]\nPublicKey = {key}\nAllowedIPs = {addr}\n'
    if match:
        if match[0] == block: sys.exit()
        raise SystemExit('Peer exists with different settings; revoke first')
    if f'PublicKey = {key}\n' in text or f'AllowedIPs = {addr}\n' in text:
        raise SystemExit('Duplicate peer key or address')
    updated = text + block
fd, tmp = tempfile.mkstemp(dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f: f.write(updated)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
if subprocess.run(['ip', 'link', 'show', 'wg0'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    stripped = subprocess.check_output(['wg-quick', 'strip', 'wg0'])
    fd, tmp = tempfile.mkstemp(dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(stripped)
        try:
            subprocess.run(['wg', 'syncconf', 'wg0', tmp], check=True)
        except subprocess.CalledProcessError:
            # Keep the persistent file consistent with the old live state.
            p.write_text(text)
            os.chmod(p, 0o600)
            raise
    finally: os.unlink(tmp)
print(action, name)
PY
