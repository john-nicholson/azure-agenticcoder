install_node() {
  signed_repo nodesource https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    'deb [arch=amd64 signed-by=/etc/apt/keyrings/nodesource.asc] https://deb.nodesource.com/node_24.x nodistro main'
  apt_install nodejs
  node --version
  npm --version
}
