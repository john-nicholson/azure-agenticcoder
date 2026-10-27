# Azure OpenCode development VM

## Initial state at planning time

The workspace initially contained no infrastructure or deployment scripts. Earlier conversation established a design, not an implemented Tailscale deployment. This document records the agreed architecture and Build agent handoff. The implementation is now present; consult `README.md` for the actual command interface and `docs/verification.md` for verification performed.

## Proposed state

A headless Ubuntu VM on Azure, accessed with OpenSSH over either Tailscale or self-managed WireGuard. Development tooling is selectable during deployment and installable afterward.

### Decisions

| Setting | Default |
| --- | --- |
| Region | `uksouth` |
| Template and client | Bicep and Azure CLI, Bash deployment script |
| VM SKU | `Standard_D4s_v5` — 4 vCPU, 16 GiB RAM |
| OS | Ubuntu Server 24.04 LTS, x86-64, Gen2 |
| Workspace disk | 128 GiB `StandardSSD_LRS`, mounted at `/workspace` |
| Authentication | Existing SSH public key; passwords and root SSH login disabled |
| VPN | Explicit selection: `tailscale` or `wireguard`; install only selected mode |
| Docker | Enabled by default; `--no-docker` disables installation |
| Language toolchains | Opt-in: `python`, `dotnet`, `terraform`, `node` |
| OpenCode | OpenCode 2 standalone installation, no desktop or public web server |
| Shutdown | Disabled by default; optional daily UTC shutdown time |

Retain the VM size, disk size, tool selections, VPN port, and VPN addresses as parameters. Do not silently substitute a different SKU or region if unavailable.

```mermaid
flowchart LR
    Client[Authorized device] --> VPN{Selected VPN}
    VPN -->|Tailscale encrypted overlay| TS[tailscale0]
    VPN -->|Public IP UDP 51820| WG[wg0]
    TS --> SSH[OpenSSH with public-key authentication]
    WG --> SSH
    SSH --> VM[Ubuntu VM: OpenCode and development tooling]
    VM --> Disk[Persistent workspace disk]
    Disk --> Repos[/workspace/repos]
    Disk --> Docker[/workspace/docker]
```

## Security and connectivity

### Shared

- Use a Standard static public IP for explicit outbound connectivity. Public SSH must remain blocked.
- NSG: explicit inbound deny overriding Azure's default VNet inbound allowance, with only the selected WireGuard UDP exception at higher priority. Preserve required outbound Azure guest-agent connectivity.
- Host firewall: allow SSH through the selected VPN interface only. Validate interaction with Tailscale netfilter rules and Docker's firewall rules rather than assuming UFW controls all Docker traffic.
- No cloud credentials, provider API keys, SSH private keys, or VPN private keys in cloud-init, Bicep parameters, deployment history, or command output.
- Use Azure Run Command for bootstrap verification and recovery, not public SSH. Its permission is effectively root access; document RBAC requirements.
- Do not assign the VM Azure roles by default. Authenticate Git and model providers interactively after SSH login.
- Recommend tmux for sessions surviving client disconnection. This does not make sessions survive reboot or shutdown.

### Tailscale mode

- No inbound NSG allow rules.
- Install and enable tailscaled; manually enroll with a browser login URL obtained through bounded Azure Run Command operations.
- Do not block cloud-init on enrollment. Run login separately and handle pending authentication, timeout, and reruns safely.
- Keep Tailscale SSH disabled; use OpenSSH and the supplied SSH key.
- Document least-privilege tailnet access policy, approval requirements, and key-expiry recovery. Do not alter a tailnet policy automatically.

### WireGuard mode

- Default endpoint: static public IP, UDP `51820`; configurable UDP port and optional permitted client source CIDRs.
- Default server tunnel address: `10.77.0.1/24`; first client: `10.77.0.2/32`. Make addresses configurable and validate conflicts and duplicates.
- Split tunnel to this VM only. Client `AllowedIPs` defaults to `10.77.0.1/32`; server peer `AllowedIPs` contains only that client's `/32`.
- Do not enable routing, IP forwarding, internet NAT, or a DNS override for VPN peers.
- Generate server private key on the VM and client private key on the client. Accept a client public-key file; output only a configuration template with a private-key placeholder.
- Persist root-owned WireGuard configuration under `/etc/wireguard`, mode `0600`; enable `wg-quick@wg0` on boot. Never print the private-key-bearing configuration in status output.
- Peer management must add/list/revoke uniquely named device peers, reject duplicate keys/addresses, persist changes, and update live state without disconnecting unrelated peers.
- VPN authorization is device-key based, not human identity. Separate Linux accounts/SSH keys are needed for per-user shell access.
- Work client installation is confirmed. Outbound UDP availability and corporate policy still need verification; WireGuard does not have a TCP fallback.

