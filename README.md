# Azure OpenCode development VM

Headless Ubuntu development host with selectable **Tailscale** or **WireGuard** access. Run OpenCode in tmux so work continues after you disconnect your laptop.

## Defaults

| Component | Default |
| --- | --- |
| Region | UK South (`uksouth`) |
| Compute | `Standard_D4s_v5`, 4 vCPU, 16 GiB RAM, non-burstable |
| OS | Ubuntu Server 24.04 LTS x86-64, Trusted Launch, 64 GiB Standard SSD OS disk |
| Workspace | Separate 128 GiB Standard SSD, `/workspace` |
| Editors | nano, terminal Emacs (`emacs-nox`), Vim |
| Base tooling | Git, curl, jq, ripgrep, unzip, build essentials, Python 3 (bootstrap dependency), tmux |
| Agent | OpenCode 2 standalone CLI, installed for the admin user |
| Docker | Enabled; Engine, Buildx, Compose; use `sudo docker` |
| Optional toolchains | `python`, `dotnet`, `terraform`, `node` |
| VPN | Explicit `--vpn tailscale` or `--vpn wireguard` |
| Shutdown | No schedule unless `--shutdown-time HHmm` is supplied (UTC) |

The VM has a static public IP for outbound internet. **TCP 22 is never exposed publicly.** WireGuard mode opens its UDP endpoint only; Tailscale mode denies all unsolicited inbound Azure traffic. Azure Run Command is used for provisioning verification and recovery.

## Prerequisites

On your Mac or Linux deployment workstation:

- Bash 3.2+, Python 3.9+, OpenSSH (`ssh`, `ssh-keygen`), Azure CLI with Bicep installed (`az bicep install`). No Python third-party packages are required for deployment.
- `az login`, a subscription with sufficient Dsv5 quota, and permission to manage Compute, Network, disks, and optionally DevTestLab schedules in the resource group. Contributor on a dedicated resource group plus subscription SKU-read access is a straightforward setup. Run Command permission grants root-equivalent access to the guest.
- An RSA (2048+ bit) or Ed25519 SSH key pair. Only supply the `.pub` file.
- Tailscale on your client, or a WireGuard client and permission to use outbound UDP on the work network.

Generate an SSH key if needed:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/opencode_vm
```

The examples assume you run commands from this repository root. Scripts can also be called with `bash scripts/deploy.sh ...`.

## Deploy with WireGuard (work)

Generate a **client** WireGuard key pair locally using `wg` from WireGuard tools, or use the WireGuard application's tunnel generator. Keep the private key on the client. Export only its public key for deployment.

```bash
umask 077
wg genkey > work-laptop.private
wg pubkey < work-laptop.private > work-laptop.pub

./scripts/deploy.sh \
  --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work \
  --name opencode-work \
  --ssh-public-key ~/.ssh/opencode_vm.pub \
  --vpn wireguard \
  --wireguard-client-public-key ./work-laptop.pub \
  --tools python,node,terraform
```

The script deploys resources, waits for bootstrap, displays an SSH host-key fingerprint, and prints a client configuration template. Insert your **local private key** into that template and import it into WireGuard. Store the completed configuration privately; it contains a credential.

Default topology:

```text
Laptop 10.77.0.2/32  -- encrypted UDP 51820 -->  VM 10.77.0.1/24
```

- Client `AllowedIPs = 10.77.0.1/32`: only this VM uses the tunnel. No VPN internet routing, DNS override, or VPN-to-VNet forwarding rules are configured. Docker may independently enable kernel forwarding for its container bridges.
- Server peer `AllowedIPs` contains the client's `/32`, preventing that peer from claiming other VPN addresses.
- Server private key is generated on the VM and remains in root-only `/etc/wireguard`. It never appears in deployment parameters or command output.
- Activate the tunnel, then connect:

```bash
ssh -i ~/.ssh/opencode_vm opencode@10.77.0.1
```

Compare the first SSH fingerprint with deployment output. If the work network blocks UDP, changing a WireGuard port does not provide TCP fallback. The port is configurable to match permitted UDP egress.

## Deploy with Tailscale (personal)

```bash
./scripts/deploy.sh \
  --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-personal \
  --name opencode-personal \
  --ssh-public-key ~/.ssh/opencode_vm.pub \
  --vpn tailscale \
  --tools node
