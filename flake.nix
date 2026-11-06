{
  description = "Cross-platform native reconciliation adapter for Nix deployments";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      # pyproject.toml holds the only version literal.
      inherit (builtins.fromTOML (builtins.readFile ./pyproject.toml)) project;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          nix-adapter = pkgs.python3Packages.buildPythonPackage {
            pname = "nix-adapter";
            inherit (project) version;
            src = ./.;
            format = "pyproject";
            nativeBuildInputs = [ pkgs.python3Packages.flit-core ];
            nativeCheckInputs = [ pkgs.gitMinimal ];
            propagatedBuildInputs = [
              pkgs.python3Packages.rich
            ];
            checkPhase = ''
              python -m unittest discover -s tests
            '';
          };
          adapterPython = pkgs.python3.withPackages (_: [ nix-adapter ]);
        in
        {
          inherit nix-adapter adapterPython;
          default = nix-adapter;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = self.packages.${system}.adapterPython;
        in
        {
          package = self.packages.${system}.default;

          source-format =
            pkgs.runCommand "nix-adapter-source-format"
              {
                nativeBuildInputs = [ pkgs.ruff ];
              }
              ''
                ruff check --no-cache ${./.}
                ruff format --no-cache --check ${./.}
                touch "$out"
              '';

          source-lint =
            pkgs.runCommand "nix-adapter-source-lint"
              {
                nativeBuildInputs = [
                  pkgs.actionlint
                  pkgs.deadnix
                  pkgs.nixfmt
                  pkgs.shellcheck
                  pkgs.statix
                ];
              }
              ''
                cd ${./.}
                statix check .
                deadnix --fail flake.nix
                nixfmt --check flake.nix
                actionlint .github/workflows/*.yml
                touch "$out"
              '';

          python-types =
            pkgs.runCommand "nix-adapter-python-types"
              {
                nativeBuildInputs = [ pkgs.pyright ];
              }
              ''
                cd ${./.}
                pyright --pythonpath ${python}/bin/python
                touch "$out"
              '';
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              self.packages.${system}.adapterPython
              pkgs.python3Packages.flit-core
              pkgs.pyright
              pkgs.ruff
              pkgs.git-cliff
              pkgs.just
            ];
          };
        }
      );
    };
}