## Development tooling

### Base (always installed)

`nano`, `emacs-nox`, `vim`, Git, curl, CA certificates, jq, ripgrep, unzip, build essentials, tmux, OpenSSH, and OpenCode 2.

### Optional modules

| Name | Responsibilities |
| --- | --- |
| `docker` | Docker Engine from official repository, Buildx, Compose plugin; default enabled |
| `python` | Ubuntu Python 3, pip, venv, pipx, development headers; do not bypass externally managed Python safeguards |
| `dotnet` | Supported .NET LTS SDK from a verified Ubuntu-compatible package source |
| `terraform` | Terraform CLI from HashiCorp's signed package repository |
| `node` | Supported Node.js LTS and npm; document optional package-manager installation explicitly |

Resolve and document exact supported versions at implementation time. Pin chosen major release lines and report installed versions; avoid an unbounded `latest` silently changing the toolchain's major version. Package updates within supported lines may occur. OpenCode 2 uses the v2 installer or a verified versioned standalone artifact, not the v1 npm package.

### Installer contract

- Entrypoint: `/opt/dev-bootstrap/install-dev-tools.sh` on the VM, sourced from repository `scripts/install-dev-tools.sh`.
- `--tools python,node,terraform` selects registered modules. Base is always installed. Docker is enabled by default for deployment, but post-deployment installer runs only explicitly requested optional modules so adding Python does not unexpectedly install Docker.
- Reject unknown tools before changes, deduplicate selections, and log each module's result.
- Modules are independently rerunnable: detect installed packages/repositories, avoid duplicated configuration, and verify their tools after installation.
- Require root for system changes; do not run user credential setup or OpenCode sessions as root.
- Prevent concurrent installer execution with a lock; use bounded retries for apt locks and transient downloads.
- Do not hide installation failures. Produce a clear final summary and nonzero exit on failure.
- New tools require a module, registry entry, documentation, and tests, not a rewrite of deployment logic.

## Storage and lifecycle

- Mount the managed disk at `/workspace` by filesystem UUID. Identify it by intended Azure LUN, never arbitrary `/dev/sdX` ordering.
- Format only a confirmed blank intended disk. Preserve recognized existing filesystems; reject ambiguous signatures/partitions rather than overwriting them.
- Create `/workspace/repos` owned by the development user. Keep `/workspace/docker` root-owned.
- Configure Docker data root as `/workspace/docker` before first service start; gate service start on the real workspace mount. A missing disk must not silently put Docker data on the OS disk.
- Docker installation can start its service automatically: prevent that during initial installation until mount and daemon configuration are ready. Merge any existing daemon JSON safely.
- Docker group membership is root-equivalent; default to `sudo docker`. Add membership only with an explicit option.
- Docker-published ports can bypass UFW; test external exposure and document localhost-bound port publishing as the default workflow.
- Data disk attachment uses `deleteOption: Detach`. Support creating a new disk or explicitly attaching an existing compatible disk, without trying to recreate/resize it implicitly.
- Retention is not backup. Deleting the resource group also deletes its disks; teardown must not promise preservation while deleting the disk's resource group.
- Document that repositories and Docker data persist, but OS-resident credentials, OpenCode session state, VPN identity, and home-directory configuration do not automatically survive OS replacement. Do not claim full session recovery; defer automatic credential migration.
- Optional shutdown interrupts running work and does not automatically restart the VM. Disk and public-IP charges continue while deallocated.

## Proposed file layout

```text
README.md
docs/implementation-plan.md
infra/main.bicep
infra/cloud-init.yaml
scripts/deploy.sh
scripts/install-dev-tools.sh
scripts/tools/base.sh
scripts/tools/docker.sh
scripts/tools/python.sh
scripts/tools/dotnet.sh
scripts/tools/terraform.sh
scripts/tools/node.sh
scripts/vpn/tailscale.sh
scripts/vpn/wireguard.sh
scripts/wireguard-peer.sh
scripts/status.sh
scripts/destroy.sh
tests/
```

Prefer a small single-VM template over unnecessary module fragmentation. Bundle bootstrap files into custom data within Azure size limits; do not depend on a nonexistent public Git repository or an unauthenticated mutable script URL.

## Proposed command interface

