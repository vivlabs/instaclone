"""
Instaclone main library.
"""

from __future__ import print_function

__author__ = 'jlevy'

import logging as log
import re
import sys
import os
import shutil

from enum import Enum  # enum34


# The subprocess module has known threading issues, so prefer subprocess32.
try:
  import subprocess32 as subprocess
except ImportError:
  import subprocess

from strif import (atomic_output_file, temp_output_dir, write_string_to_file, DEV_NULL,
                   move_to_backup, movefile, copyfile_atomic, copytree_atomic, file_sha1,
                   make_all_dirs, make_parent_dirs, chmod_native,
                   shell_expand_to_popen,
                   dict_merge)

import archives
import configs

from log_calls import log_calls

SHELL_OUTPUT = sys.stderr

# We only support one archive format currently.
ARCHIVER = archives.TarGzArchiver

# Suffix to use when making backups.
BACKUP_SUFFIX = ".bak"

class AppError(RuntimeError):
  pass


@log_calls
def _make_readonly(path, silent=False):
  if silent and not os.path.exists(path):
    return
  return chmod_native(path, "ugo-w", recursive=True)


@log_calls
def _make_writable(path, silent=False):
  if silent and not os.path.exists(path):
    return
  return chmod_native(path, "u+w", recursive=True)


