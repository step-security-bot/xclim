"""Statistic-related functions. See the `frequency_analysis` notebook for examples."""
from __future__ import annotations

import warnings
from typing import Any, Sequence

import lmoments3.distr
import numpy as np
import xarray as xr

from xclim.core.calendar import resample_doy, select_time
from xclim.core.formatting import prefix_attrs, unprefix_attrs, update_history
from xclim.core.units import convert_units_to
from xclim.core.utils import Quantified, uses_dask

from . import generic

__all__ = [
    "_fit_start",
    "_lm3_dist_map",
    "dist_method",
    "fa",
    "fit",
    "frequency_analysis",
    "get_dist",
    "get_lm3_dist",
    "parametric_cdf",
    "parametric_quantile",
    "preprocess_standardized_index",
    "standardized_index",
    "standardized_index_fit_params",
]


# Map the scipy distribution name to the lmoments3 name. Distributions with mismatched parameters are excluded.
_lm3_dist_map = {
    "expon": "exp",
    "gamma": "gam",
    "genextreme": "gev",
    # "genlogistic": "glo",
    # "gennorm": "gno",
    "genpareto": "gpa",
    "gumbel_r": "gum",
    # "kappa4": "kap",
    "norm": "nor",
    "pearson3": "pe3",
    "weibull_min": "wei",
}


# Fit the parameters.
# This would also be the place to impose constraints on the series minimum length if needed.
def _fitfunc_1d(arr, *, dist, nparams, method, **fitkwargs):
    """Fit distribution parameters."""
    x = np.ma.masked_invalid(arr).compressed()  # pylint: disable=no-member

    # Return NaNs if array is empty.
    if len(x) <= 1:
        return np.asarray([np.nan] * nparams)

    # Estimate parameters
    if method in ["ML", "MLE"]:
        args, kwargs = _fit_start(x, dist.name, **fitkwargs)
        params = dist.fit(x, *args, method="mle", **kwargs, **fitkwargs)
    elif method == "MM":
        params = dist.fit(x, method="mm", **fitkwargs)
    elif method == "PWM":
        params = list(dist.lmom_fit(x).values())
    elif method == "APP":
        args, kwargs = _fit_start(x, dist.name, **fitkwargs)
        kwargs_list = list(kwargs.values())
        if "loc" not in kwargs:
            kwargs_list = [0] + kwargs_list
        params = list(args) + kwargs_list
    else:
        raise NotImplementedError(f"Unknown method `{method}`.")

    params = np.asarray(params)

    # Fill with NaNs if one of the parameters is NaN
    if np.isnan(params).any():
        params[:] = np.nan

    return params