```

After bootstrap, open the printed Tailscale URL, authenticate, approve the device if required, and press Enter. Connect using the printed Tailscale IP and your SSH private key. This uses **OpenSSH**, not Tailscale SSH.

The guest login process times out after ten minutes. Enrollment can be resumed without redeploying:

```bash
python3 scripts/operations.py enroll --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-personal --name opencode-personal
```

Use `--skip-enrollment` for deployment without an interactive prompt. Noninteractive deployment prints the enrollment URL and returns; inspect `status.sh` after authenticating. Tailnet policies and device approval must allow TCP 22 from your identity to this VM. Use a least-privilege policy; Tailscale's netfilter rules may permit more than host UFW rules, so do not treat UFW as a substitute for tailnet policy. Review server key expiry and reauthenticate before expiry, or manage it according to your tailnet's server policy.

## Select development tooling

Docker is enabled during deployment unless `--no-docker` is supplied. Other modules are opt-in.

| Module | Installation |
| --- | --- |
| `docker` | Official Docker Ubuntu stable repository: Engine, Buildx, Compose |
| `python` | Ubuntu Python 3.12, pip, venv, pipx, development headers |
| `dotnet` | .NET 10 LTS SDK from Ubuntu 24.04 repositories |
| `terraform` | HashiCorp signed repository, Terraform 1.x pin |
| `node` | NodeSource signed Node.js 24 LTS repository, npm |

Base Python is required for bootstrap even when the Python **development** module is not selected. Package patches are not frozen: installers take available updates in their release line. Docker uses its stable repository; OpenCode uses the latest v2 installer when first installed. The OpenCode installer is downloaded over HTTPS and run as the unprivileged admin user; it is not checksum-pinned. Review/pin vendor artifacts before using in environments requiring reproducible builds.

Examples:

```bash
# Add these flags to a deployment
--tools python,dotnet,terraform,node
--no-docker
--docker-group  # Optional root-equivalent access without sudo; requires Docker
```

On the deployed VM, install more tools later:

```bash
sudo /opt/dev-bootstrap/install-dev-tools.sh --tools python,dotnet
```

The guest installer always ensures base packages and runs **only the explicitly requested optional modules**. It does not add Docker implicitly. Repeated runs are supported, including duplicate tool names. Installing updates can restart services: rerun during a suitable maintenance window.

To extend the tool list, add `scripts/tools/NAME.sh` defining `install_NAME`, register the name in `scripts/install-dev-tools.sh` and `scripts/deploy.py`, and update documentation/tests. Tool modules execute with root privileges.

### Docker storage and ports

- Docker data root: `/workspace/docker`; containerd content root: `/workspace/containerd`.
- Both services require `/workspace` to be mounted. Installation refuses to silently move existing data from an OS-disk root.
- Docker commands use `sudo` unless `--docker-group` was explicitly selected.
- Bind published ports to localhost and use SSH forwarding:

```bash
# On the VM
sudo docker run --rm -p 127.0.0.1:8080:80 nginx

# On your laptop, while connected to the selected VPN
ssh -i ~/.ssh/opencode_vm -L 8080:127.0.0.1:8080 opencode@10.77.0.1
```

Then browse `http://localhost:8080`. Docker's firewall rules can bypass UFW; the Azure NSG remains the public ingress boundary. Explicit host-wide Docker publishing may expose services to VPN peers; do not assume UFW restricts published ports.

## OpenCode workflow

```bash
cd /workspace/repos
git clone YOUR_REPOSITORY_URL
cd YOUR_REPOSITORY
tmux new -As opencode
opencode
```

Run `/connect` inside OpenCode to configure your model provider. Set up Git credentials separately on the VM. Credentials are not copied from your laptop and are never provisioned through Bicep. Detach tmux with `Ctrl-b d`; reconnect with `tmux attach -t opencode`. Laptop disconnection does not stop the process; VM shutdown/reboot does.

OpenCode is installed at `~/.opencode/bin/opencode`. Open a new interactive shell if PATH is not refreshed. No unattended root-level agent or publicly accessible OpenCode web service is started.

## Preview and configuration

Run `./scripts/deploy.sh --help` for all options. Notable overrides:

```text
--location uksouth
--vm-size Standard_D4s_v5
--admin-username opencode
--workspace-size 256
--workspace-sku Premium_LRS
--existing-workspace-disk-id /subscriptions/.../providers/Microsoft.Compute/disks/...
--wireguard-address 10.79.0.1/24
--wireguard-client-address 10.79.0.2/32
--wireguard-port 51820
--wireguard-source-cidr 203.0.113.10/32  (repeatable; replace example IP)
--shutdown-time 2200
```

Choose a VPN subnet that does not overlap work/home routes. The Azure VNet is `10.88.0.0/16`; VPN overlap with that range is rejected. Endpoint source restrictions apply to the client's **public egress IP**, not its VPN address.

For a local preview, add `--render-only parameters.json` to your full deployment command. This validates inputs and bundles the local bootstrap scripts without calling Azure. The generated file includes public keys but no private keys; do not commit generated deployment files.

For Azure what-if, first create a dedicated resource group, then add `--what-if` to your command. What-if does not create a missing resource group. It queries SKU availability and existing disk compatibility but does not create billable resources. Azure pricing, regional quota, and live capacity still need checking before deployment; no reservation or savings commitment is purchased.

Bootstrap is passed through cloud-init as a compressed bundle, limited to Azure's 64 KiB custom-data limit. It does not fetch scripts from this repository over the internet.

