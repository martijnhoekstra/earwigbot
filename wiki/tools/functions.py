# -*- coding: utf-8  -*-

"""
EarwigBot's Wiki Toolset: Misc Functions

This module, a component of the wiki.tools package, contains miscellaneous
functions that are not methods of any class, like get_site().

There's no need to import this module explicitly. All functions here are
automatically available from wiki.tools.
"""

from core import config
from wiki.tools.exceptions import ConfigError, SiteNotFoundError
from wiki.tools.site import Site

__all__ = ["get_site"]

def get_site(name=None, project=None, lang=None):
    """Returns a Site instance based on information from our config file.

    With no arguments, returns the default site as specified by our config
    file. This is default = config.wiki["defaultSite"];
    config.wiki["sites"][default].

    With `name` specified, returns the site specified by
    config.wiki["sites"][name].

    With `project` and `lang` specified, returns the site specified by the
    member of config.wiki["sites"], `s`, for which s["project"] == project and
    s["lang"] == lang.

    Specifying a project without a lang or a lang without a project will raise
    TypeError. If all three args are specified, `name` will be first tried,
    then `project` and `lang`. If, with any number of args, a site cannot be
    found in the config, SiteNotFoundError is raised.
    """
    if config._config is None:
        e = "Config file has not been loaded: use config.verify_config() and then config.parse_config() to do so."
        raise ConfigError(e)

    if (project is None and lang is not None) or (project is not None and lang is None):
        e = "Keyword arguments 'lang' and 'project' must be specified together."
        raise TypeError(e)

    if name is None and project is None:  # no args given (project is None implies lang is None)
        try:  # ...so use the default site
            default = config.wiki["defaultSite"]
        except KeyError:
            e = "Default site is not specified in config."
            raise SiteNotFoundError(e)
        try:
            return config.wiki["sites"][default]
        except KeyError:
            e = "Default site specified by config is not in the config's sites list."
            raise SiteNotFoundError(e)

    if name is not None:  # name arg given, but don't look at others yet
        try:
            return config.wiki["sites"][name]
        except KeyError:
            if project is None:  # implies lang is None, i.e., only name was given
                e = "Site '{0}' not found in config.".format(name)
                raise SiteNotFoundError(e)
            for site in config.wiki["sites"].values():
                if site["project"] == project and site["lang"] == lang:
                    return site
            e = "Neither site '{0}' nor site '{1}:{2}' found in config.".format(name, project, lang)
            raise SiteNotFoundError(e)

    for site in config.wiki["sites"].values():  # implied lang and proj are not None
        if site["project"] == project and site["lang"] == lang:
            return site
    e = "Site '{0}:{1}' not found in config.".format(project, lang)
    raise SiteNotFoundError(e)
