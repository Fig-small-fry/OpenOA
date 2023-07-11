from __future__ import annotations

import json
import itertools
from copy import deepcopy
from pathlib import Path

import yaml
import attrs
import numpy as np
from attrs import field, define


# *************************************************************************
# Define the analysis requirements for ease of findability and modification
# *************************************************************************


# Datetime frequency checks
_at_least_monthly = ("M", "MS", "W", "D", "H", "T", "min", "S", "L", "ms", "U", "us", "N")
_at_least_daily = ("D", "H", "T", "min", "S", "L", "ms", "U", "us", "N")
_at_least_hourly = ("H", "T", "min", "S", "L", "ms", "U", "us", "N")

ANALYSIS_REQUIREMENTS = {
    "MonteCarloAEP": {
        "meter": {
            "columns": ["MMTR_SupWh"],
            "freq": _at_least_monthly,
        },
        "curtail": {
            "columns": ["IAVL_DnWh", "IAVL_ExtPwrDnWh"],
            "freq": _at_least_monthly,
        },
        "reanalysis": {
            "columns": ["WMETR_HorWdSpd", "WMETR_AirDen"],
            "conditional_columns": {
                "reg_temperature": ["WMETR_EnvTmp"],
                "reg_wind_direction": ["WMETR_HorWdSpdU", "WMETR_HorWdSpdV"],
            },
            "freq": _at_least_monthly,
        },
    },
    "TurbineLongTermGrossEnergy": {
        "scada": {
            "columns": ["asset_id", "WMET_HorWdSpd", "WTUR_W"],
            "freq": _at_least_daily,
        },
        "reanalysis": {
            "columns": ["WMETR_HorWdSpd", "WMETR_HorWdDir", "WMETR_AirDen"],
            "freq": _at_least_daily,
        },
    },
    "ElectricalLosses": {
        "scada": {
            "columns": ["asset_id", "WTUR_W"],
            "freq": _at_least_daily,
        },
        "meter": {
            "columns": ["MMTR_SupWh"],
            "freq": _at_least_monthly,
        },
    },
    "WakeLosses": {
        "scada": {
            "columns": ["asset_id", "WMET_HorWdSpd", "WMET_HorWdDir", "WTUR_W"],
            "freq": _at_least_hourly,
        },
        "reanalysis": {
            "columns": ["WMETR_HorWdSpd", "WMETR_HorWdDir"],
            "freq": _at_least_hourly,
        },
    },
}


def determine_analysis_requirements(
    which: str, analysis_type: str | list[str]
) -> dict | tuple[dict, dict]:
    """Determines the column, frequency, or both requirements for each type of data, such as SCADA,
    depending on the analysis type(s) provided.

    Args:
        which (str): One of "columns", "frequency", or "both".
        analysis_type (str | list[str]): The analysis type(s) determine the bare minimum requirements
            for each type of data.

    Raises:
        ValueError: Raised if :py:attr:`which` is not one of "columns", "frequency", or "both".

    Returns:
        dict | tuple[dict, dict]: The dictionary of column or frequency requirements, or if "both", then a tuple
            of each dictionary.
    """
    if isinstance(analysis_type, str):
        analysis_type = [analysis_type]
    requirements = {key: ANALYSIS_REQUIREMENTS[key] for key in analysis_type}
    if which in ("columns", "both"):
        categories = ("scada", "meter", "tower", "curtail", "reanalysis", "asset")
        column_requirements = {
            cat: set(
                itertools.chain(*[r.get(cat, {}).get("columns", []) for r in requirements.values()])
            )
            for cat in categories
        }
        column_requirements = {k: v for k, v in column_requirements.items() if v != set()}
    if which in ("frequency", "both"):
        frequency = {
            key: {name: value["freq"] for name, value in values.items()}
            for key, values in requirements.items()
        }
        frequency_requirements = {
            k: []
            for k in set(itertools.chain.from_iterable([[*val] for val in frequency.values()]))
        }
        for vals in frequency.values():
            for name, req in vals.items():
                reqs = frequency_requirements[name]
                if reqs == []:
                    frequency_requirements[name] = set(req)
                else:
                    frequency_requirements[name] = reqs.intersection(req)
    if which == "both":
        return column_requirements, frequency_requirements
    elif which == "columns":
        return column_requirements
    elif which == "frequency":
        return frequency_requirements
    raise ValueError("`which` must be one of 'columns', 'frequency', or 'both'.")


