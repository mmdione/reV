# -*- coding: utf-8 -*-
"""
reV analysis configs (generation, lcoe, etc...)

Created on Mon Jan 28 11:43:27 2019

@author: gbuster
"""
import logging
from math import ceil
from warnings import warn
import os

from reV.config.base_analysis_config import AnalysisConfig
from reV.config.sam_config import SAMConfig
from reV.config.curtailment import Curtailment
from reV.config.project_points import PointsControl, ProjectPoints
from reV.utilities.exceptions import ConfigError, ConfigWarning
from reV.pipeline.pipeline import Pipeline


logger = logging.getLogger(__name__)


class SAMAnalysisConfig(AnalysisConfig):
    """SAM-based analysis config (generation, lcoe, etc...)."""

    def __init__(self, config):
        """
        Parameters
        ----------
        config : str | dict
            File path to config json (str), serialized json object (str),
            or dictionary with pre-extracted config.
        """
        self._tech = None
        self._sam_config = None
        self._pc = None
        self._output_request = None
        super().__init__(config)

    @property
    def tech(self):
        """Get the tech property from the config.

        Returns
        -------
        _tech : str
            reV technology string to analyze (e.g. pv, csp, wind, etc...).
        """
        if self._tech is None:
            self._tech = self['project_control']['technology']
            self._tech = self._tech.lower().replace(' ', '')
        return self._tech

    @property
    def sam_config(self):
        """Get the SAM configuration object.

        Returns
        -------
        _sam_gen : reV.config.sam.SAMConfig
            SAM config object. This object emulates a dictionary.
        """
        if self._sam_config is None:
            self._sam_config = SAMConfig(self['sam_files'])
        return self._sam_config

    @property
    def points_control(self):
        """Get the generation points control object.

        Returns
        -------
        _pc : reV.config.project_points.PointsControl
            PointsControl object based on specified project points and
            execution control option.
        """

        if self._pc is None:
            # make an instance of project points
            pp = ProjectPoints(self['project_points'], self['sam_files'],
                               self.tech)

            if (self.execution_control.option == 'peregrine' or
                    self.execution_control.option == 'eagle'):
                # sites per split on peregrine or eagle is the number of sites
                # in project points / number of nodes. This is for the initial
                # division of the project sites between HPC nodes (jobs)
                sites_per_split = ceil(len(pp) / self.execution_control.nodes)

            elif self.execution_control.option == 'local':
                # sites per split on local is number of sites / # of processes
                sites_per_split = ceil(len(pp) / self.execution_control.ppn)

            # make an instance of points control and set to protected attribute
            self._pc = PointsControl(pp, sites_per_split=sites_per_split)

        return self._pc

    @property
    def output_request(self):
        """Get the list of requested output variables.

        Returns
        -------
        _output_request : list
            List of requested reV output variables corresponding to SAM
            variable names.
        """

        # map of commonly expected typos
        corrections = {'cf_means': 'cf_mean',
                       'cf': 'cf_mean',
                       'capacity_factor': 'cf_mean',
                       'capacityfactor': 'cf_mean',
                       'cf_profiles': 'cf_profile',
                       'profiles': 'cf_profile',
                       'profile': 'cf_profile',
                       'generation': 'annual_energy',
                       'yield': 'energy_yield',
                       'generation_profile': 'gen_profile',
                       'generation_profiles': 'gen_profile',
                       'plane_of_array': 'poa',
                       'plane_of_array_irradiance': 'poa',
                       'gen_profiles': 'gen_profile',
                       'lcoe': 'lcoe_fcr',
                       'lcoe_nominal': 'lcoe_nom',
                       'real_lcoe': 'lcoe_real',
                       'net_present_value': 'npv',
                       'ppa': 'ppa_price',
                       'single_owner': 'ppa_price',
                       'singleowner': 'ppa_price',
                       }

        if self._output_request is None:
            self._output_request = []
            # default output request if not specified
            temp = ['cf_mean']
            if 'output_request' in self['project_control']:
                temp = self['project_control']['output_request']

            if isinstance(temp, str):
                temp = [temp]

            for request in temp:
                if request in corrections.values():
                    self._output_request.append(request)
                elif request in corrections.keys():
                    self._output_request.append(corrections[request])
                    warn('Correcting output request "{}" to "{}".'
                         .format(request, corrections[request]), ConfigWarning)
                else:
                    self._output_request.append(request)
                    warn('Did not recognize requested output variable "{}". '
                         'Passing forward, but this may cause a downstream '
                         'error. Available known output variables are: {}'
                         .format(request, list(set(corrections.values()))),
                         ConfigWarning)

        return self._output_request


