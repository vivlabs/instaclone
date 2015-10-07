"""
Instaclone configuration handling.
"""

__author__ = 'jlevy'

import logging as log
import os
import re
import sys
from collections import namedtuple, OrderedDict

from enum import Enum  # enum34 pip
import yaml  # PyYAML pip
from functools32 import lru_cache  # functools32 pip
import strif

from log_calls import log_calls

_NAME_FIELD = "name"
_required_fields = "local_path remote_path remote_prefix install_method upload_command download_command"
_other_fields = "version_string version_hashable version_command"

ConfigBase = namedtuple("ConfigBase", _NAME_FIELD + " " + _other_fields + " " + _required_fields)

CONFIGS_REQUIRED = _required_fields.split()
CONFIG_DEFAULTS = {
  "install_method": "symlink"
}
CONFIG_DESCRIPTIONS = {
  "local_path": "the local target path to sync to, relative to current dir",
  "remote_path": "remote path (in backing store such as S3) to sync to",
  "remote_prefix": "remote path prefix (such as s3://my-bucket/instaclone) to sync to",
  "install_method": "the way to install files (symlink, copy, fastcopy, hardlink)",
  "upload_command": "shell command template to upload file",
  "download_command": "shell command template to download file",
  "version_string": "explicit version string to use",
  "version_hashable": "a file path that should be SHA1 hashed to get a version string",
  "version_command": "a shell command that should be run to get a version string",
}
# For now, allow anything to be overridden.
CONFIG_OVERRIDABLE = CONFIG_DESCRIPTIONS.keys()

_CONFIG_VERSION_RE = re.compile("^[\\w.-]+$")


def _stringify_config_field(value):
  return value.name if isinstance(value, Enum) else str(value)


class Config(ConfigBase):
  """Configuration for a single item."""

  def as_string_dict(self):
    d = dict(self._asdict())
    return {k: _stringify_config_field(v) for (k, v) in d.iteritems() if v is not None and k != _NAME_FIELD}


CONFIG_NAME = "instaclone"
CONFIG_DIR_ENV = "INSTACLONE_DIR"
CONFIG_HOME_DIR = ".instaclone"

DEFAULT_ITEM_NAME = "default"


class ConfigError(RuntimeError):
  pass


InstallMethod = Enum("InstallMethod", "symlink hardlink copy fastcopy")


@lru_cache(maxsize=None)
def _locate_config_dir():
  """Check for which config directory to use."""
  if CONFIG_DIR_ENV in os.environ:
    config_dir = os.environ[CONFIG_DIR_ENV]
  else:
    config_dir = os.path.join(os.environ["HOME"], CONFIG_HOME_DIR)
  return config_dir


def _locate_config_file(search_dirs):
  """Look in common locations for config file."""
  tried = []
  for base in search_dirs:
    for path in [os.path.join(base, CONFIG_NAME + suffix) for suffix in ".yml", ".json"]:
      log.debug("searching for config file: %s", path)
      tried.append(path)
      if os.path.isfile(path):
        log.info("using config file: %s", path)
        return path
  raise ConfigError("no config file found in: %s" % ", ".join(tried))


@log_calls
def _load_raw_configs(override_path, defaults, overrides):
  """
  Merge defaults, configs from a file, and overrides.
  Uses first config file in override_path (if set) or finds it in current dir or config dir.
  """
  if override_path:
    path = override_path
  else:
    search_dirs = [".", _locate_config_dir()]
    path = _locate_config_file(search_dirs)

  with open(path) as f:
    parsed_configs = yaml.safe_load(f)

  out = []
  try:
    items = parsed_configs["items"]
    for config_dict in items:
      # Legacy fix for renamed key. TODO: Remove this after a while.
      if "copy_type" in config_dict:
        config_dict["install_method"] = config_dict["copy_type"]
        del config_dict["copy_type"]

      # Name this config (since we may override the local_path).
      config_dict["name"] = config_dict["local_path"]

      nones = {key: None for key in Config._fields}
      combined = strif.dict_merge(nones, defaults, config_dict, overrides)
      log.debug("raw, combined config: %r", combined)

      try:
        out.append(combined)
      except TypeError as e:
        raise ConfigError("error in config value: %s: %s" % (e, config_dict))
  except ValueError as e:
    raise ConfigError("error reading config file: %s" % e)

  return out


def _parse_and_validate(raw_config_list):
  """
  Parse and validate settings. Merge settings from config files, global defaults, and command-line overrides.
  """
  items = []
  for raw in raw_config_list:

    # Validation.
    for key in CONFIGS_REQUIRED:
      if key not in raw or raw[key] is None:
        raise ConfigError("must specify '%s' in item config: %s" % (key, raw))

    if "version_string" in raw and not _CONFIG_VERSION_RE.match(str(raw["version_string"])):
      raise ConfigError("invalid version string: '%s'" % raw["version_string"])
    if "version_string" not in raw and "version_hashable" not in raw and "version_command" not in raw:
      raise ConfigError("must specify 'version_string', 'version_hashable', or 'version_command' in item config: %s" % raw)

    # Validate shell templates.
    # For these, we don't expand environment variables here, but instead do it at once at call time.
    for key in "upload_command", "download_command":
      try:
        strif.shell_expand_to_popen(raw[key], {"REMOTE": "dummy", "LOCAL": "dummy"})
      except ValueError as e:
        raise ConfigError("invalid command in config value for %s: %s" % (key, e))

    # Normalize and expand environment variables.
    for key in "local_path", "remote_prefix", "remote_path":
      if key.startswith("/"):
        raise ConfigError("currently only support relative paths for local_path and remote_path: %s" % key)
      raw[key] = raw[key].rstrip("/")

      try:
        raw[key] = strif.expand_variables(raw[key], os.environ)
      except ValueError as e:
        raise ConfigError("invalid command in config value for %s: %s" % (key, e))

    # Parse enums.
    try:
      raw["install_method"] = InstallMethod[raw["install_method"]]
    except KeyError:
      raise ConfigError("invalid copy type: %s" % raw["install_method"])

    items.append(Config(**raw))

  log.debug("final configs: %s", items)
  return items


@log_calls
def set_up_cache_dir():
  config_dir = _locate_config_dir()
  cache_dir = os.path.join(config_dir, "cache")
  if not os.path.exists(cache_dir):
    log.info("cache dir not found, so creating: %s", cache_dir)
    strif.make_all_dirs(cache_dir)
  return cache_dir


def load(override_path=None, overrides=None):
  """
  Load all configs from a single file. Use override_path or the first one found in standard locations.
  If overrides are present, these override all settings.
  """
  if not overrides:
    overrides = {}
  return _parse_and_validate(_load_raw_configs(override_path, CONFIG_DEFAULTS, overrides))


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
