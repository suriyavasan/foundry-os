"""Company OS agent runtime: a lean "AI employee" launcher around the Hermes Agent fork.

Light on purpose: importing this package must never import Hermes (the launcher prepares
HERMES_HOME and the process environment first).
"""

__version__ = "0.1.0"
PROTOCOL_VERSION = 1
# Upstream Hermes Agent commit this tree was forked from (see FORK.md).
UPSTREAM_COMMIT = "db1fa153fe24a051af44ef8ee862b93e27998060"
