from .clients import IMSClient, list_sources  # noqa: F401
from .odbc_handlers import list_adsa_servers  # noqa: F401
from .utils import ReaderType, add_statoil_root_certificate  # noqa: F401

try:
    from .version import version as __version__
except ImportError:
    # Just in case it wasn't installed properly, for some reason
    from datetime import datetime

    __version__ = "unknown-" + datetime.today().strftime("%Y%m%d")
