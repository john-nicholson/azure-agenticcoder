#!/usr/bin/env python3
"""Azure deployment client. Requires Python 3.9+, Azure CLI, and OpenSSH tools."""
import argparse
import base64
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {'docker', 'python', 'dotnet', 'terraform', 'node'}


def az(*args):
    result = subprocess.run(['az', *map(str, args), '--only-show-errors', '-o', 'json'],
                            text=True, stdout=subprocess.PIPE, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def run_guest(subscription, group, name, script):
    response = az('vm', 'run-command', 'invoke', '--subscription', subscription,
                  '-g', group, '-n', name, '--command-id', 'RunShellScript', '--scripts', script)
    output = '\n'.join(row.get('message', '') for row in response.get('value', []))
    return output


def parser():
    p = argparse.ArgumentParser(description='Deploy a private-SSH OpenCode development VM.')
    p.add_argument('--subscription', required=True)
    p.add_argument('--resource-group', required=True)
    p.add_argument('--name', default='opencode')
    p.add_argument('--admin-username', default='opencode')
    p.add_argument('--ssh-public-key', type=Path, required=True)
    p.add_argument('--vpn', choices=['tailscale', 'wireguard'], required=True)
    p.add_argument('--location', default='uksouth')
    p.add_argument('--vm-size', default='Standard_D4s_v5')
    p.add_argument('--workspace-size', type=int, default=128, help='GiB, for a new disk')
    p.add_argument('--workspace-sku', choices=['StandardSSD_LRS', 'Premium_LRS'], default='StandardSSD_LRS')
    p.add_argument('--existing-workspace-disk-id', default='')
    p.add_argument('--tools', default='', help='Comma-separated python,dotnet,terraform,node,docker')
    p.add_argument('--no-docker', action='store_true')
    p.add_argument('--docker-group', action='store_true', help='Grant root-equivalent Docker access to admin')
    p.add_argument('--wireguard-client-public-key', type=Path)
    p.add_argument('--wireguard-address', default='10.77.0.1/24')
    p.add_argument('--wireguard-client-address', default='10.77.0.2/32')
    p.add_argument('--wireguard-port', type=int, default=51820)
    p.add_argument('--wireguard-source-cidr', action='append', help='Repeat to allow multiple source IPv4 CIDRs')
    p.add_argument('--shutdown-time', default='', help='HHmm UTC; empty disables schedule')
    p.add_argument('--what-if', action='store_true', help='Preview resources; requires existing resource group')
    p.add_argument('--render-only', type=Path, metavar='PARAMETERS_JSON', help='Write parameters locally without Azure calls')
    p.add_argument('--skip-enrollment', action='store_true', help='Deploy and verify bootstrap, enroll later')
    return p


def public_key(path, wireguard=False):
    value = path.expanduser().read_text().strip()
    if wireguard:
        try:
            if len(base64.b64decode(value, validate=True)) != 32:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('Invalid WireGuard public key') from exc
    else:
        if not re.match(r'^(ssh-ed25519|ssh-rsa) [A-Za-z0-9+/=]+(?: .*)?$', value):
            raise ValueError('SSH key must be a single-line RSA or Ed25519 public key')
        subprocess.run(['ssh-keygen', '-lf', str(path.expanduser())], check=True, stdout=subprocess.DEVNULL)
    return value


def validate(a):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,38}[a-z0-9]|[a-z]', a.name):
        raise ValueError('VM name must be 1-40 lowercase letters, digits, or hyphens, without trailing hyphen')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}', a.admin_username) or a.admin_username in {'root', 'admin', 'administrator', 'ubuntu'}:
        raise ValueError('Choose a non-reserved Linux admin username')
    if not re.fullmatch(r'[\w.()-]{1,90}', a.resource_group) or a.resource_group.endswith('.'):
        raise ValueError('Invalid resource group name')
    if not 4 <= a.workspace_size <= 32767:
        raise ValueError('Workspace size must be 4-32767 GiB')
    if a.shutdown_time and not re.fullmatch(r'(?:[01][0-9]|2[0-3])[0-5][0-9]', a.shutdown_time):
        raise ValueError('Shutdown time must be HHmm UTC')
    tools = list(dict.fromkeys(a.tools.split(','))) if a.tools else []
    if set(tools) - TOOLS:
        raise ValueError('Unknown tools: ' + ','.join(sorted(set(tools) - TOOLS)))
    if a.no_docker and 'docker' in tools:
        raise ValueError('--no-docker conflicts with --tools docker')
    if not a.no_docker and 'docker' not in tools:
        tools.insert(0, 'docker')
    if a.docker_group and 'docker' not in tools:
        raise ValueError('--docker-group requires Docker')
    server = ipaddress.ip_interface(a.wireguard_address)
    client = ipaddress.ip_interface(a.wireguard_client_address)
    if (server.version != 4 or client.version != 4 or server.network.prefixlen > 30
            or client.network.prefixlen != 32 or client.ip not in server.network
            or server.ip in (server.network.network_address, server.network.broadcast_address)
            or client.ip in (server.ip, server.network.network_address, server.network.broadcast_address)):
        raise ValueError('Use a valid server IPv4 subnet and distinct usable client /32 within it')
    if server.network.overlaps(ipaddress.ip_network('10.88.0.0/16')):
        raise ValueError('VPN subnet overlaps the Azure VNet (10.88.0.0/16)')
    if not 1 <= a.wireguard_port <= 65535:
        raise ValueError('Invalid UDP port')
    prefixes = a.wireguard_source_cidr or ['0.0.0.0/0']
    for cidr in prefixes:
        if ipaddress.ip_network(cidr).version != 4:
            raise ValueError('WireGuard endpoint source CIDRs must be IPv4')
    if a.vpn == 'wireguard' and not a.wireguard_client_public_key:
        raise ValueError('WireGuard requires --wireguard-client-public-key')
    if a.vpn == 'tailscale' and (a.wireguard_client_public_key or a.wireguard_source_cidr):
        raise ValueError('WireGuard enrollment options cannot be used with Tailscale')
    if a.existing_workspace_disk_id and not re.fullmatch(r'/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Compute/disks/[^/]+', a.existing_workspace_disk_id, re.I):
        raise ValueError('Expected a full Azure managed disk resource ID')
    return tools, prefixes