@define(auto_attribs=True)
class FromDictMixin:
    """A Mixin class to allow for kwargs overloading when a data class doesn't
    have a specific parameter definied. This allows passing of larger dictionaries
    to a data class without throwing an error.

    Raises
    ------
    AttributeError
        Raised if the required class inputs are not provided.
    """

    @classmethod
    def from_dict(cls, data: dict):
        """Maps a data dictionary to an `attrs`-defined class.
        TODO: Add an error to ensure that either none or all the parameters are passed in
        Args:
            data : dict
                The data dictionary to be mapped.
        Returns:
            cls
                The `attrs`-defined class.
        """
        # Get all parameters from the input dictionary that map to the class initialization
        kwargs = {
            a.name: data[a.name]
            for a in cls.__attrs_attrs__  # type: ignore
            if a.name in data and a.init
        }

        # Map the inputs must be provided: 1) must be initialized, 2) no default value defined
        required_inputs = [
            a.name
            for a in cls.__attrs_attrs__  # type: ignore
            if a.init and isinstance(a.default, type(attrs.NOTHING))  # type: ignore
        ]
        undefined = sorted(set(required_inputs) - set(kwargs))
        if undefined:
            raise AttributeError(
                f"The class defintion for {cls.__name__} is missing the following inputs: {undefined}"
            )
        return cls(**kwargs)  # type: ignore


# ***************************************
# Define the meta data validation classes
# ***************************************


@define(auto_attribs=True)
class SCADAMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about the SCADA data, that will contribute to a larger
    plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the SCADA data, by default "time". This data should be of
            type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex. Additional
            columns describing the datetime stamps are: `frequency`
        asset_id (str): The turbine identifier column in the SCADA data, by default "asset_id". This data should be of
            type: `str`.
        WTUR_W (str): The power produced, in kW, column in the SCADA data, by default "WTUR_W".
            This data should be of type: `float`.
        WMET_HorWdSpd (str): The measured windspeed, in m/s, column in the SCADA data, by default "WMET_HorWdSpd".
            This data should be of type: `float`.
        WMET_HorWdDir (str): The measured wind direction, in degrees, column in the SCADA data, by default
            "WMET_HorWdDir". This data should be of type: `float`.
        WTUR_TurSt (str): The status code column in the SCADA data, by default "WTUR_TurSt". This data
            should be of type: `str`.
        WROT_BlPthAngVal (str): The pitch, in degrees, column in the SCADA data, by default "WROT_BlPthAngVal". This data
            should be of type: `float`.
        WMET_EnvTmp (str): The temperature column in the SCADA data, by default "WMET_EnvTmp". This
            data should be of type: `float`.
        frequency (str): The frequency of `time` in the SCADA data, by default "10T". The input
            should align with the `Pandas frequency offset aliases`_.


    .. _Pandas frequency offset aliases:
       https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    """

    # DataFrame columns
    time: str = field(default="time")
    asset_id: str = field(default="asset_id")
    WTUR_W: str = field(default="WTUR_W")
    WMET_HorWdSpd: str = field(default="WMET_HorWdSpd")
    WMET_HorWdDir: str = field(default="WMET_HorWdDir")
    WTUR_TurSt: str = field(default="WTUR_TurSt")
    WROT_BlPthAngVal: str = field(default="WROT_BlPthAngVal")
    WMET_EnvTmp: str = field(default="WMET_EnvTmp")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="scada", init=False)
    WTUR_SupWh: str = field(default="WTUR_SupWh", init=False)  # calculated in PlantData
    col_map: dict = field(init=False)
    col_map_reversed: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            asset_id=str,
            WTUR_W=float,
            WMET_HorWdSpd=float,
            WMET_HorWdDir=float,
            WTUR_TurSt=str,
            WROT_BlPthAngVal=float,
            WMET_EnvTmp=float,
            WTUR_SupWh=float,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            asset_id=None,
            WTUR_W="kW",
            WMET_HorWdSpd="m/s",
            WMET_HorWdDir="deg",
            WTUR_TurSt=None,
            WROT_BlPthAngVal="deg",
            WMET_EnvTmp="C",
            WTUR_SupWh="kWh",
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            asset_id=self.asset_id,
            WTUR_W=self.WTUR_W,
            WMET_HorWdSpd=self.WMET_HorWdSpd,
            WMET_HorWdDir=self.WMET_HorWdDir,
            WTUR_TurSt=self.WTUR_TurSt,
            WROT_BlPthAngVal=self.WROT_BlPthAngVal,
            WMET_EnvTmp=self.WMET_EnvTmp,
            WTUR_SupWh=self.WTUR_SupWh,
        )
        self.col_map_reversed = {v: k for k, v in self.col_map.items()}


@define(auto_attribs=True)
class MeterMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about energy meter data, that will contribute to a larger
    plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the meter data, by default "time". This data should
            be of type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex.
            Additional columns describing the datetime stamps are: `frequency`
        MMTR_SupWh (str): The energy produced, in kWh, column in the meter data, by default
            "MMTR_SupWh". This data should be of type: `float`.
        frequency (str): The frequency of `time` in the meter data, by default "10T". The input
            should align with the `Pandas frequency offset aliases`_.


    .. _Pandas frequency offset aliases:
       https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    """

    # DataFrame columns
    time: str = field(default="time")
    MMTR_SupWh: str = field(default="MMTR_SupWh")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="meter", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            MMTR_SupWh=float,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            MMTR_SupWh="kWh",
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            MMTR_SupWh=self.MMTR_SupWh,
        )


