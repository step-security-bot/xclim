from __future__ import annotations

from xclim import indices
from xclim.core.indicator import ResamplingIndicatorWithIndexing

__all__ = [
    "calm_days",
    "windy_days",
    "sfcWind_mean",
    "sfcWindmax_max",
]


class Wind(ResamplingIndicatorWithIndexing):
    """Indicator involving daily sfcWind series."""

    src_freq = "D"


calm_days = Wind(
    title="Calm days",
    identifier="calm_days",
    units="days",
    standard_name="number_of_days_with_sfcWind_below_threshold",
    long_name="Number of days with surface wind speed below {thresh}",
    description="{freq} number of days with surface wind speed below {thresh}.",
    abstract="Number of days with surface wind speed below threshold.",
    cell_methods="time: sum over days",
    compute=indices.calm_days,
)

windy_days = Wind(
    title="Windy days",
    identifier="windy_days",
    units="days",
    standard_name="number_of_days_with_sfcWind_above_threshold",
    long_name="Number of days with surface wind speed at or above {thresh}",
    description="{freq} number of days with surface wind speed at or above {thresh}.",
    abstract="Number of days with surface wind speed at or above threshold.",
    cell_methods="time: sum over days",
    compute=indices.windy_days,
)

sfcWind_mean = Wind(
    title="Mean near-surface wind speed",
    identifier="sfcWind_mean",
    units="m s-1",
    standard_name="wind_speed",
    long_name="Mean daily mean wind speed",
    description="{freq} mean of daily mean wind speed",
    abstract="Mean of daily near-surface wind speed.",
    cell_methods="time: mean over days",
    compute=indices.sfcWind_mean,
)

sfcWindmax_max = Wind(
    title="Maximum near-surface maximum wind speed",
    identifier="sfcWindmax_max",
    units="m s-1",
    standard_name="wind_speed",
    long_name="Maximum daily maximum wind speed",
    description="{freq} maximum of daily maximum wind speed",
    abstract="Maximum of daily maximum near-surface wind speed.",
    cell_methods="time: max over days",
    compute=indices.sfcWindmax_max,
)
