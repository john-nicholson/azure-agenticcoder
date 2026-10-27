#!/usr/bin/env python3
"""Management over the Azure guest agent; never opens public SSH."""
import argparse
import re
import subprocess
import sys
from deploy import az, enroll, run_guest

STATUS = '''#!/bin/bash
echo '--- bootstrap ---'
cloud-init status || true
if test -f /var/lib/dev-bootstrap-ready; then echo BOOTSTRAP_READY; else echo NOT_READY; fi
echo '--- storage ---'
findmnt /workspace || true
df -h /workspace
echo '--- SSH ---'
ss -lnt '( sport = :22 )'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
echo '--- VPN ---'
if command -v wg >/dev/null; then wg show; fi
if command -v tailscale >/dev/null; then tailscale status; fi
echo '--- tools ---'
for tool in node python3 dotnet terraform; do
  if command -v "$tool" >/dev/null; then "$tool" --version; fi
done
if command -v docker >/dev/null; then docker info --format '{{.DockerRootDir}}'; docker compose version; fi
user=$(jq -r .adminUsername /opt/dev-bootstrap/settings.json)
runuser -l "$user" -c '"$HOME/.opencode/bin/opencode" --version' || true
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['status', 'enroll', 'start', 'deallocate', 'destroy'])
    p.add_argument('--subscription', required=True)
    p.add_argument('--resource-group', required=True)
    p.add_argument('--name', default='opencode')
    p.add_argument('--delete-workspace', action='store_true', help='Destroy only: permanently delete attached data disks')
    a = p.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,38}[a-z0-9]|[a-z]', a.name):
        p.error('Invalid VM name')
    if a.delete_workspace and a.action != 'destroy':
        p.error('--delete-workspace only applies to destroy')
    common = ['--subscription', a.subscription, '-g', a.resource_group, '-n', a.name]
    vm = az('vm', 'show', *common)
    if vm.get('tags', {}).get('project') != 'opencode-dev-vm':
        raise ValueError('VM is not tagged as managed by this project')
    if a.action == 'status':
        view = az('vm', 'get-instance-view', *common)
        states = view['instanceView']['statuses']
        for state in states:
            print(state.get('displayStatus', state['code']))
        if any(s['code'] == 'PowerState/running' for s in states):
            print(run_guest(a.subscription, a.resource_group, a.name, STATUS))
    elif a.action == 'enroll':
        if vm['tags'].get('vpn') != 'tailscale':
            raise ValueError('This VM uses WireGuard; there is no browser enrollment')
        a.admin_username = vm['osProfile']['adminUsername']
        enroll(a)
    elif a.action in {'start', 'deallocate'}:
        az('vm', a.action, *common)
        print(a.action, 'completed')
    else:
        disks = [d['managedDisk']['id'] for d in vm['storageProfile']['dataDisks']]
        print('Deleting VM, OS disk, NIC, public IP, NSG, VNet, and shutdown schedule.')
        print('Workspace disks:', 'PERMANENTLY DELETE' if a.delete_workspace else 'RETAIN', *disks, sep='\n')
        expected = f'DELETE {a.name}'
        if input(f'Type {expected!r} to confirm: ') != expected:
            raise ValueError('Cancelled')
        # Never delete the resource group: retained disks may be in this group.
        az('vm', 'delete', *common, '--yes')
        for kind, suffix in [('Microsoft.Network/networkInterfaces', '-nic'),
                             ('Microsoft.Network/publicIPAddresses', '-pip'),
                             ('Microsoft.Network/networkSecurityGroups', '-nsg'),
                             ('Microsoft.Network/virtualNetworks', '-vnet')]:
            matches = az('resource', 'list', '--subscription', a.subscription,
                         '-g', a.resource_group, '--resource-type', kind)
            for resource in matches:
                if resource['name'] == a.name + suffix and resource.get('tags', {}).get('project') == 'opencode-dev-vm':
                    az('resource', 'delete', '--subscription', a.subscription, '--ids', resource['id'])
        schedules = az('resource', 'list', '--subscription', a.subscription, '-g', a.resource_group,
                       '--resource-type', 'Microsoft.DevTestLab/schedules')
        for schedule in schedules:
            if schedule['name'] == f'shutdown-computevm-{a.name}':
                az('resource', 'delete', '--subscription', a.subscription, '--ids', schedule['id'])
        if a.delete_workspace:
            for disk_id in disks:
                az('disk', 'delete', '--subscription', a.subscription, '--ids', disk_id, '--yes')
        print('Teardown complete. Resource group retained.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, EOFError, subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