@define(auto_attribs=True)
class TowerMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about meteorological tower (met tower) data, that will contribute to a
    larger plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the met tower data, by default "time". This data should
            be of type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex.
            Additional columns describing the datetime stamps are: `frequency`
        asset_id (str): The met tower identifier column in the met tower data, by default "asset_id". This data
            should be of type: `str`.
        frequency (str): The frequency of `time` in the met tower data, by default "10T". The input
            should align with the `Pandas frequency offset aliases`_.

    .. _Pandas frequency offset aliases:
       https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    """

    # DataFrame columns
    time: str = field(default="time")
    asset_id: str = field(default="asset_id")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="tower", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            asset_id=str,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            asset_id=None,
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            asset_id=self.asset_id,
        )


@define(auto_attribs=True)
class StatusMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about the turbine status log data, that will contribute to a
    larger plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the status data, by default "time". This data should
            be of type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex.
            Additional columns describing the datetime stamps are: `frequency`
        asset_id (str): The turbine identifier column in the status data, by default "asset_id". This data
            should be of type: `str`.
        status_id (str): The status code identifier column in the status data, by default "asset_id". This data
            should be of type: `str`.
        status_code (str): The status code column in the status data, by default "asset_id". This data
            should be of type: `str`.
        status_text (str): The status text description column in the status data, by default "asset_id".
            This data should be of type: `str`.
        frequency (str): The frequency of `time` in the met tower data, by default "10T". The input
            should align with the `Pandas frequency offset aliases`_.

    .. _Pandas frequency offset aliases:
       https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    """

    # DataFrame columns
    time: str = field(default="time")
    asset_id: str = field(default="asset_id")
    status_id: str = field(default="status_id")
    status_code: str = field(default="status_code")
    status_text: str = field(default="status_text")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="status", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            asset_id=str,
            status_id=np.int64,
            status_code=np.int64,
            status_text=str,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            asset_id=None,
            status_id=None,
            status_code=None,
            status_text=None,
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            asset_id=self.asset_id,
            status_id=self.status_id,
            status_code=self.status_code,
            status_text=self.status_text,
        )