These are target interfaces, not currently executable commands:

```bash
# Work: Docker enabled by default
./scripts/deploy.sh --subscription SUBSCRIPTION_ID \
  --resource-group rg-opencode --name opencode \
  --ssh-public-key ~/.ssh/id_ed25519.pub \
  --vpn wireguard --wireguard-client-public-key ./work-laptop.pub \
  --tools python,node,terraform

# Personal
./scripts/deploy.sh --subscription SUBSCRIPTION_ID \
  --resource-group rg-opencode --name opencode \
  --ssh-public-key ~/.ssh/id_ed25519.pub --vpn tailscale --no-docker

# Later, on the VM: install only these optional modules
sudo /opt/dev-bootstrap/install-dev-tools.sh --tools python,dotnet
```

Additional deployment flags: location, VM size, admin username, workspace size/tier or existing disk ID, WireGuard port and addresses, optional Docker group membership, shutdown UTC time, `--what-if`, and `--help`. Defaults must be consistent between CLI, Bicep, bootstrap, and README.

## Implementation steps for Build agent

1. Reinspect repository before implementing; preserve any newly added files. Confirm Ubuntu image identifiers, API versions, UK South SKU availability, package signing sources, and OpenCode 2 installation behavior from current vendor documentation.
2. Implement the tooling dispatcher and independently rerunnable modules. Test parsing, validation, Docker default semantics, logging, and failure propagation.
3. Implement safe workspace mount/bootstrap, Docker mount dependencies, OpenSSH hardening, and mutually exclusive VPN setup. Validate sshd before reload, including Ubuntu socket-activation behavior.
4. Implement Bicep resources: VM, NIC with required accelerated networking, VNet/subnet, NSG, Standard public IP, disk, boot diagnostics, and optional shutdown schedule. Prefer Trusted Launch with supported Ubuntu image. Do not add a managed identity or roles without a concrete requirement.
5. Implement Azure CLI deployment: preflight dependencies/login/subscription/public-key inputs; validation and what-if; template deployment; bounded cloud-init readiness checks through Run Command; selected VPN enrollment; final SSH instructions and SSH host-key fingerprints obtained over the authenticated Azure channel.
6. Implement WireGuard peer management, status, recovery, deallocation/start guidance, and teardown with explicit retention semantics and confirmations. Re-running deployment must not rotate active VPN keys or format existing data.
7. Write README deployment and operations instructions, workstation WireGuard key generation/import, Tailscale login, model and Git authentication, tmux, localhost-bound Docker publishing, disk retention/replacement, upgrade behavior, and cost caveats.
8. Run static tests, then request explicit authorization before any live Azure deployment that incurs cost. Record executed verification separately from unperformed live tests.

## Verification and acceptance criteria

- Bicep compiles/lints; shell scripts pass syntax checks and ShellCheck where available; cloud-init schema validates.
- Test CLI on supported macOS and Linux Bash versions; explicitly state minimum version and avoid accidental GNU-only client dependencies.
- Both VPN modes produce expected NSG payloads; WireGuard opens only its configured UDP port, never TCP 22.
- Unknown tool/mode/address/key inputs fail before provisioning or modifying the guest.
- Fresh and repeated installs pass for all modules individually and in combination.
- Docker and Compose work, data root is on the mounted data disk, and service cannot start on an unmounted `/workspace` directory.
- Blank disk formatting, existing disk attachment, missing disk, filesystem ambiguity, and VM replacement paths are tested without destructive fallback.
- VPN SSH works after reboot; public SSH does not. Check unauthorized peers, peer revocation, UDP-blocked client diagnostics, and Docker-published-port exposure.
- Tailscale pending browser login cannot hang deployment indefinitely and can be resumed.
- Private keys and provider credentials are absent from Azure outputs, logs, custom data, and generated tracked files.
- Teardown retention test proves the workspace disk remains; resource-group deletion is explicitly destructive.

## Risks and open questions

- Subscription quota, SKU capacity, corporate outbound UDP restrictions, and VPN subnet overlap need deployment-time checks.
- A single VM is not highly available. Shutdown, reboots, host maintenance, and disk failure interrupt work.
- 128 GiB may be tight for large container images and multiple build trees; parameterize growth and document filesystem expansion. Never shrink an existing disk automatically.
- Installed tooling and retained Docker volumes can contain secrets and execute privileged code. Preserve least privilege and document backups/revocation.
- Exact release versions and artifact verification mechanisms must be validated during implementation; do not infer v2 behavior from OpenCode v1 documentation.
