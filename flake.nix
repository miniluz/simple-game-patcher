{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-compat = {
      url = "https://git.lix.systems/lix-project/flake-compat/archive/main.tar.gz";
      flake = false;
    };
  };
  outputs =
    {
      nixpkgs,
      ...
    }:
    let
      inherit (nixpkgs) lib;

      supportedSystems = [
        "x86_64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      forAllSystems = lib.genAttrs supportedSystems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "simple-game-patcher";
            version = "1.0.0";
            src = ./.;
            format = "other";

            dontBuild = true;
            dontUnpack = true;

            installPhase = ''
              mkdir -p $out/bin
              cp ${./simple-game-patcher.py} $out/bin/simple-game-patcher
              chmod +x $out/bin/simple-game-patcher
            '';

            meta = {
              description = "Manage file overlays for game modifications";
              mainProgram = "simple-game-patcher";
            };
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            buildInputs = [
              (pkgs.python3.withPackages (python-pkgs: with python-pkgs; [
                pytest
              ]))
            ];
          };
        }
      );
    };
}
