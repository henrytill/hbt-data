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

        # buildPythonPackage, not buildPythonApplication: hbt-analysis imports
        # this to build the cross-implementation matrix -- one comparator over
        # four binaries, with the per-fixture results the text report throws
        # away -- and an application is not on the import path of a
        # python3.withPackages environment. flit still generates the console
        # script from [project.scripts], so `hbt-conformance` is unaffected.
        hbtConformance = pkgs.python3Packages.buildPythonPackage {
          pname = "hbt-conformance";
          # From __init__.py, which pyproject's dynamic version already makes
          # the one source the wheel is built from.
          version = builtins.head (
            builtins.match ".*__version__ = \"([^\"]+)\".*" (builtins.readFile ./hbt/conformance/__init__.py)
          );
          pyproject = true;
          build-system = [ pkgs.python3Packages.flit-core ];
          dependencies = with pkgs.python3Packages; [
            click
            pyyaml
          ];
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

        # The fixtures, the half the package leaves out. Selected by the names
        # the harness walks for (_walk in hbt/conformance/corpus.py) rather than
        # by directory, so a new category cannot be left out of the copy
        # unnoticed.
        corpus = pkgs.lib.fileset.toSource {
          root = ./.;
          fileset = pkgs.lib.fileset.fileFilter (
            file: pkgs.lib.hasInfix ".input." file.name || pkgs.lib.hasInfix ".expected." file.name
          ) ./.;
        };

        # The conformance check, for an implementation's flake to call; the
        # README says how to take this flake as an input. A function rather
        # than a callPackage file: callPackage fills any argument that names an
        # attribute of pkgs, and `tz` would be nixpkgs' tz package.
        check =
          {
            # The hbt executable to hold to the corpus.
            binary,
            # A waiver file in the format the README describes, or null.
            waivers ? null,
            # A timezone to force, or null for the sandbox's own.
            tz ? null,
          }:
          let
            # Interpolated, not left for escapeShellArgs to stringify: toString
            # on a path neither copies it to the store nor records a
            # dependency, so a path literal would be missing from the sandbox.
            args = [
              "--binary"
              "${binary}"
              "--corpus"
              corpus
            ]
            ++ pkgs.lib.optionals (waivers != null) [
              "--waivers"
              "${waivers}"
            ]
            ++ pkgs.lib.optionals (tz != null) [
              "--tz"
              tz
            ];
          in
          pkgs.runCommand "hbt-conformance" { } ''
            ${hbtConformance}/bin/hbt-conformance ${pkgs.lib.escapeShellArgs args}
            touch $out
          '';
      in
      {
        packages.hbt-conformance = hbtConformance;
        packages.default = hbtConformance;
        packages.python = pkgs.python3.withPackages (_: [ hbtConformance ]);

        lib.check = check;

        # The check is exercised here before any implementation calls it,
        # against tests/hbt-stub: the run conforms only if the waiver file
        # reaches the sandbox. Both are path literals, the way an
        # implementation passes them.
        checks.conformance = check {
          binary = ./tests/hbt-stub;
          waivers = ./tests/stub.waivers;
        };

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
