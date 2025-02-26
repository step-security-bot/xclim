"""
==============
Ensemble tools
==============

This submodule defines some useful methods for dealing with ensembles of climate simulations.
In xclim, an "ensemble" is a `Dataset` or a `DataArray` where multiple climate realizations
or models are concatenated along the `realization` dimension.
"""
from __future__ import annotations

from ._base import create_ensemble, ensemble_mean_std_max_min, ensemble_percentiles
from ._partitioning import fractional_uncertainty, hawkins_sutton, lafferty_sriver
from ._reduce import (
    kkz_reduce_ensemble,
    kmeans_reduce_ensemble,
    make_criteria,
    plot_rsqprofile,
)
from ._robustness import (
    change_significance,
    robustness_categories,
    robustness_coefficient,
    robustness_fractions,
)
