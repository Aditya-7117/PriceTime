import os

from hypothesis import settings

# CI runs a fixed, larger set of examples so a red build reproduces exactly on rerun.
# Local runs stay random so each run explores inputs the last one did not.
settings.register_profile("ci", max_examples=500, derandomize=True, deadline=None, print_blob=True)
settings.register_profile("dev", max_examples=200, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