def bundle_cloud_init(settings):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for path in sorted((ROOT / 'scripts').rglob('*.sh')):
            if path.name in {'deploy.sh', 'status.sh', 'destroy.sh'}:
                continue
            info = archive.gettarinfo(str(path), arcname=str(path.relative_to(ROOT / 'scripts')))
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            info.mtime = 0
            with path.open('rb') as f:
                archive.addfile(info, f)
        data = json.dumps(settings).encode()
        info = tarfile.TarInfo('settings.json')
        info.size, info.mode, info.mtime = len(data), 0o644, 0
        archive.addfile(info, io.BytesIO(data))
    payload = base64.b64encode(stream.getvalue()).decode()
    config = '''#cloud-config
package_update: true
packages: [jq, curl, ca-certificates]
write_files:
  - path: /opt/dev-bootstrap.tar.gz
    permissions: '0600'
    encoding: b64
    content: PAYLOAD
runcmd:
  - [mkdir, -p, /opt/dev-bootstrap]
  - [tar, -xzf, /opt/dev-bootstrap.tar.gz, -C, /opt/dev-bootstrap]
  - [bash, /opt/dev-bootstrap/bootstrap.sh]
'''.replace('PAYLOAD', payload)
    if len(config.encode()) > 65535:
        raise ValueError('Bootstrap bundle exceeds Azure custom-data size limit')
    return base64.b64encode(config.encode()).decode()


def parameters(a):
    tools, prefixes = validate(a)
    ssh = public_key(a.ssh_public_key)
    peer = public_key(a.wireguard_client_public_key, True) if a.vpn == 'wireguard' else ''
    settings = dict(adminUsername=a.admin_username, vpn=a.vpn, tools=','.join(tools),
                    dockerGroup=a.docker_group, wireguardAddress=a.wireguard_address,
                    wireguardClientAddress=a.wireguard_client_address,
                    wireguardPort=a.wireguard_port, wireguardClientPublicKey=peer)
    values = dict(location=a.location, vmName=a.name, vmSize=a.vm_size,
                  adminUsername=a.admin_username, sshPublicKey=ssh,
                  customData=bundle_cloud_init(settings), vpn=a.vpn,
                  wireguardPort=a.wireguard_port, wireguardSourcePrefixes=prefixes,
                  workspaceSizeGiB=a.workspace_size, workspaceSku=a.workspace_sku,
                  existingWorkspaceDiskId=a.existing_workspace_disk_id, shutdownTime=a.shutdown_time)
    return {'$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
            'contentVersion': '1.0.0.0', 'parameters': {k: {'value': v} for k, v in values.items()}}


