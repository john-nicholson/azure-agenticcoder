install_dotnet() {
  # Ubuntu 24.04 supplies .NET 10 LTS in its own package feed.
  apt_install dotnet-sdk-10.0
  dotnet --list-sdks
}