**Existing VM handling:** deployment deliberately refuses to overwrite an existing VM. Azure custom data does not rerun on a normal deployment update. Use the guest installer to add tools; use a new VM for a VPN mode change. After a deployment timeout, inspect the existing VM rather than deploying a duplicate. The Bicep template can be maintained directly for infrastructure updates by an operator who understands these lifecycle constraints.

## Status, power, and recovery

```bash
./scripts/status.sh --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work --name opencode-work

python3 scripts/operations.py deallocate --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work --name opencode-work

python3 scripts/operations.py start --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work --name opencode-work
```

Deallocation stops compute billing; disks and public IP continue to incur charges. `--shutdown-time` schedules daily shutdown in UTC, interrupts work, and does not schedule restart. Disable an existing schedule with `az vm auto-shutdown --subscription ... -g ... -n ... --off`.

Logs on the guest:

- `/var/log/cloud-init-output.log`
- `/var/log/dev-bootstrap.log`
- `/var/log/dev-tools.log`
- `journalctl -u wg-quick@wg0` or `journalctl -u tailscaled`

If VPN access fails, Azure Portal **VM → Run command → RunShellScript** works without opening SSH. For bootstrap diagnosis:

```bash
cloud-init status --long
tail -n 80 /var/log/dev-bootstrap.log
```

After fixing a failure, rerun `bash /opt/dev-bootstrap/bootstrap.sh` as root through Run Command. Keep commands bounded; Action Run Command has a 90-minute limit and returns only the final 4 KiB of output. Do not print `/etc/wireguard/wg0.conf` or any credential files. Server public key and safe status: `wg show wg0 public-key`, `wg show`, `tailscale status`.

## WireGuard peers and users

Generate each device's keys locally. From an existing administrative SSH session:

```bash
sudo bash /opt/dev-bootstrap/wireguard-peer.sh add work-phone 10.77.0.3/32 PUBLIC_KEY
sudo bash /opt/dev-bootstrap/wireguard-peer.sh list
sudo bash /opt/dev-bootstrap/wireguard-peer.sh revoke work-phone
```

Give the new peer the same endpoint/server public key, its own address/private key, and server-only `AllowedIPs`. Peer changes persist and synchronize without restarting unrelated peers. The initial deployment peer is named `initial`. Re-running full bootstrap will restore that initial peer from deployment settings; update those settings if intentionally revoking it permanently.

WireGuard authenticates device keys, not users. To grant separate shell access, create a separate Linux account and add that user's SSH public key to its `~/.ssh/authorized_keys`. Do not share the admin account/private key. Revoking a VPN peer does not revoke that person's other VPN devices or SSH credentials.

## Persistence, replacement, teardown

`/workspace/repos`, Docker data, and containerd data persist on the managed data disk. Home directories, provider/Git credentials, OpenCode session/configuration state, SSH host keys, and VPN identity remain on the OS disk. Replacing the VM requires configuring those again. Persistent storage is not a backup; configure snapshots/backups separately for important work.

The bootstrap formats **only a blank whole disk at Azure SCSI LUN 0**. It reuses ext4 filesystems and rejects partitioned or unrecognized disks. The default SKU/image uses SCSI disks; changing to an NVMe-only VM family requires adapting disk discovery. A disk missing at boot can stop dependent services or boot rather than silently using the OS disk.

Teardown requires typing a confirmation and **retains data disks by default**:

```bash
./scripts/destroy.sh --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work --name opencode-work

# Permanently delete attached data disks as well:
./scripts/destroy.sh --subscription YOUR_SUBSCRIPTION_ID \
  --resource-group rg-opencode-work --name opencode-work --delete-workspace
```

The helper deletes the VM/OS disk and project networking, leaving the resource group. It never deletes the entire resource group to preserve retained disks. **Deleting the resource group yourself also deletes retained disks.** Record their resource IDs before teardown. Remove obsolete Tailscale nodes from your tailnet separately.

To replace a VM using its retained disk, supply `--existing-workspace-disk-id` on the next deployment. It must be detached, non-zonal, and in the same region. If a disk with the default workspace name already exists, the client requires this explicit flag. Existing Linux file ownership depends on UID/GID: keep the same user identity or reconcile ownership explicitly. Disk enlargement and filesystem growth are manual operations; never shrink a disk containing data.

## Local verification

```bash
python3 -m unittest discover -s tests -v
az bicep build --file infra/main.bicep --stdout
```

Tests cover input rejection, Docker selection semantics, self-contained cloud-init bundling, public-key handling, and no-Azure render-only behavior. A live Azure test is still required to verify cloud-init, actual package installation, VPN connectivity/reboot, firewall behavior, and disk retention. No live deployment is performed by the local test suite.

Architecture and acceptance criteria: [`docs/implementation-plan.md`](docs/implementation-plan.md).
