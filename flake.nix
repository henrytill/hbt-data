{
  description = "The hbt corpus and its conformance harness";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        hbtConformance = pkgs.python3Packages.buildPythonApplication {
          pname = "hbt-conformance";
          # From __init__.py, which pyproject's dynamic version already makes
          # the one source the wheel is built from.
          version = builtins.head (
            builtins.match ".*__version__ = \"([^\"]+)\".*" (builtins.readFile ./hbt/conformance/__init__.py)
          );
          pyproject = true;
          build-system = [ pkgs.python3Packages.flit-core ];
          dependencies = [ pkgs.python3Packages.pyyaml ];
          # unittest from the standard library; the tests are the normalizer's,
          # and adding a test runner to the dependency closure of four
          # implementations to run sixteen of them is not a trade worth making.
          checkPhase = "python -m unittest discover -s tests -t .";
          # Just the harness. The fixtures are the other half of this
          # repository and are far larger than the code; a consumer that wants
          # them has the submodule, and baking them into the derivation would
          # rebuild the package whenever a fixture changed.
          src = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./hbt/conformance
              ./tests
              # The schema is the authority on field names and types, and a
              # test holds the normalizer's roster to it.
              ./collection.schema.json
              ./pyproject.toml
              ./README.md
            ];
          };
        };
      in
      {
        packages.hbt-conformance = hbtConformance;
        packages.default = hbtConformance;

        apps.default = {
          type = "app";
          program = "${hbtConformance}/bin/hbt-conformance";
        };

        # A flake check rather than a build step, so mypy is not a build input
        # of the four implementations that consume this package.
        #
        # pyproject.toml is the one place the tool configuration lives, so the
        # check reads it with --config-file rather than restating any of it
        # here. The source has to be copied because explicit_package_bases
        # makes the working directory the package root, so the tree has to sit
        # at `hbt/conformance` for the module to be `hbt.conformance`, and a
        # store path's basename is a hash.
        checks.mypy =
          pkgs.runCommand "hbt-conformance-mypy"
            {
              nativeBuildInputs = [
                pkgs.python3Packages.mypy
                # PyYAML ships no py.typed, so its stubs have to be added
                # alongside it. A stub is not a runtime dependency, so this is
                # the one thing the package cannot supply.
                pkgs.python3Packages.types-pyyaml
              ]
              ++ hbtConformance.propagatedBuildInputs;
            }
            ''
              mkdir hbt
              cp -r ${./hbt/conformance} hbt/conformance
              cp -r ${./tests} tests
              mypy --config-file ${./pyproject.toml} hbt/conformance tests
              touch $out
            '';

        devShells.default = pkgs.mkShell {
          inputsFrom = [ hbtConformance ];
          packages =
            (with pkgs; [
              nixfmt
              pyright
            ])
            ++ (with pkgs.python3Packages; [
              black
              flake8
              isort
              mypy
              pylint
              types-pyyaml
            ]);
        };
      }
    );
}
