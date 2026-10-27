# Local verification

Performed during implementation:

- `az bicep build --file infra/main.bicep --stdout`: passed with Bicep 0.40.2.
- `az bicep lint --file infra/main.bicep`: passed, no template diagnostics.
- `python3 -m unittest discover -s tests -v`: seven tests passed.
- `bash -n` on each shell script independently: passed.
- Generated cloud-init validated against the upstream Canonical `schema-cloud-config-v1.json`, using locally available PyYAML and jsonschema: passed.
- Deployment, status, teardown, and guest installer help commands: passed.

ShellCheck is not installed in the development environment, so ShellCheck was not run.

No Azure deployment, authenticated what-if, or guest package installation was performed. No billable resources were created. Before relying on this as a running environment, perform a live smoke test for each VPN mode, including SSH/public-port isolation, selected toolchain installation, Docker storage/mount dependencies, reboot behavior, additional peer revocation, and workspace-disk retention/replacement. Subscription quota and work-network UDP connectivity remain deployment-time checks.
