"""Climate indices computation package based on Xarray."""
from __future__ import annotations

try:
    from importlib.resources import files as _files
except ImportError:
    from importlib_resources import files as _files

from xclim import indices
from xclim.core import units  # noqa
from xclim.core.indicator import build_indicator_module_from_yaml
from xclim.core.locales import load_locale
from xclim.core.options import set_options  # noqa
from xclim.indicators import atmos, generic, land, seaIce  # noqa

__author__ = """Travis Logan"""
__email__ = "logan.travis@ouranos.ca"
__version__ = "0.47.5-dev.8"


_module_data = _files("xclim.data")

# Load official locales
for filename in _module_data.glob("??.json"):
    # Only select <locale>.json and not <module>.<locale>.json
    load_locale(filename, filename.stem)


# Virtual modules creation:
build_indicator_module_from_yaml(_module_data / "icclim", mode="raise")
build_indicator_module_from_yaml(_module_data / "anuclim", mode="raise")
build_indicator_module_from_yaml(_module_data / "cf", mode="raise")
