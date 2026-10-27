install_python() {
  apt_install python3 python3-pip python3-venv python3-dev pipx
  python3 --version
  pipx --version
}
