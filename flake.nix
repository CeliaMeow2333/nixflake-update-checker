{
  description = "Check a Nix flake against upstream and summarize package version and download changes";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      packageFor = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          py = pkgs.python3Packages;
        in
        py.buildPythonApplication {
          pname = "nixflake-update-checker";
          version = "0.1.0";
          format = "pyproject";

          src = nixpkgs.lib.fileset.toSource {
            root = ./.;
            fileset = nixpkgs.lib.fileset.unions [
              ./pyproject.toml
              ./README.md
              ./src
              ./tests
            ];
          };

          nativeBuildInputs = [ py.setuptools ];
          dependencies = [ py.rich ];

          nativeCheckInputs = [ py.pytestCheckHook ];
          pythonImportsCheck = [ "nixflake_update_checker" ];

          meta = {
            description = "Compare a local Nix flake with upstream and summarize version and download changes";
            homepage = "https://github.com/CeliaMeow2333/nixflake-update-checker";
            license = nixpkgs.lib.licenses.mit;
            mainProgram = "nixflake-check";
            platforms = nixpkgs.lib.platforms.unix;
          };
        };
    in
    {
      packages = forAllSystems (system: {
        default = packageFor system;
        nixflake-check = packageFor system;
      });

      apps = forAllSystems (system:
        let
          app = {
            type = "app";
            program = "${packageFor system}/bin/nixflake-check";
            meta.description = "Check a Nix flake against upstream";
          };
        in
        {
          default = app;
          nixflake-check = app;
        });

      checks = forAllSystems (system: {
        default = packageFor system;
      });

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.python3Packages.rich
              pkgs.python3Packages.pytest
              pkgs.nix
            ];
          };
        });

      overlays.default = final: prev: {
        nixflake-check = self.packages.${final.stdenv.hostPlatform.system}.default;
      };

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.programs.nixflake-check;
        in
        {
          options.programs.nixflake-check = {
            enable = lib.mkEnableOption "the nixflake-update-checker CLI";

            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
              defaultText = lib.literalExpression
                "nixflake-checker.packages.\${pkgs.stdenv.hostPlatform.system}.default";
              description = "The nixflake-check package to install.";
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ cfg.package ];
          };
        };

      nixosModules.nixflake-check = self.nixosModules.default;
    };
}