@define(auto_attribs=True)
class CurtailMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about the plant curtailment data, that will contribute to a
    larger plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the curtailment data, by default "time". This data should
            be of type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex.
            Additional columns describing the datetime stamps are: `frequency`
        IAVL_ExtPwrDnWh (str): The curtailment, in kWh, column in the curtailment data, by default
            "IAVL_ExtPwrDnWh". This data should be of type: `float`.
        IAVL_DnWh (str): The availability, in kWh, column in the curtailment data, by default
            "IAVL_DnWh". This data should be of type: `float`.
        frequency (str): The frequency of `time` in the met tower data, by default "10T". The input
            should align with the `Pandas frequency offset aliases`_.

    .. _Pandas frequency offset aliases:
       https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    """

    # DataFrame columns
    time: str = field(default="time")
    IAVL_ExtPwrDnWh: str = field(default="IAVL_ExtPwrDnWh")
    IAVL_DnWh: str = field(default="IAVL_DnWh")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="curtail", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            IAVL_ExtPwrDnWh=float,
            IAVL_DnWh=float,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            IAVL_ExtPwrDnWh="kWh",
            IAVL_DnWh="kWh",
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            IAVL_ExtPwrDnWh=self.IAVL_ExtPwrDnWh,
            IAVL_DnWh=self.IAVL_DnWh,
        )


@define(auto_attribs=True)
class AssetMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic to create the necessary column mappings and other validation
    components, or other data about the site's asset metadata, that will contribute to a
    larger plant metadata schema/routine.

    Args:
        asset_id (str): The asset identifier column in the asset metadata, by default "asset_id"
            This data should be of type: `str`.
        latitude (str): The asset's latitudinal position, in WGS84, column in the asset metadata, by
            default "latitude". This data should be of type: `float`.
        longitude (str): The asset's longitudinal position, in WGS84, column in the asset metadata,
            by default "longitude". This data should be of type: `float`.
        rated_power (str): The asset's rated power, in kW, column in the asset metadata, by default
            "rated_power". This data should be of type: `float`.
        hub_height (str): The asset's hub height, in m, column in the asset metadata, by default
            "hub_height". This data should be of type: `float`.
        elevation (str): The asset's elevation above sea level, in m, column in the asset metadata,
            by default "elevation". This data should be of type: `float`.
        type (str): The type of asset column in the asset metadata, by default "type". This data
            should be of type: `str`.
    """

    # DataFrame columns
    asset_id: str = field(default="asset_id")
    latitude: str = field(default="latitude")
    longitude: str = field(default="longitude")
    rated_power: str = field(default="rated_power")
    hub_height: str = field(default="hub_height")
    rotor_diameter: str = field(default="rotor_diameter")
    elevation: str = field(default="elevation")
    type: str = field(default="type")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="asset", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            asset_id=str,
            latitude=float,
            longitude=float,
            rated_power=float,
            hub_height=float,
            rotor_diameter=float,
            elevation=float,
            type=str,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            asset_id=None,
            latitude="WGS84",
            longitude="WGS84",
            rated_power="kW",
            hub_height="m",
            rotor_diameter="m",
            elevation="m",
            type=None,
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            asset_id=self.asset_id,
            latitude=self.latitude,
            longitude=self.longitude,
            rated_power=self.rated_power,
            hub_height=self.hub_height,
            rotor_diameter=self.rotor_diameter,
            elevation=self.elevation,
            type=self.type,
        )


def convert_reanalysis(value: dict[str, dict]):
    return {k: ReanalysisMetaData.from_dict(v) for k, v in value.items()}


@define(auto_attribs=True)
class ReanalysisMetaData(FromDictMixin):  # noqa: F821
    """A metadata schematic for each of the reanalsis products to be used for operationa analyses
    to create the necessary column mappings and other validation components, or other data about
    the site's asset metadata, that will contribute to a larger plant metadata schema/routine.

    Args:
        time (str): The datetime stamp for the curtailment data, by default "time". This data should
            be of type: `np.datetime64[ns]`, or able to be converted to a pandas DatetimeIndex.
            Additional columns describing the datetime stamps are: `frequency`
        WMETR_HorWdSpd (:obj:`str`): The reanalysis non-directional windspeed data column name, in
            m/s, by default "WMETR_HorWdSpd".
        WMETR_HorWdSpdU (:obj:`str`): The reanalysis u-direction windspeed data column name, in m/s,
            by default "WMETR_HorWdSpdU".
        WMETR_HorWdSpdV (:obj:`str`): The reanalysis v-directional windspeed data column name, in
            m/s, by default "WMETR_HorWdSpdV".
        WMETR_HorWdDir (:obj:`str`): The reanalysis windspeed horizontal direction data column name,
            in degrees, by default "WMETR_HorWdDir".
        WMETR_EnvTmp (:obj:`str`): The temperature data column name in the renalysis data, in
            degrees Kelvin, by default "WMETR_EnvTmp".
        WMETR_AirDen (:obj:`str`): The air density reanalysis data column name, in kg/m^3, by
            default "WMETR_AirDen".
        WMETR_EnvPres (:obj:`str`): The surface air pressure reanalysis data column name, in Pa, by
            default "WMETR_EnvPres".
        frequency (:obj:`str`): The frequency of the timestamps in the :py:attr:`time` column, by
            default "10T".
    """

    time: str = field(default="time")
    WMETR_HorWdSpd: str = field(default="WMETR_HorWdSpd")
    WMETR_HorWdSpdU: str = field(default="WMETR_HorWdSpdU")
    WMETR_HorWdSpdV: str = field(default="WMETR_HorWdSpdV")
    WMETR_HorWdDir: str = field(default="WMETR_HorWdDir")
    WMETR_EnvTmp: str = field(default="WMETR_EnvTmp")
    WMETR_AirDen: str = field(default="WMETR_AirDen")
    WMETR_EnvPres: str = field(default="surface_pressure")

    # Data about the columns
    frequency: str = field(default="10T")

    # Parameterizations that should not be changed
    # Prescribed mappings, datatypes, and units for in-code reference.
    name: str = field(default="reanalysis", init=False)
    col_map: dict = field(init=False)
    dtypes: dict = field(
        default=dict(
            time=np.datetime64,
            WMETR_HorWdSpd=float,
            WMETR_HorWdSpdU=float,
            WMETR_HorWdSpdV=float,
            WMETR_HorWdDir=float,
            WMETR_EnvTmp=float,
            WMETR_AirDen=float,
            WMETR_EnvPres=float,
        ),
        init=False,  # don't allow for user input
    )
    units: dict = field(
        default=dict(
            time="datetim64[ns]",
            WMETR_HorWdSpd="m/s",
            WMETR_HorWdSpdU="m/s",
            WMETR_HorWdSpdV="m/s",
            WMETR_HorWdDir="deg",
            WMETR_EnvTmp="K",
            WMETR_AirDen="kg/m^3",
            WMETR_EnvPres="Pa",
        ),
        init=False,  # don't allow for user input
    )

    def __attrs_post_init__(self) -> None:
        self.col_map = dict(
            time=self.time,
            WMETR_HorWdSpd=self.WMETR_HorWdSpd,
            WMETR_HorWdSpdU=self.WMETR_HorWdSpdU,
            WMETR_HorWdSpdV=self.WMETR_HorWdSpdV,
            WMETR_HorWdDir=self.WMETR_HorWdDir,
            WMETR_EnvTmp=self.WMETR_EnvTmp,
            WMETR_AirDen=self.WMETR_AirDen,
            WMETR_EnvPres=self.WMETR_EnvPres,
        )