def fit(
    da: xr.DataArray,
    dist: str = "norm",
    method: str = "ML",
    dim: str = "time",
    **fitkwargs: Any,
) -> xr.DataArray:
    r"""Fit an array to a univariate distribution along the time dimension.

    Parameters
    ----------
    da : xr.DataArray
        Time series to be fitted along the time dimension.
    dist : str
        Name of the univariate distribution, such as beta, expon, genextreme, gamma, gumbel_r, lognorm, norm
        (see :py:mod:scipy.stats for full list). If the PWM method is used, only the following distributions are
        currently supported: 'expon', 'gamma', 'genextreme', 'genpareto', 'gumbel_r', 'pearson3', 'weibull_min'.
    method : {"ML" or "MLE", "MM", "PWM", "APP"}
        Fitting method, either maximum likelihood (ML or MLE), method of moments (MM),
        probability weighted moments (PWM), also called L-Moments, or approximate method (APP).
        The PWM method is usually more robust to outliers.
    dim : str
        The dimension upon which to perform the indexing (default: "time").
    \*\*fitkwargs
        Other arguments passed directly to :py:func:`_fitstart` and to the distribution's `fit`.

    Returns
    -------
    xr.DataArray
        An array of fitted distribution parameters.

    Notes
    -----
    Coordinates for which all values are NaNs will be dropped before fitting the distribution. If the array still
    contains NaNs, the distribution parameters will be returned as NaNs.
    """
    method = method.upper()
    method_name = {
        "ML": "maximum likelihood",
        "MM": "method of moments",
        "MLE": "maximum likelihood",
        "PWM": "probability weighted moments",
        "APP": "approximative method",
    }
    if method not in method_name:
        raise ValueError(f"Fitting method not recognized: {method}")

    # Get the distribution
    dc = get_dist(dist)
    if method == "PWM":
        lm3dc = get_lm3_dist(dist)
    else:
        lm3dc = None

    shape_params = [] if dc.shapes is None else dc.shapes.split(",")
    dist_params = shape_params + ["loc", "scale"]

    data = xr.apply_ufunc(
        _fitfunc_1d,
        da,
        input_core_dims=[[dim]],
        output_core_dims=[["dparams"]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
        keep_attrs=True,
        kwargs=dict(
            # Don't know how APP should be included, this works for now
            dist=dc if method in ["ML", "MLE", "MM", "APP"] else lm3dc,
            nparams=len(dist_params),
            method=method,
            **fitkwargs,
        ),
        dask_gufunc_kwargs={"output_sizes": {"dparams": len(dist_params)}},
    )

    # Add coordinates for the distribution parameters and transpose to original shape (with dim -> dparams)
    dims = [d if d != dim else "dparams" for d in da.dims]
    out = data.assign_coords(dparams=dist_params).transpose(*dims)

    out.attrs = prefix_attrs(
        da.attrs, ["standard_name", "long_name", "units", "description"], "original_"
    )
    attrs = dict(
        long_name=f"{dist} parameters",
        description=f"Parameters of the {dist} distribution",
        method=method,
        estimator=method_name[method].capitalize(),
        scipy_dist=dist,
        units="",
        history=update_history(
            f"Estimate distribution parameters by {method_name[method]} method along dimension {dim}.",
            new_name="fit",
            data=da,
        ),
    )
    out.attrs.update(attrs)
    return out


def parametric_quantile(p: xr.DataArray, q: float | Sequence[float]) -> xr.DataArray:
    """Return the value corresponding to the given distribution parameters and quantile.

    Parameters
    ----------
    p : xr.DataArray
        Distribution parameters returned by the `fit` function.
        The array should have dimension `dparams` storing the distribution parameters,
        and attribute `scipy_dist`, storing the name of the distribution.
    q : float or Sequence of float
        Quantile to compute, which must be between `0` and `1`, inclusive.

    Returns
    -------
    xarray.DataArray
        An array of parametric quantiles estimated from the distribution parameters.

    Notes
    -----
    When all quantiles are above 0.5, the `isf` method is used instead of `ppf` because accuracy is sometimes better.
    """
    q = np.atleast_1d(q)

    # Get the distribution
    dist = p.attrs["scipy_dist"]
    dc = get_dist(dist)

    # Create a lambda function to facilitate passing arguments to dask. There is probably a better way to do this.
    if np.all(q > 0.5):

        def func(x):
            return dc.isf(1 - q, *x)

    else:

        def func(x):
            return dc.ppf(q, *x)

    data = xr.apply_ufunc(
        func,
        p,
        input_core_dims=[["dparams"]],
        output_core_dims=[["quantile"]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
        keep_attrs=True,
        dask_gufunc_kwargs={"output_sizes": {"quantile": len(q)}},
    )

    # Assign quantile coordinates and transpose to preserve original dimension order
    dims = [d if d != "dparams" else "quantile" for d in p.dims]
    out = data.assign_coords(quantile=q).transpose(*dims)
    out.attrs = unprefix_attrs(p.attrs, ["units", "standard_name"], "original_")

    attrs = dict(
        long_name=f"{dist} quantiles",
        description=f"Quantiles estimated by the {dist} distribution",
        cell_methods="dparams: ppf",
        history=update_history(
            "Compute parametric quantiles from distribution parameters",
            new_name="parametric_quantile",
            parameters=p,
        ),
    )
    out.attrs.update(attrs)
    return out


def parametric_cdf(p: xr.DataArray, v: float | Sequence[float]) -> xr.DataArray:
    """Return the cumulative distribution function corresponding to the given distribution parameters and value.

    Parameters
    ----------
    p : xr.DataArray
        Distribution parameters returned by the `fit` function.
        The array should have dimension `dparams` storing the distribution parameters,
        and attribute `scipy_dist`, storing the name of the distribution.
    v : float or Sequence of float
        Value to compute the CDF.

    Returns
    -------
    xarray.DataArray
        An array of parametric CDF values estimated from the distribution parameters.
    """
    v = np.atleast_1d(v)

    # Get the distribution
    dist = p.attrs["scipy_dist"]
    dc = get_dist(dist)

    # Create a lambda function to facilitate passing arguments to dask. There is probably a better way to do this.
    def func(x):
        return dc.cdf(v, *x)

    data = xr.apply_ufunc(
        func,
        p,
        input_core_dims=[["dparams"]],
        output_core_dims=[["cdf"]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
        keep_attrs=True,
        dask_gufunc_kwargs={"output_sizes": {"cdf": len(v)}},
    )

    # Assign quantile coordinates and transpose to preserve original dimension order
    dims = [d if d != "dparams" else "cdf" for d in p.dims]
    out = data.assign_coords(cdf=v).transpose(*dims)
    out.attrs = unprefix_attrs(p.attrs, ["units", "standard_name"], "original_")

    attrs = dict(
        long_name=f"{dist} cdf",
        description=f"CDF estimated by the {dist} distribution",
        cell_methods="dparams: cdf",
        history=update_history(
            "Compute parametric cdf from distribution parameters",
            new_name="parametric_cdf",
            parameters=p,
        ),
    )
    out.attrs.update(attrs)
    return out


def fa(
    da: xr.DataArray,
    t: int | Sequence,
    dist: str = "norm",
    mode: str = "max",
    method: str = "ML",
) -> xr.DataArray:
    """Return the value corresponding to the given return period.

    Parameters
    ----------
    da : xr.DataArray
        Maximized/minimized input data with a `time` dimension.
    t : int or Sequence of int
        Return period. The period depends on the resolution of the input data. If the input array's resolution is
        yearly, then the return period is in years.
    dist : str
        Name of the univariate distribution, such as:
        `beta`, `expon`, `genextreme`, `gamma`, `gumbel_r`, `lognorm`, `norm`
    mode : {'min', 'max}
        Whether we are looking for a probability of exceedance (max) or a probability of non-exceedance (min).
    method : {"ML", "MLE", "MOM", "PWM", "APP"}
        Fitting method, either maximum likelihood (ML or MLE), method of moments (MOM),
        probability weighted moments (PWM), also called L-Moments, or approximate method (APP).
        The PWM method is usually more robust to outliers.

    Returns
    -------
    xarray.DataArray
        An array of values with a 1/t probability of exceedance (if mode=='max').

    See Also
    --------
    scipy.stats : For descriptions of univariate distribution types.
    """
    # Fit the parameters of the distribution
    p = fit(da, dist, method=method)
    t = np.atleast_1d(t)

    if mode in ["max", "high"]:
        q = 1 - 1.0 / t

    elif mode in ["min", "low"]:
        q = 1.0 / t

    else:
        raise ValueError(f"Mode `{mode}` should be either 'max' or 'min'.")

    # Compute the quantiles
    out = (
        parametric_quantile(p, q)
        .rename({"quantile": "return_period"})
        .assign_coords(return_period=t)
    )
    out.attrs["mode"] = mode
    return out


def frequency_analysis(
    da: xr.DataArray,
    mode: str,
    t: int | Sequence[int],
    dist: str,
    window: int = 1,
    freq: str | None = None,
    method: str = "ML",
    **indexer: int | float | str,
) -> xr.DataArray:
    r"""Return the value corresponding to a return period.

    Parameters
    ----------
    da : xarray.DataArray
        Input data.
    mode : {'min', 'max'}
        Whether we are looking for a probability of exceedance (high) or a probability of non-exceedance (low).
    t : int or sequence
        Return period. The period depends on the resolution of the input data. If the input array's resolution is
        yearly, then the return period is in years.
    dist : str
        Name of the univariate distribution, e.g. `beta`, `expon`, `genextreme`, `gamma`, `gumbel_r`, `lognorm`, `norm`.
    window : int
        Averaging window length (days).
    freq : str, optional
        Resampling frequency. If None, the frequency is assumed to be 'YS' unless the indexer is season='DJF',
        in which case `freq` would be set to `AS-DEC`.
    method : {"ML" or "MLE", "MOM", "PWM", "APP"}
        Fitting method, either maximum likelihood (ML or MLE), method of moments (MOM),
        probability weighted moments (PWM), also called L-Moments, or approximate method (APP).
        The PWM method is usually more robust to outliers.
    \*\*indexer
        Time attribute and values over which to subset the array. For example, use season='DJF' to select winter values,
        month=1 to select January, or month=[6,7,8] to select summer months. If indexer is not provided, all values are
        considered.

    Returns
    -------
    xarray.DataArray
        An array of values with a 1/t probability of exceedance or non-exceedance when mode is high or low respectively.

    See Also
    --------
    scipy.stats : For descriptions of univariate distribution types.
    """
    # Apply rolling average
    attrs = da.attrs.copy()
    if window > 1:
        da = da.rolling(time=window).mean(skipna=False)
        da.attrs.update(attrs)

    # Assign default resampling frequency if not provided
    freq = freq or generic.default_freq(**indexer)

    # Extract the time series of min or max over the period
    sel = generic.select_resample_op(da, op=mode, freq=freq, **indexer)

    if uses_dask(sel):
        sel = sel.chunk({"time": -1})
    # Frequency analysis
    return fa(sel, t, dist=dist, mode=mode, method=method)


def get_dist(dist: str):
    """Return a distribution object from `scipy.stats`."""
    from scipy import stats  # pylint: disable=import-outside-toplevel

    dc = getattr(stats, dist, None)
    if dc is None:
        e = f"Statistical distribution `{dist}` is not found in scipy.stats."
        raise ValueError(e)
    return dc


def get_lm3_dist(dist: str):
    """Return a distribution object from `lmoments3.distr`."""
    if dist not in _lm3_dist_map:
        raise ValueError(
            f"The PWM fitting method cannot be used with the {dist} distribution, as it is not supported "
            f"by `lmoments3`."
        )

    return getattr(lmoments3.distr, _lm3_dist_map[dist])


def _fit_start(x, dist: str, **fitkwargs: Any) -> tuple[tuple, dict]:
    r"""Return initial values for distribution parameters.

    Providing the ML fit method initial values can help the optimizer find the global optimum.

    Parameters
    ----------
    x : array-like
        Input data.
    dist : str
        Name of the univariate distribution, e.g. `beta`, `expon`, `genextreme`, `gamma`, `gumbel_r`, `lognorm`, `norm`.
        (see :py:mod:scipy.stats). Only `genextreme` and `weibull_exp` distributions are supported.
    \*\*fitkwargs
        Kwargs passed to fit.

    Returns
    -------
    tuple, dict

    References
    ----------
    :cite:cts:`coles_introduction_2001,cohen_parameter_2019, thom_1958`

    """
    x = np.asarray(x)
    m = x.mean()
    v = x.var()

    if dist == "genextreme":
        s = np.sqrt(6 * v) / np.pi
        return (0.1,), {"loc": m - 0.57722 * s, "scale": s}

    if dist == "genpareto" and "floc" in fitkwargs:
        # Taken from julia' Extremes. Case for when "mu/loc" is known.
        t = fitkwargs["floc"]
        if not np.isclose(t, 0):
            m = (x - t).mean()
            v = (x - t).var()

        c = 0.5 * (1 - m**2 / v)
        scale = (1 - c) * m
        return (c,), {"scale": scale}

    if dist in "weibull_min":
        s = x.std()
        loc = x.min() - 0.01 * s
        chat = np.pi / np.sqrt(6) / (np.log(x - loc)).std()
        scale = ((x - loc) ** chat).mean() ** (1 / chat)
        return (chat,), {"loc": loc, "scale": scale}

    if dist in ["gamma"]:
        x_pos = x[x > 0]
        m = x_pos.mean()
        log_of_mean = np.log(m)
        mean_of_logs = np.log(x_pos).mean()
        a = log_of_mean - mean_of_logs
        alpha = (1 + np.sqrt(1 + 4 * a / 3)) / (4 * a)
        beta = m / alpha
        return (alpha,), {"scale": beta}

    if dist in ["fisk"]:
        x_pos = x[x > 0]
        m = x_pos.mean()
        v = x_pos.var()
        # pdf =  (beta/alpha) (x/alpha)^{beta-1}/ (1+(x/alpha)^{beta})^2
        # Compute f_1 and f_2 which only depend on beta:
        # f_1 := mean/alpha = <x>/alpha
        # f_2 := variance/alpha^2 = (<x^2> - <x>^2)/alpha^2
        # In the large beta limit, f_1 -> 1 and f_1/sqrt(f_2) -> 0.56*beta - 0.25
        # Solve for alpha and beta below:
        alpha, beta = m, (1 / 0.56) * (m / np.sqrt(v) + 1 / 4)
        return (beta,), {"scale": alpha}
    return (), {}


def _dist_method_1D(*args, dist: str, function: str, **kwargs: Any) -> xr.DataArray:
    r"""Statistical function for given argument on given distribution initialized with params.

    See :py:ref:`scipy:scipy.stats.rv_continuous` for all available functions and their arguments.
    Every method where `"*args"` are the distribution parameters can be wrapped.

    Parameters
    ----------
    \*args
        The arguments for the requested scipy function.
    dist : str
        The scipy name of the distribution.
    function : str
        The name of the function to call.
    \*\*kwargs
        Other parameters to pass to the function call.

    Returns
    -------
    array_like
    """
    dist = get_dist(dist)
    return getattr(dist, function)(*args, **kwargs)


def dist_method(
    function: str,
    fit_params: xr.DataArray,
    arg: xr.DataArray | None = None,
    **kwargs: Any,
) -> xr.DataArray:
    r"""Vectorized statistical function for given argument on given distribution initialized with params.

    Methods where `"*args"` are the distribution parameters can be wrapped, except those that return new dimensions
    (Ex: 'rvs' with size != 1, 'stats' with more than one moment, 'interval', 'support')

    Parameters
    ----------
    function : str
        The name of the function to call.
    fit_params : xr.DataArray
        Distribution parameters are along `dparams`, in the same order as given by :py:func:`fit`.
        Must have a `scipy_dist` attribute with the name of the distribution fitted.
    arg : array_like, optional
        The first argument for the requested function if different from `fit_params`.
    \*\*kwargs
        Other parameters to pass to the function call.

    Returns
    -------
    array_like
        Same shape as arg.

    See Also
    --------
    scipy:scipy.stats.rv_continuous : for all available functions and their arguments.
    """
    # Typically the data to be transformed
    arg = [arg] if arg is not None else []
    args = arg + [fit_params.sel(dparams=dp) for dp in fit_params.dparams.values]
    return xr.apply_ufunc(
        _dist_method_1D,
        *args,
        kwargs={"dist": fit_params.attrs["scipy_dist"], "function": function, **kwargs},
        output_dtypes=[float],
        dask="parallelized",
    )


def preprocess_standardized_index(
    da: xr.DataArray, freq: str | None, window: int, **indexer
):
    r"""Perform resample and roll operations involved in computing a standardized index.

    da : xarray.DataArray
        Input array.
    freq : {D, MS}, optional
        Resampling frequency. A monthly or daily frequency is expected. Option `None` assumes that desired resampling
        has already been applied input dataset and will skip the resampling step.
    window : int
        Averaging window length relative to the resampling frequency. For example, if `freq="MS"`,
        i.e. a monthly resampling, the window is an integer number of months.
    \*\*indexer
        Indexing parameters to compute the indicator on a temporal subset of the data.
        It accepts the same arguments as :py:func:`xclim.indices.generic.select_time`.

    Returns
    -------
    xarray.DataArray, str
        Processed array and time grouping corresponding to the final time frequency
        (following resampling if applicable).
    """
    # We could allow a more general frequency in this function and move
    # the constraint {"D", "MS"} in specific indices such as SPI / SPEI.
    final_freq = freq or xr.infer_freq(da.time)
    try:
        group = {"D": "time.dayofyear", "MS": "time.month"}[final_freq]
    except KeyError():
        raise ValueError(
            f"The input (following resampling if applicable) has a frequency `{final_freq}`"
            "which is not supported for standardized indices."
        )

    if freq is not None:
        da = da.resample(time=freq).mean(keep_attrs=True)

    if uses_dask(da) and len(da.chunks[da.get_axis_num("time")]) > 1:
        warnings.warn(
            "The input data is chunked on time dimension and must be fully rechunked to"
            " run `fit` on groups ."
            " Beware, this operation can significantly increase the number of tasks dask"
            " has to handle.",
            stacklevel=2,
        )
        da = da.chunk({"time": -1})

    if window > 1:
        da = da.rolling(time=window).mean(skipna=False, keep_attrs=True)

    # The time reduction must be done after the rolling
    da = select_time(da, **indexer)
    return da, group


def standardized_index_fit_params(
    da: xr.DataArray,
    freq: str | None,
    window: int,
    dist: str,
    method: str,
    offset: Quantified | None = None,
    **indexer,
) -> xr.DataArray:
    r"""Standardized Index fitting parameters.

    A standardized index measures the deviation of a variable averaged over a rolling temporal window and
    fitted with a given distribution `dist` with respect to a calibration dataset. The comparison is done by porting
    back results to a normalized distribution. The fitting parameters of the calibration dataset fitted with `dist`
    are obtained here.

    Parameters
    ----------
    da : xarray.DataArray
        Input array.
    freq : str, optional
        Resampling frequency. A monthly or daily frequency is expected. Option `None` assumes that desired resampling
        has already been applied input dataset and will skip the resampling step.
    window : int
        Averaging window length relative to the resampling frequency. For example, if `freq="MS"`,
        i.e. a monthly resampling, the window is an integer number of months.
    dist : {'gamma', 'fisk'}
        Name of the univariate distribution. (see :py:mod:`scipy.stats`).
    method : {'ML', 'APP', 'PWM'}
        Name of the fitting method, such as `ML` (maximum likelihood), `APP` (approximate). The approximate method
        uses a deterministic function that doesn't involve any optimization.
    offset: Quantified
        Distributions bounded by zero (e.g. "gamma", "fisk") can be used for datasets with negative values
        by using an offset: `da + offset`.
    \*\*indexer
        Indexing parameters to compute the indicator on a temporal subset of the data.
        It accepts the same arguments as :py:func:`xclim.indices.generic.select_time`.

    Returns
    -------
    xarray.DataArray
        Standardized Index fitting parameters. The time dimension of the initial array is reduced to

    Notes
    -----
    Supported combinations of `dist` and `method` are:
    * Gamma ("gamma") : "ML", "APP", "PWM"
    * Log-logistic ("fisk") : "ML", "APP"
    """
    # "WPM" method doesn't seem to work for gamma or pearson3
    dist_and_methods = {"gamma": ["ML", "APP", "PWM"], "fisk": ["ML", "APP"]}
    if dist not in dist_and_methods:
        raise NotImplementedError(f"The distribution `{dist}` is not supported.")
    if method not in dist_and_methods[dist]:
        raise NotImplementedError(
            f"The method `{method}` is not supported for distribution `{dist}`."
        )

    if offset is not None:
        with xr.set_options(keep_attrs=True):
            da = da + convert_units_to(offset, da, context="hydro")

    da, group = preprocess_standardized_index(da, freq, window, **indexer)
    params = da.groupby(group).map(fit, dist=dist, method=method)
    cal_range = (
        da.time.min().dt.strftime("%Y-%m-%d").item(),
        da.time.max().dt.strftime("%Y-%m-%d").item(),
    )
    params.attrs = {
        "calibration_period": cal_range,
        "freq": freq or "",
        "window": window,
        "scipy_dist": dist,
        "method": method,
        "group": group,
        "units": "",
        "offset": offset or "",
    }
    if indexer != {}:
        method, args = indexer.popitem()
    else:
        method, args = "", []
    params.attrs["time_indexer"] = (method, *args)

    return params


def standardized_index(da: xr.DataArray, params: xr.DataArray):
    """Compute standardized index for given fit parameters.

    This computes standardized indices which measure the deviation of  variables in the dataset compared
    to a reference distribution. The reference is a statistical distribution computed with fitting parameters `params`
    over a given calibration period of the dataset. Those fitting parameters are obtained with
    ``xclim.standardized_index_fit_params``.

    Parameters
    ----------
    da : xarray.DataArray
        Input array.
        Resampling and rolling operations should have already been applied using
        ``xclim.indices.preprocess_standardized_index``.
    params : xarray.DataArray
        Fit parameters computed using ``xclim.indices.stats.standardized_index_fit_params``.
    """
    group = params.attrs["group"]

    def reindex_time(da, da_ref):
        if group == "time.dayofyear":
            da = da.rename(day="time").reindex(time=da_ref.time.dt.dayofyear)
            da["time"] = da_ref.time
            da = resample_doy(da, da_ref)
        elif group == "time.month":
            da = da.rename(month="time").reindex(time=da_ref.time.dt.month)
            da["time"] = da_ref.time
        return da if not uses_dask(da) else da.chunk({"time": -1})

    probs_of_zero = da.groupby(group).map(
        lambda x: (x == 0).sum("time") / x.notnull().sum("time")
    )
    params, probs_of_zero = (reindex_time(dax, da) for dax in [params, probs_of_zero])
    dist_probs = dist_method("cdf", params, da)
    probs = probs_of_zero + ((1 - probs_of_zero) * dist_probs)

    params_norm = xr.DataArray(
        [0, 1],
        dims=["dparams"],
        coords=dict(dparams=(["loc", "scale"])),
        attrs=dict(scipy_dist="norm"),
    )
    std_index = dist_method("ppf", params_norm, probs)
    # A cdf value of 0 or 1 gives ±np.inf when inverted to the normal distribution.
    # The neighbouring values 0.00...1 and 0.99...9 with a float64 give a standardized index of ± 8.21
    # We use this index as reference for maximal/minimal standardized values.
    # Smaller values of standardized index could also be used as bounds. For example, [Guttman, 1999]
    # notes that precipitation events (over a month/season) generally occur less than 100 times in the US.
    # Values of standardized index outside the range [-3.09, 3.09] correspond to probabilities smaller than 0.001
    # or greater than 0.999, which could be considered non-statistically relevant for this sample size.
    std_index = std_index.clip(-8.21, 8.21)
    return std_index