class GenConfig(SAMAnalysisConfig):
    """Class to import and manage user configuration inputs."""

    NAME = 'gen'

    def __init__(self, config):
        """
        Parameters
        ----------
        config : str | dict
            File path to config json (str), serialized json object (str),
            or dictionary with pre-extracted config.
        """
        self._curtailment = None
        self._downscale = None
        self._res_files = None
        super().__init__(config)

    @property
    def curtailment(self):
        """Get the curtailment config object that the gen config points to.

        Returns
        -------
        _curtailment : NoneType | reV.config.curtailment.Curtailment
            Returns None if no curtailment config is specified. If one is
            specified, this returns the reV curtailment config object.
        """
        if self._curtailment is None:
            if 'curtailment' in self:
                if self['curtailment']:
                    # curtailment was specified and is not None or False
                    self._curtailment = Curtailment(self['curtailment'])

        return self._curtailment

    @property
    def downscale(self):
        """Get the resource downscale request (nsrdb only!).

        Returns
        -------
        _downscale : NoneType | str
            Returns None if no downscaling is requested. Otherwise, expects a
            downscale variable in the project_control section in the Pandas
            frequency format, e.g. '5min'.
        """

        if self._downscale is None:
            if 'downscale' in self['project_control']:
                if self['project_control']['downscale']:
                    # downscaling was requested and is not None or False
                    self._downscale = str(self['project_control']['downscale'])
        return self._downscale

    @property
    def res_files(self):
        """Get a list of the resource files with years filled in.

        Returns
        -------
        _res_files : list
            List of config-specified resource files. Resource files with {}
            formatting will be filled with the specified year(s). This return
            value is a list with len=1 for a single year run.
        """
        if self._res_files is None:
            # get base filename, may have {} for year format
            fname = self['resource_file']
            if '{}' in fname:
                # need to make list of res files for each year
                self._res_files = [fname.format(year) for year in self.years]
            else:
                # only one resource file request, still put in list
                self._res_files = [fname]
        self.check_files(self._res_files)
        if len(self._res_files) != len(self.years):
            raise ConfigError('The number of resource files does not match '
                              'the number of analysis years!'
                              '\n\tResource files: \n\t\t{}'
                              '\n\tYears: \n\t\t{}'
                              .format(self._res_files, self.years))
        return self._res_files


class ExclConfig(AnalysisConfig):
    """Class to import and manage user exclusion configuration inputs."""

    def __init__(self, config):
        """
        Parameters
        ----------
        config : str | dict
            File path to config json (str), serialized json object (str),
            or dictionary with pre-extracted config.
        """
        self._exclusions = None
        self._fout = None
        self._use_blocks = False
        self._filter = None

        super().__init__(config)

    @property
    def exclusions(self):
        """Get the exclusions layer config object.

        Returns
        -------
        _exclusions : NoneType | dict list
            Returns None if no exclusions config is specified. If one is
            specified, this returns the reV exclusions config object.
        """
        if self._exclusions is None:
            if 'exclusions' in self:
                self._exclusions = self['exclusions']

        return self._exclusions

    @property
    def fout(self):
        """Get the exclusions output filename.

        Returns
        -------
        _fout : NoneType | dict list
            Returns None if no fout is specified. If one is
            specified, this returns the reV fout object.
        """
        if self._fout is None:
            if 'fout' in self:
                fout = os.path.join(self.dirout,
                                    self['fout'])
            else:
                fout = os.path.join(self.dirout,
                                    'exclusions.tif')
            self._fout = fout
        return self._fout

    @property
    def use_blocks(self):
        """Get the use_blocks config object.

        Returns
        -------
        _use_blocks : NoneType | dict list
            Returns None if no use_blocks config is specified.
            If one is specified, this returns the reV use_blocks
            config object.
        """
        if self._use_blocks is False:
            if 'use_blocks' in self:
                use_blocks = bool(self['use_blocks'])
                self._use_blocks = use_blocks

        return self._use_blocks

    @property
    def filter(self):
        """Get filter config object that the gen config points to.

        Returns
        -------
        _filter : NoneType | dict list
            Returns None if no filter config is specified. If one is
            specified, this returns the reV filter config object.
        """
        if self._filter is None:
            if 'filter' in self:
                self._filter = self['filter']

        return self._filter


class EconConfig(SAMAnalysisConfig):
    """Class to import and manage configuration inputs for econ analysis."""

    NAME = 'econ'

    def __init__(self, config):
        """
        Parameters
        ----------
        config : str | dict
            File path to config json (str), serialized json object (str),
            or dictionary with pre-extracted config.
        """
        self._cf_files = None
        self._site_data = None
        super().__init__(config)

    @property
    def cf_files(self):
        """Get the capacity factor files (reV generation output data).

        Returns
        -------
        _cf_files : list
            Target paths for capacity factor files (reV generation output
            data) for input to reV LCOE calculation.
        """

        if self._cf_files is None:
            # get base filename, may have {} for year format
            fname = self['cf_file']
            if '{}' in fname:
                # need to make list of res files for each year
                self._cf_files = [fname.format(year) for year in self.years]
            elif 'PIPELINE' in fname:
                self._cf_files = Pipeline.parse_previous(self.dirout,
                                                         'econ',
                                                         target='fpath')
            else:
                # only one resource file request, still put in list
                self._cf_files = [fname]

            self.check_files(self._cf_files)

            # check year/cf_file matching if not a pipeline input
            if 'PIPELINE' not in fname:
                if len(self._cf_files) != len(self.years):
                    raise ConfigError('The number of cf files does not match '
                                      'the number of analysis years!'
                                      '\n\tCF files: \n\t\t{}'
                                      '\n\tYears: \n\t\t{}'
                                      .format(self._cf_files, self.years))
                for year in self.years:
                    if year not in str(self._cf_files):
                        raise ConfigError('Could not find year {} in cf '
                                          'files: {}'
                                          .format(year, self._cf_files))

        return self._cf_files

    @property
    def site_data(self):
        """Get the site-specific data file.

        Returns
        -------
        _site_data : str | NoneType
            Target path for site-specific data file.
        """
        if self._site_data is None:
            self._site_data = None
            if 'site_data' in self:
                self._site_data = self['site_data']
        return self._site_data