@define(auto_attribs=True)
class PlantMetaData(FromDictMixin):  # noqa: F821
    """Composese the metadata/validation requirements from each of the individual data
    types that can compose a `PlantData` object.

    Args:
        latitude (`float`): The wind power plant's center point latitude.
        longitude (`float`): The wind power plant's center point longitude.
        capacity (`float`): The capacity of the plant in MW
        scada (`SCADAMetaData`): A dictionary containing the `SCADAMetaData`
            column mapping and frequency parameters. See `SCADAMetaData` for more details.
        meter (`MeterMetaData`): A dictionary containing the `MeterMetaData`
            column mapping and frequency parameters. See `MeterMetaData` for more details.
        tower (`TowerMetaData`): A dictionary containing the `TowerMetaData`
            column mapping and frequency parameters. See `TowerMetaData` for more details.
        status (`StatusMetaData`): A dictionary containing the `StatusMetaData`
            column mapping parameters. See `StatusMetaData` for more details.
        curtail (`CurtailMetaData`): A dictionary containing the `CurtailMetaData`
            column mapping and frequency parameters. See `CurtailMetaData` for more details.
        asset (`AssetMetaData`): A dictionary containing the `AssetMetaData`
            column mapping parameters. See `AssetMetaData` for more details.
        reanalysis (`dict[str, ReanalysisMetaData]`): A dictionary containing the
            reanalysis type (as keys, such as "era5" or "merra2") and `ReanalysisMetaData`
            column mapping and frequency parameters for each type of reanalysis data
            provided. See `ReanalysisMetaData` for more details.
    """

    latitude: float = field(default=0, converter=float)
    longitude: float = field(default=0, converter=float)
    capacity: float = field(default=0, converter=float)
    scada: SCADAMetaData = field(default={}, converter=SCADAMetaData.from_dict)
    meter: MeterMetaData = field(default={}, converter=MeterMetaData.from_dict)
    tower: TowerMetaData = field(default={}, converter=TowerMetaData.from_dict)
    status: StatusMetaData = field(default={}, converter=StatusMetaData.from_dict)
    curtail: CurtailMetaData = field(default={}, converter=CurtailMetaData.from_dict)
    asset: AssetMetaData = field(default={}, converter=AssetMetaData.from_dict)
    reanalysis: dict[str, ReanalysisMetaData] = field(
        default={"product": {}}, converter=convert_reanalysis  # noqa: F821
    )  # noqa: F821

    @property
    def column_map(self) -> dict[str, dict]:
        """Provides the column mapping for all of the available data types with
        the name of each data type as the key and the dictionary mapping as the values.
        """
        values = dict(
            scada=self.scada.col_map,
            meter=self.meter.col_map,
            tower=self.tower.col_map,
            status=self.status.col_map,
            asset=self.asset.col_map,
            curtail=self.curtail.col_map,
            reanalysis={},
        )
        if self.reanalysis != {}:
            values["reanalysis"] = {k: v.col_map for k, v in self.reanalysis.items()}
        return values

    @property
    def dtype_map(self) -> dict[str, dict]:
        """Provides the column dtype matching for all of the available data types with
        the name of each data type as the keys, and the column dtype mapping as values.
        """
        types = dict(
            scada=self.scada.dtypes,
            meter=self.meter.dtypes,
            tower=self.tower.dtypes,
            status=self.status.dtypes,
            asset=self.asset.dtypes,
            curtail=self.curtail.dtypes,
            reanalysis={},
        )
        if self.reanalysis != {}:
            types["reanalysis"] = {k: v.dtypes for k, v in self.reanalysis.items()}
        return types

    @property
    def coordinates(self) -> tuple[float, float]:
        """Returns the latitude, longitude pair for the wind power plant.

        Returns:
            tuple[float, float]: The (latitude, longitude) pair
        """
        return self.latitude, self.longitude

    @classmethod
    def from_json(cls, metadata_file: str | Path) -> PlantMetaData:
        """Loads the metadata from a JSON file.

        Args:
            metadata_file (`str | Path`): The full path and file name of the JSON file.

        Raises:
            FileExistsError: Raised if the file doesn't exist at the provided location.

        Returns:
            PlantMetaData
        """
        metadata_file = Path(metadata_file).resolve()
        if not metadata_file.is_file():
            raise FileExistsError(f"Input JSON file: {metadata_file} is an invalid input.")

        with open(metadata_file) as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_yaml(cls, metadata_file: str | Path) -> PlantMetaData:
        """Loads the metadata from a YAML file with a PyYAML encoding.

        Args:
            metadata_file (`str | Path`): The full path and file name of the YAML file.

        Raises:
            FileExistsError: Raised if the file doesn't exist at the provided location.

        Returns:
            PlantMetaData
        """
        metadata_file = Path(metadata_file).resolve()
        if not metadata_file.is_file():
            raise FileExistsError(f"Input YAML file: {metadata_file} is an invalid input.")

        with open(metadata_file) as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def load(cls, data: str | Path | dict | PlantMetaData) -> PlantMetaData:
        """Loads the metadata from either a dictionary or file such as a JSON or YAML file.

        Args:
            metadata_file (`str | Path | dict`): Either a pre-loaded dictionary or
                the full path and file name of the JSON or YAML file.

        Raises:
            ValueError: Raised if the file name doesn't reflect a JSON or YAML encoding.
            ValueError: Raised if the data provided isn't of the correct data type.

        Returns:
            PlantMetaData
        """
        if isinstance(data, PlantMetaData):
            return data

        if isinstance(data, str):
            data = Path(data).resolve()

        if isinstance(data, Path):
            if data.suffix == ".json":
                return cls.from_json(data)
            elif data.suffix in (".yaml", ".yml"):
                return cls.from_yaml(data)
            else:
                raise ValueError("Bad input file extension, must be one of: .json, .yml, or .yaml")

        if isinstance(data, dict):
            return cls.from_dict(data)

        raise ValueError("PlantMetaData can only be loaded from str, Path, or dict objects.")

    def frequency_requirements(self, analysis_types: list[str | None]) -> dict[str, set[str]]:
        """Creates a frequency requirements dictionary for each data type with the name
        as the key and a set of valid frequency fields as the values.

        Args:
            analysis_types (list[str  |  None]): The analyses the data is intended to be
                used for, which will determine what data need to be checked.

        Returns:
            dict[str, set[str]]: The dictionary of data type name and valid frequencies
                for the datetime stamps.
        """
        if "all" in analysis_types:
            requirements = deepcopy(ANALYSIS_REQUIREMENTS)
        else:
            requirements = {
                key: ANALYSIS_REQUIREMENTS[key] for key in analysis_types if key is not None
            }
        frequency_requirements = {
            key: {name: value["freq"] for name, value in values.items()}
            for key, values in requirements.items()
        }
        frequency = {
            k: []
            for k in set(
                itertools.chain.from_iterable([[*val] for val in frequency_requirements.values()])
            )
        }
        for vals in frequency_requirements.values():
            for name, req in vals.items():
                reqs = frequency[name]
                if reqs == []:
                    frequency[name] = set(req)
                else:
                    frequency[name] = reqs.intersection(req)
        return frequency