install_terraform() {
  printf 'Package: terraform\nPin: version 1.*\nPin-Priority: 1001\n' > /etc/apt/preferences.d/terraform
  signed_repo hashicorp https://apt.releases.hashicorp.com/gpg \
    'deb [arch=amd64 signed-by=/etc/apt/keyrings/hashicorp.asc] https://apt.releases.hashicorp.com noble main'
  apt_install terraform
  terraform version
}
