import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import deploy


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = Path(cls.tmp.name) / 'ssh'
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(cls.key)],
                       check=True, stdout=subprocess.DEVNULL)
        cls.peer = Path(cls.tmp.name) / 'client.pub'
        cls.peer.write_text(base64.b64encode(bytes(range(32))).decode())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def args(self, *extra):
        return deploy.parser().parse_args(['--subscription', 'test', '--resource-group', 'rg-test',
                                         '--ssh-public-key', str(self.key) + '.pub',
                                         '--vpn', 'tailscale', *extra])

    def test_defaults_and_explicit_post_install_tools(self):
        a = self.args('--tools', 'python,node,python')
        tools, _ = deploy.validate(a)
        self.assertEqual(tools, ['docker', 'python', 'node'])
        self.assertEqual(a.vm_size, 'Standard_D4s_v5')
        a = self.args('--no-docker')
        self.assertEqual(deploy.validate(a)[0], [])

    def test_validation_rejects_dangerous_or_conflicting_inputs(self):
        cases = [('--tools', 'python,unknown'), ('--no-docker', '--tools', 'docker'),
                 ('--no-docker', '--docker-group'), ('--shutdown-time', '2460'),
                 ('--wireguard-address', '10.88.0.1/24'),
                 ('--wireguard-client-address', '10.77.0.1/32'),
                 ('--wireguard-client-address', '10.77.0.255/32'),
                 ('--wireguard-port', '65536'), ('--name', "x';boom"),
                 ('--vpn', 'wireguard'), ('--wireguard-source-cidr', '::/0')]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                deploy.validate(self.args(*extra))

    def test_bundle_is_self_contained_and_has_no_private_keys(self):
        a = self.args('--vpn', 'wireguard', '--wireguard-client-public-key', str(self.peer),
                      '--tools', 'python,node')
        p = deploy.parameters(a)['parameters']
        cloud = base64.b64decode(p['customData']['value']).decode()
        self.assertTrue(cloud.startswith('#cloud-config'))
        self.assertLess(len(cloud.encode()), 65536)
        payload = cloud.split('    content: ')[1].splitlines()[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode='r:gz') as tar:
            names = tar.getnames()
            self.assertIn('tools/docker.sh', names)
            self.assertIn('vpn/wireguard.sh', names)
            settings = json.load(tar.extractfile('settings.json'))
            self.assertEqual(settings['tools'], 'docker,python,node')
            self.assertEqual(settings['vpn'], 'wireguard')
        self.assertNotIn('PRIVATE KEY-----', cloud)
        self.assertEqual(p['wireguardSourcePrefixes']['value'], ['0.0.0.0/0'])

    def test_render_only_never_calls_azure(self):
        output = Path(self.tmp.name) / 'parameters.json'
        with patch.object(sys, 'argv', ['deploy.py', '--subscription', 'test', '--resource-group', 'rg-test',
                                      '--ssh-public-key', str(self.key) + '.pub', '--vpn', 'tailscale',
                                      '--render-only', str(output)]), patch.object(deploy, 'az') as azure:
            deploy.main()
            azure.assert_not_called()
        self.assertEqual(json.loads(output.read_text())['parameters']['vpn']['value'], 'tailscale')

    def test_installer_rejects_unknown_tools_before_system_changes(self):
        result = subprocess.run(['bash', str(deploy.ROOT / 'scripts/install-dev-tools.sh'),
                                 '--tools', 'invalid'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Invalid tool selection', result.stderr)

    def test_what_if_missing_group_never_creates_resources(self):
        args = ['deploy.py', '--subscription', 'test', '--resource-group', 'rg-test',
                '--ssh-public-key', str(self.key) + '.pub', '--vpn', 'tailscale', '--what-if']
        with patch.object(sys, 'argv', args), patch.object(deploy, 'az') as azure:
            azure.side_effect = [{}, False]
            with self.assertRaisesRegex(ValueError, 'existing resource group'):
                deploy.main()
            self.assertEqual([call.args[:2] for call in azure.call_args_list],
                             [('account', 'show'), ('group', 'exists')])

    def test_existing_vm_cannot_be_changed_by_bootstrap_redeployment(self):
        args = ['deploy.py', '--subscription', 'test', '--resource-group', 'rg-test',
                '--ssh-public-key', str(self.key) + '.pub', '--vpn', 'tailscale']
        with patch.object(sys, 'argv', args), patch.object(deploy, 'az') as azure:
            azure.side_effect = [{}, True, ['opencode']]
            with self.assertRaisesRegex(ValueError, 'VM already exists'):
                deploy.main()
            self.assertFalse(any('create' in call.args for call in azure.call_args_list))


if __name__ == '__main__':
    unittest.main()