def main():
    a = parser().parse_args()
    params = parameters(a)
    if a.render_only:
        # Contains public keys and bootstrap data only, never private keys.
        with a.render_only.open('w') as f:
            json.dump(params, f, indent=2)
        print(f'Rendered {a.render_only}; no Azure calls performed')
        return
    if not shutil.which('az'):
        raise ValueError('Install Azure CLI first')
    az('account', 'show', '--subscription', a.subscription)
    common = ['--subscription', a.subscription, '--resource-group', a.resource_group]
    exists = az('group', 'exists', '--subscription', a.subscription, '-n', a.resource_group)
    if a.what_if and not exists:
        raise ValueError('--what-if requires an existing resource group; no resources were created')
    # Existing VMs must be managed in place: customData does not rerun on update,
    # and changing only the NSG could otherwise strand the machine.
    if exists and az('vm', 'list', *common, '--query', f"[?name=='{a.name}'].name"):
        raise ValueError('VM already exists. Use the guest installer for tools; VPN changes require a new VM.')
    if exists and not a.existing_workspace_disk_id:
        retained = az('disk', 'list', *common, '--query', f"[?name=='{a.name}-workspace'].id")
        if retained:
            raise ValueError('A workspace disk already exists. Pass --existing-workspace-disk-id explicitly to retain it: ' + retained[0])
    skus = az('vm', 'list-skus', '--subscription', a.subscription, '--location', a.location,
              '--resource-type', 'virtualMachines', '--size', a.vm_size, '--all')
    if not any(s['name'] == a.vm_size and not s.get('restrictions') for s in skus):
        raise ValueError(f'{a.vm_size} is unavailable or restricted in {a.location} for this subscription')
    if a.existing_workspace_disk_id:
        disk = az('disk', 'show', '--subscription', a.subscription, '--ids', a.existing_workspace_disk_id)
        if disk['location'].lower() != a.location.lower() or disk.get('managedBy') or disk.get('zones'):
            raise ValueError('Existing workspace disk must be detached, non-zonal, and in the VM region')
    if not exists:
        az('group', 'create', '--subscription', a.subscription, '-n', a.resource_group, '-l', a.location)
    with tempfile.TemporaryDirectory(prefix='opencode-deploy-') as tmp:
        path = Path(tmp) / 'parameters.json'
        path.write_text(json.dumps(params))
        cmd = ['deployment', 'group', 'what-if' if a.what_if else 'create', *common,
               '--name', a.name, '--template-file', ROOT / 'infra/main.bicep', '--parameters', '@' + str(path)]
        if a.what_if:
            subprocess.run(['az', *map(str, cmd)], check=True)
            return
        deployment = az(*cmd)
    outputs = deployment['properties']['outputs']
    public_ip = outputs['publicIpAddress']['value']
    print('Infrastructure deployed. Waiting up to 30 minutes for guest bootstrap...', flush=True)
    script = """#!/bin/bash
for ((i=0; i<180; i++)); do
  if test -f /var/lib/dev-bootstrap-ready; then echo BOOTSTRAP_READY; exit 0; fi
  if test -e /var/lib/cloud/instance/boot-finished; then echo BOOTSTRAP_FAILED; exit 1; fi
  sleep 10
done
echo BOOTSTRAP_TIMEOUT
exit 1
"""
    output = run_guest(a.subscription, a.resource_group, a.name, script)
    if '\nBOOTSTRAP_READY' not in output:
        raise ValueError('Guest bootstrap not ready. Run scripts/status.sh for diagnostics.\n' + output)
    # Removing a disabled schedule explicitly is necessary in incremental ARM mode.
    if not a.shutdown_time:
        schedules = az('resource', 'list', *common, '--resource-type', 'Microsoft.DevTestLab/schedules')
        for schedule in schedules:
            if schedule['name'] == f'shutdown-computevm-{a.name}':
                az('resource', 'delete', '--subscription', a.subscription, '--ids', schedule['id'])
    fingerprints = run_guest(a.subscription, a.resource_group, a.name,
                             'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub')
    print('SSH server fingerprint (compare on first connection):\n' + fingerprints)
    if a.vpn == 'wireguard':
        key_output = run_guest(a.subscription, a.resource_group, a.name,
                              "printf 'SERVER_PUBLIC_KEY='; wg show wg0 public-key")
        key = re.search(r'SERVER_PUBLIC_KEY=([A-Za-z0-9+/=]+)', key_output)
        if not key:
            raise ValueError('Cannot read WireGuard server public key: ' + key_output)
        server_ip = str(ipaddress.ip_interface(a.wireguard_address).ip)
        print(f'''\nClient configuration template (insert your LOCAL private key):
[Interface]
PrivateKey = <LOCAL_CLIENT_PRIVATE_KEY>
Address = {a.wireguard_client_address}

[Peer]
PublicKey = {key[1]}
Endpoint = {public_ip}:{a.wireguard_port}
AllowedIPs = {server_ip}/32
PersistentKeepalive = 25

After activating WireGuard: ssh {a.admin_username}@{server_ip}
''')
    elif not a.skip_enrollment:
        enroll(a)
    print('Workspace disk:', outputs['workspaceDiskId']['value'])


def enroll(a):
    # systemd owns the login process beyond the Azure Run Command lifetime.
    script = '''#!/bin/bash
set -e
if tailscale status --json | jq -e '.BackendState == "Running"' >/dev/null; then
  echo 'Already enrolled'; tailscale ip -4; exit 0
fi
if ! systemctl is-active --quiet dev-tailscale-login; then
  systemctl reset-failed dev-tailscale-login 2>/dev/null || true
  systemd-run --unit=dev-tailscale-login --collect /bin/bash -c 'umask 077; exec tailscale up --ssh=false --timeout=10m > /run/dev-tailscale-login.log 2>&1'
fi
sleep 5
cat /run/dev-tailscale-login.log
'''
    print(run_guest(a.subscription, a.resource_group, a.name, script))
    if not sys.stdin.isatty():
        print('Complete enrollment using the URL above, then use status.sh for the VPN address.')
        return
    input('Open the login URL, authenticate/approve the VM, then press Enter: ')
    print(run_guest(a.subscription, a.resource_group, a.name, 'tailscale status; tailscale ip -4'))
    print(f'SSH: ssh {a.admin_username}@<tailscale-ip>')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
