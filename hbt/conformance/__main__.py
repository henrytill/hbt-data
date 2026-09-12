from hbt.conformance.cli import cli

# Click supplies every parameter from the command line; pylint reads the
# decorated function's signature and sees nine missing arguments.
cli(prog_name="hbt-conformance")  # pylint: disable=no-value-for-parameter