def _upload_file(command_template, local_path, remote_loc):
  popenargs = shell_expand_to_popen(command_template,
                                    dict_merge(os.environ, {"REMOTE": remote_loc, "LOCAL": local_path}))
  log.info("uploading: %s", " ".join(popenargs))
  # TODO: Find a way to support force here (e.g. add or remove -f to s4cmd)
  subprocess.check_call(popenargs, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


def _download_file(command_template, remote_loc, local_path):
  with atomic_output_file(local_path, make_parents=True) as temp_target:
    popenargs = shell_expand_to_popen(command_template,
                                      dict_merge(os.environ, {"REMOTE": remote_loc, "LOCAL": temp_target}))
    log.info("downloading: %s", " ".join(popenargs))
    # TODO: Find a way to support force here.
    subprocess.check_call(popenargs, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


def _compress_dir(local_dir, archive_path, force=False):
  if os.path.exists(archive_path):
    if force:
      log.info("deleting previous archive: %s", archive_path)
      os.unlink(archive_path)
    else:
      raise AppError("Archive already in cache (has version changed?): %r" % archive_path)
  with atomic_output_file(archive_path) as temp_archive:
    make_parent_dirs(temp_archive)
    ARCHIVER.archive(local_dir, temp_archive)


def _decompress_dir(archive_path, target_path, force=False):
  if os.path.exists(target_path):
    if force:
      log.info("deleting previous dir: %s", target_path)
      _rmtree_fast(target_path)
    else:
      raise AppError("Target already exists: %r" % target_path)
  with atomic_output_file(target_path) as temp_dir:
    make_all_dirs(temp_dir)
    ARCHIVER.unarchive(archive_path, temp_dir)


def _rsync_dir(source_dir, target_dir, chmod=None):
  """
  Use rsync to clone source_dir to target_dir. Preserves the original owners and permissions.
  As an optimization, rsync also allows perms to be partly modified as files are synced.
  """
  popenargs = ["rsync", "-a", "--delete"]
  if chmod:
    popenargs.append("--chmod=%s" % chmod)
  popenargs.append(source_dir.rstrip('/') + '/')
  popenargs.append(target_dir)
  log.info("using rsync for faster copy")
  log.debug("rsync: %r" % popenargs)
  subprocess.check_call(popenargs)


def _rmtree_fast(path, ignore_errors=False):
  """
  Delete a file or directory. Uses rsync to delete directories, which is among the fastest
  ways possible to delete large numbers of files. Note it can remove some read-only files.
  See http://www.slashroot.in/which-is-the-fastest-method-to-delete-files-in-linux
  """
  if ignore_errors and not os.path.exists(path):
    return
  if os.path.isdir(path) and not os.path.islink(path):
    with temp_output_dir("empty.", always_clean=True) as empty_dir:
      popenargs = ["rsync", "-r", "--delete", empty_dir + '/', path]
      subprocess.check_call(popenargs)
    os.rmdir(path)
  else:
    os.unlink(path)


@log_calls
def _install_from_cache(cache_path, target_path, install_method, force=False, make_backup=False):
  """
  Install a file or directory from cache, either symlinking, hardlinking, or copying.
  """

  def clear_symlink():
    # Never backup links (they are probably previous installs).
    if os.path.islink(target_path):
      os.unlink(target_path)

  def checked_remove():
    clear_symlink()
    if os.path.exists(target_path):
      if force:
        if make_backup:
          _rmtree_fast(target_path + BACKUP_SUFFIX, ignore_errors=True)
          move_to_backup(target_path, backup_suffix=BACKUP_SUFFIX)
        else:
          _rmtree_fast(target_path)
      else:
        raise AppError("Target already exists: %r" % target_path)

  if not os.path.exists(cache_path):
    raise AssertionError("Cached file missing: %r" % cache_path)
  log.debug("using install method %s", install_method.name)
  if install_method == configs.InstallMethod.symlink:
    checked_remove()
    os.symlink(cache_path, target_path)
  elif install_method == configs.InstallMethod.hardlink:
    if os.path.isdir(cache_path):
      raise AppError("Can't hardlink a directory: %r" % cache_path)
    checked_remove()
    os.link(cache_path, target_path)
  elif install_method == configs.InstallMethod.copy:
    checked_remove()
    copytree_atomic(cache_path, target_path)
  elif install_method == configs.InstallMethod.fastcopy:
    if os.path.isdir(cache_path):
      # Try it and see if rsync is available.
      try:
        clear_symlink()
        # Ensure we add write perms since the cache has read-only perms. Other perms will be preserved.
        _rsync_dir(cache_path, target_path, chmod="u+w")
      except OSError as e:
        log.info("rsync doesn't seem to be here (%s), so will copy instead", e)
        checked_remove()
        copytree_atomic(cache_path, target_path)
    else:
      checked_remove()
      copyfile_atomic(cache_path, target_path)
  else:
    raise AssertionError("Invalid install_method: %r" % install_method)


VERSION_SEP = ".$"
VERSION_END = "$"


class FileCache(object):
  """
  Manage uploading and downloading files to/from the cloud using a local cache to maintain copies.
  Also seamlessly support directories by archiving them as compressed files.
  The cache is not bounded and must be managed/cleaned up manually.
  """

  version = "1"

  def __init__(self, root_path):
    self.root_path = root_path.rstrip("/")
    self.contents_path = os.path.join(root_path, "contents")
    self.version_path = os.path.join(root_path, "version")
    self.setup_done = False
    assert os.path.exists(self.root_path)

  def setup(self):
    """Lazy initialize file cache post instantiation."""
    if not self.setup_done:
      if os.path.exists(self.version_path):
        log.info("using cache: %s", self.root_path)
      else:
        log.info("initializing new cache: %s", self.root_path)
        make_all_dirs(self.contents_path)
        write_string_to_file(self.version_path, FileCache.version + "\n")
      self.setup_done = True

  def __str__(self):
    return "FileCache@%s" % self.root_path

  def __repr__(self):
    return self.__str__()

  @staticmethod
  def versioned_path(config, version, suffix=""):
    return os.path.join(config.remote_path,
                        "%s%s%s%s" % (config.name, VERSION_SEP, version, VERSION_END),
                        "%s%s" % (os.path.basename(config.name), suffix))

  @staticmethod
  def pathify_remote_loc(remote_loc):
    return os.path.join(*re.findall("[a-zA-Z0-9_.-]+", remote_loc))

  def cache_path(self, config, version, suffix=""):
    return os.path.join(self.contents_path,
                        self.pathify_remote_loc(config.remote_prefix),
                        self.versioned_path(config, version, suffix))

  def remote_loc(self, config, version, suffix=""):
    return os.path.join(config.remote_prefix,
                        self.versioned_path(config, version, suffix))

  def _upload(self, config, cached_path, version):
    _upload_file(config.upload_command, cached_path, self.remote_loc(config, version))

  @log_calls
  def publish(self, config, version, force=False):
    # As precaution for users, we keep unarchived items in cache that may be symlinked to as read-only.
    cached_path = self.cache_path(config, version)
    try:
      _make_writable(cached_path, silent=True)
      self._publish_writable(config, version, force=force)
    finally:
      _make_readonly(cached_path, silent=True)

  def _publish_writable(self, config, version, force=False):
    local_path = config.local_path
    cached_path = self.cache_path(config, version)

    if os.path.islink(local_path):
      raise AppError("Cannot publish symlinks (is path already published?): %r" % local_path)

    self.setup()

    # Directories are archived. Files are published as is.
    if os.path.isdir(local_path):
      cached_archive = self.cache_path(config, version, suffix=ARCHIVER.suffix)
      remote_loc = self.remote_loc(config, version, suffix=ARCHIVER.suffix)

      # We archive and then unarchive, to make sure we expand symlinks exactly the way
      # a future installation would.
      # TODO: This is usually what we want (think of relative symlinks like ../../foo), but we could make it an option.
      log.debug("installing to cache: %s -> %s", local_path, cached_path)
      _compress_dir(local_path, cached_archive, force=force)
      _upload_file(config.upload_command, cached_archive, remote_loc)
      _decompress_dir(cached_archive, cached_path, force=force)
      # If everything has succeeded, we can safely delete the archive to save space.
      os.unlink(cached_archive)
      # Leave the previous version of the tree as a backup.
      log.info("installed to cache: %s -> %s", local_path, cached_path)
      _install_from_cache(cached_path, local_path, config.install_method, force=True, make_backup=True)
      log.info("published archive: %s", remote_loc)
    elif os.path.isfile(local_path):
      remote_loc = self.remote_loc(config, version)

      log.debug("installing to cache: %s -> %s", local_path, cached_path)
      # For speed on large files, move it rather than copy.
      # Also make it read-only, just as it will be after install.
      movefile(local_path, cached_path, make_parents=True)
      _upload_file(config.upload_command, cached_path, remote_loc)
      log.info("installed to cache: %s -> %s", local_path, cached_path)
      _install_from_cache(cached_path, local_path, config.install_method, force=False, make_backup=False)
      log.info("published file: %s", remote_loc)
    elif os.path.exists(local_path):
      # Not a file, dir, or symlink!
      raise ValueError("Only files or directories can be published: %r" % local_path)
    else:
      raise ValueError("File not found: %r" % local_path)

  @log_calls
  def install(self, config, version, force=False):
    self.setup()
    cached_path = self.cache_path(config, version)
    if os.path.exists(cached_path):
      # It's a cached file or a cached directory and we've already unpacked it.
      _install_from_cache(cached_path, config.local_path, config.install_method, force=force)
      log.info("installed from cache (%s): %s -> %s", config.install_method.name, config.local_path, cached_path)
    else:
      # First try it as a directory/archive.
      remote_archive_loc = self.remote_loc(config, version, suffix=ARCHIVER.suffix)
      cached_archive_path = self.cache_path(config, version, suffix=ARCHIVER.suffix)
      is_dir = True
      # This could be cleaner, but it's nice to be data-driven and not require a config saying it's a dir or file.
      log.debug("checking if it's a directory by seeing if archive suffix exits")
      try:
        _download_file(config.download_command, remote_archive_loc, cached_archive_path)
      except subprocess.CalledProcessError:
        log.debug("doesn't look like an archived directory, so treating it as a file")
        is_dir = False
      if is_dir:
        log.info("downloaded published archive: %s", remote_archive_loc)
        _decompress_dir(cached_archive_path, cached_path, force=force)
        # If everything has succeeded, we can safely delete the archive to save space.
        os.unlink(cached_archive_path)
        log.info("installed directory: %s -> %s", config.local_path, cached_path)
      else:
        remote_loc = self.remote_loc(config, version)
        _download_file(config.download_command, remote_loc, cached_path)
        log.info("downloaded published file: %s", remote_loc)
        log.info("installed file: %s -> %s", config.local_path, cached_path)

      _make_readonly(cached_path)
      _install_from_cache(cached_path, config.local_path, config.install_method, force=force)

  @log_calls
  def purge(self):
    log.info("purging cache: %s", self.root_path)
    _make_writable(self.root_path, silent=True)
    _rmtree_fast(self.root_path)


def version_for(config):
  """
  The version for an item is either the explicit version specified by the user, or the SHA1 hash of hashable file.
  """
  bits = []
  if config.version_string:
    bits.append(str(config.version_string))
  if config.version_hashable:
    log.debug("computing sha1 of: %s", config.version_hashable)
    bits.append(file_sha1(config.version_hashable))
  if config.version_command:
    log.debug("version command: %s", config.version_command)
    popenargs = shell_expand_to_popen(config.version_command, os.environ)
    output = subprocess.check_output(popenargs, stderr=SHELL_OUTPUT, stdin=DEV_NULL).strip()
    if not configs._CONFIG_VERSION_RE.match(output):
      raise configs.ConfigError("Invalid version output from version command: %r" % output)
    bits.append(output)

  return "-".join(bits)


#
# ---- Command line ----

Command = Enum("Command", "publish install purge configs")
_command_list = [c.name for c in Command]


def select_configs(config_list, items):
  """Select configs by name, or all configs if none specified."""
  if items:
    log.debug("selecting configs for items: %s", items)
    new_config_list = []
    for item in items:
      matches = filter(lambda config: config.name == item, config_list)
      if len(matches) < 1:
        raise ValueError("Could not find config for item: %s" % item)
      new_config_list.append(matches[0])
    config_list = new_config_list
  return config_list


def run_command(command, override_path=None, overrides=None, force=False, items=None):
  # Nondestructive commands that don't require cache.
  if command == Command.configs:
    config_list = select_configs(configs.load(override_path=override_path, overrides=overrides), items)
    configs.print_configs(config_list)

  # Destructive commands that require cache but not configs.
  elif command == Command.purge:
    file_cache = FileCache(configs.set_up_cache_dir())
    file_cache.purge()

  # Commands that require cache and configs.
  else:
    config_list = select_configs(configs.load(override_path=override_path, overrides=overrides), items)

    file_cache = FileCache(configs.set_up_cache_dir())

    if command == Command.publish:
      for config in config_list:
        file_cache.publish(config, version_for(config), force=force)

    elif command == Command.install:
      for config in config_list:
        file_cache.install(config, version_for(config), force=force)

    else:
      raise AssertionError("unknown command: " + command)

# TODO:
# - "clean" command that deletes local resources (requiring -f if not in cache)
# - "unpublish" command that deletes a remote resource (and purges from cache)
# - --no-cache option that just downloads
# - consider new feature:
#   failover_command that is executed if install fails,
#   and a flag failover_publish indicating whether to publish
# - command to unpublish all but most recent n versions of a resource?
# - support compressing files as well as archives
# - consider a pax-based hardlink tree copy option (since pax is cross platform, unlike cp's options)
# - init command to generate a config
# - "--offline" mode for install (i.e. will fail if it has to download)
# - test out more custom transport commands (s3cmd, awscli, wget, etc.)
# - for the custom transport like curl, figure out handling of shell redirects?
#   (perhaps better just to require a bash wrapper)
