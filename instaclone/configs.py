"""
Instaclone configuration handling.
"""

__author__ = 'jlevy'

import logging as log
import os
import re
import sys
from collections import namedtuple, OrderedDict

from enum import Enum  # enum34
import yaml  # PyYAML

from log_calls import log_calls

import utils

_required_fields = "local_path remote_path remote_prefix copy_type upload_command download_command"
_other_fields = "version version_hashable version_command"

ConfigBase = namedtuple("ConfigBase", _other_fields + " " + _required_fields)

CONFIGS_REQUIRED = _required_fields.split()

CONFIG_VERSION_RE = re.compile("^[\\w.-]+$")

def _stringify_config_field(value):
  return value.name if isinstance(value, Enum) else str(value)


class Config(ConfigBase):
  """Configuration for a single item."""

  def as_string_dict(self):
    d = dict(self._asdict())
    return {k: _stringify_config_field(v) for (k, v) in d.iteritems() if v is not None}


CONFIG_NAME = "instaclone"
CONFIG_FILE_SEARCH_ORDER = [".", "$INSTACLONE_DIR", "$HOME/.instaclone"]
CONFIG_DIR_SEARCH_ORDER = ["$INSTACLONE_DIR", "$HOME/.instaclone"]
CONFIG_DIR_DEFAULT = "$HOME/.instaclone"

DEFAULT_ITEM_NAME = "default"


class ConfigError(RuntimeError):
  pass


CopyType = Enum("CopyType", "copy symlink hardlink")


def _locate_config_dir(search_dirs):
  """Look in common locations for config directory."""
  tried = []
  for path in search_dirs:
    log.debug("searching for config dir: %s", path)
    try:
      path = utils.shell_expand_variables(path, os.environ)
    except ValueError:
      continue
    tried.append("no config dir found in: %s" % ", ".join(tried))
    if os.path.isdir(path):
      log.debug("using config dir: %s", path)
      return path
  raise ConfigError("no config file found in: %s" % ", ".join(tried))


def _locate_config_file(search_dirs):
  """Look in common locations for config file."""
  tried = []
  for base in search_dirs:
    try:
      base_expanded = utils.shell_expand_variables(base, os.environ)
    except ValueError:
      continue
    for path in [os.path.join(base_expanded, CONFIG_NAME + suffix) for suffix in ".yml", ".json"]:
      log.debug("searching for config file: %s", path)
      tried.append(path)
      if os.path.isfile(path):
        log.debug("using config file: %s", path)
        return path
  raise ConfigError("no config file found in: %s" % ", ".join(tried))


@log_calls
def _load_raw_configs(override_path, search_dirs):
  if override_path:
    path = override_path
  else:
    path = _locate_config_file(search_dirs)
    if not path:
      raise ConfigError("could not locate config file (be sure a config is in working dir or home folder)")

  with open(path) as f:
    parsed_configs = yaml.safe_load(f)

  out = []
  try:
    items = parsed_configs["items"]
    for config_dict in items:
      # TODO: Could overlay global configs and local ones here.
      combined = {key: None for key in Config._fields}
      combined.update(config_dict)

      try:
        out.append(combined)
      except TypeError as e:
        raise ConfigError("error in config value: %s: %s" % (e, config_dict))
  except ValueError as e:
    raise ConfigError("error reading config file: %s" % e)

  return out


def _parse_validate(raw_config_list):
  for raw in raw_config_list:
    # Validation.
    for key in CONFIGS_REQUIRED:
      if key not in raw or raw[key] is None:
        raise ConfigError("must specify '%s' in item config: %s" % (key, raw))

    if "version" in raw and not CONFIG_VERSION_RE.match(str(raw["version"])):
      raise ConfigError("invalid version string: '%s'" % raw["version"])
    if "version" not in raw and "version_hashable" not in raw and "version_command" not in raw:
      raise ConfigError("must specify 'version', 'version_hashable', or 'version_command' in item config: %s" % raw)

    # Validate shell templates.
    for key in "upload_command", "download_command":
      try:
        utils.shell_expand_to_popen(raw[key], {"REMOTE": "dummy", "LOCAL": "dummy"})
      except ValueError as e:
        raise ConfigError("invalid command in config: %s: %s" % (e, raw[key]))

    # Normalize.
    for path in "local_path", "remote_path":
      if path.startswith("/"):
        raise ConfigError("currently only support relative paths for local_path and remote_path: %s" % path)
      raw[path] = raw[path].rstrip("/")

    # Parse enums.
    try:
      raw["copy_type"] = CopyType[raw["copy_type"]]
    except KeyError:
      raise ConfigError("invalid copy type: %s" % raw["copy_type"])

    yield Config(**raw)


@log_calls
def cache_dir():
  try:
    config_dir = _locate_config_dir(CONFIG_DIR_SEARCH_ORDER)
  except ConfigError:
    config_dir = utils.shell_expand_variables(CONFIG_DIR_DEFAULT, os.environ)
    log.info("config dir not found, so creating: %s", config_dir)
  cache_dir = os.path.join(config_dir, "cache")
  utils.make_all_dirs(cache_dir)
  return cache_dir


def load(override_path, search_dirs=CONFIG_FILE_SEARCH_ORDER):
  """Load all configs from a single file. Use override_path or the first one found in search_dirs."""
  if not search_dirs:
    search_dirs = []
  return list(_parse_validate(_load_raw_configs(override_path, search_dirs)))


def print_configs(configs, stream=sys.stdout):
  yaml.dump({"items": [config.as_string_dict() for config in configs]},
            stream=stream, default_flow_style=False)


def _yaml_ordering_support():
  """
  Get yaml lib to handle OrderedDicts.
  See http://stackoverflow.com/questions/5121931/in-python-how-can-you-load-yaml-mappings-as-ordereddicts
  """
  _mapping_tag = yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG

  def dict_representer(dumper, data):
    return dumper.represent_dict(data.iteritems())

  def dict_constructor(loader, node):
    return OrderedDict(loader.construct_pairs(node))

  yaml.add_representer(OrderedDict, dict_representer)
  yaml.add_constructor(_mapping_tag, dict_constructor)


_yaml_ordering_support()
