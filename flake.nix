{
  description = "Poelis MCP development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    flake-parts.inputs.nixpkgs-lib.follows = "nixpkgs";
  };

  outputs = inputs:
    inputs.flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];
      perSystem = { pkgs, ... }: {
        devShells.default = pkgs.mkShellNoCC {
          packages = with pkgs; [ python315 uv ruff just direnv ];
          UV_PYTHON = "${pkgs.python315}/bin/python3";
          UV_PYTHON_DOWNLOADS = "never";
          UV_LINK_MODE = "copy";
        };
      };
    };
}
