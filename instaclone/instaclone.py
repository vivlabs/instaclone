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

import configs
from utils import atomic_output_file, write_string_to_file, DEV_NULL
from utils import move_to_backup, movefile, copytree_atomic, rmtree_or_file, file_sha1
from utils import make_all_dirs, make_parent_dirs, chmod_native
from utils import shell_expand_to_popen
from utils import dict_merge
from log_calls import log_calls

SHELL_OUTPUT = sys.stderr


class AppError(RuntimeError):
  pass


def _upload_file(command_template, local_path, remote_loc):
  popenargs = shell_expand_to_popen(command_template,
                                    dict_merge(os.environ, {"REMOTE": remote_loc, "LOCAL": local_path}))
  log.info("uploading: %s", " ".join(popenargs))
  # TODO: Find a way to support force here (e.g. add or remove -f to s4cmd)
  subprocess.check_call(popenargs, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


def _download_file(command_template, remote_loc, local_path, make_readonly=False):
  with atomic_output_file(local_path, make_parents=True) as temp_target:
    popenargs = shell_expand_to_popen(command_template,
                                      dict_merge(os.environ, {"REMOTE": remote_loc, "LOCAL": temp_target}))
    log.info("downloading: %s", " ".join(popenargs))
    # TODO: Find a way to support force here.
    subprocess.check_call(popenargs, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)

    if make_readonly:
      chmod_native(temp_target, "go-rwx")

# For simplicity, we currently only support zip compression.
# We use command-line standard zip/unzip instead of Python zip, since it is a bit more performant
# and makes the archiving mechanism pluggable in the future.
# TODO: Note zip/unzip by default follows symlinks, so full contents are included. Consider making this a flag.

ARCHIVE_SUFFIX = ".zip"


def _compress_dir(local_dir, archive_path, force=False):
  if os.path.exists(archive_path):
    if force:
      log.info("deleting previous archive: %s", archive_path)
      os.unlink(archive_path)
    else:
      raise AppError("archive already in cache (has version changed?): %s" % archive_path)
  with atomic_output_file(archive_path) as temp_archive:
    make_parent_dirs(temp_archive)
    popenargs = shell_expand_to_popen("zip -q -r $ARCHIVE $DIR", {"ARCHIVE": temp_archive, "DIR": "."})
    cd_to = local_dir
    log.debug("using cwd: %s", cd_to)
    log.info("compress: %s", " ".join(popenargs))
    subprocess.check_call(popenargs, cwd=cd_to, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


def _decompress_dir(archive_path, local_dir, force=False, make_readonly=False):
  if os.path.exists(local_dir):
    if force:
      log.info("deleting previous dir: %s", local_dir)
      shutil.rmtree(local_dir)
    else:
      raise AppError("target already exists: %s" % local_dir)
  with atomic_output_file(local_dir) as temp_dir:
    popenargs = shell_expand_to_popen("unzip -q $ARCHIVE", {"ARCHIVE": archive_path, "DIR": temp_dir})
    make_all_dirs(temp_dir)
    cd_to = temp_dir
    log.debug("using cwd: %s", cd_to)
    log.info("decompress: %s", " ".join(popenargs))

    subprocess.check_call(popenargs, cwd=cd_to, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)

    if make_readonly:
      # Make whole cache read-only to avoid subtle bugs/issues if people modify it.
      # You might think you could do this with umask on the command above, but in the case of unzip it
      # modifies permissions explicitly. So we do it brute force.
      chmod_native(temp_dir, "go-rwx", recursive=True)


@log_calls
def _install_from_cache(cache_path, target_path, copy_type, force=False, make_backup=False):
  """
  Install a file or directory from cache, either symlinking, hardlinking, or copying.
  """
  def checked_remove():
    # Never backup links (they are probably previous installs).
    if os.path.islink(target_path):
      os.unlink(target_path)
    elif os.path.exists(target_path):
      if force:
        if make_backup:
          move_to_backup(target_path)
        else:
          rmtree_or_file(target_path)
      else:
        raise AppError("target already exists: %s" % target_path)

  if not os.path.exists(cache_path):
    raise AssertionError("cached file missing: %s" % cache_path)
  if copy_type == configs.CopyType.symlink:
    checked_remove()
    os.symlink(cache_path, target_path)
  elif copy_type == configs.CopyType.hardlink:
    if os.path.isdir(cache_path):
      raise AppError("can't hardlink a directory: %s" % cache_path)
    checked_remove()
    os.link(cache_path, target_path)
  elif copy_type == configs.CopyType.copy:
    checked_remove()
    copytree_atomic(cache_path, target_path)
  else:
    raise AssertionError("invalid copy_type: %s" % copy_type)


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
                        "%s%s%s%s" % (config.local_path, VERSION_SEP, version, VERSION_END),
                        "%s%s" % (os.path.basename(config.local_path), suffix))

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
    local_path = config.local_path
    cached_path = self.cache_path(config, version)

    if os.path.islink(local_path):
      raise AppError("cannot publish symlinks (is path already published?): %s" % local_path)

    self.setup()

    # Directories are archived. Files are published as is.
    if os.path.isdir(local_path):
      cached_archive = self.cache_path(config, version, ARCHIVE_SUFFIX)
      remote_loc = self.remote_loc(config, version, ARCHIVE_SUFFIX)

      # We archive and then unarchive, to make sure we expand symlinks exactly the way
      # a future installation would (using zip/unzip).
      # TODO: This is usually what we want (think of relative symlinks like ../../foo), but we could make it an option.
      log.info("installing to cache: %s -> %s", local_path, cached_path)
      _compress_dir(local_path, cached_archive, force=force)
      _upload_file(config.upload_command, cached_archive, remote_loc)
      _decompress_dir(cached_archive, cached_path, force=force, make_readonly=True)
      # Leave the previous version of the tree as a backup.
      log.debug("installing back from cache: %s <- %s", local_path, cached_path)
      _install_from_cache(cached_path, local_path, config.copy_type, force=True, make_backup=True)
      log.info("published directory archive: %s -> %s", config.local_path, remote_loc)
    elif os.path.isfile(local_path):
      remote_loc = self.remote_loc(config, version)

      log.info("installing to cache: %s -> %s", local_path, cached_path)
      # For speed on large files, move it rather than copy.
      # Also make it read-only, just as it will be after install.
      chmod_native(local_path, "go-rwx")
      movefile(local_path, cached_path, make_parents=True)
      _upload_file(config.upload_command, cached_path, remote_loc)
      log.debug("installing back from cache: %s <- %s", local_path, cached_path)
      _install_from_cache(cached_path, local_path, config.copy_type, force=False, make_backup=False)
      log.info("published file: %s -> %s", config.local_path, remote_loc)
    elif os.path.exists(local_path):
      # TODO: Consider handling symlinks.
      raise ValueError("only files or directories supported: %s" % local_path)
    else:
      raise ValueError("file not found: %s" % local_path)

  @log_calls
  def install(self, config, version, force=False):
    self.setup()
    cached_path = self.cache_path(config, version)
    if os.path.exists(cached_path):
      # It's a cached file or a cached directory and we've already unpacked it.
      log.info("installing from cache: %s <- %s", config.local_path, cached_path)
      _install_from_cache(cached_path, config.local_path, config.copy_type, force=force)
    else:
      # First try it as a directory/archive.
      remote_archive_loc = self.remote_loc(config, version, ARCHIVE_SUFFIX)
      cached_archive_path = self.cache_path(config, version, suffix=ARCHIVE_SUFFIX)
      is_dir = True
      # TODO: This could be cleaner, but it's nice to be data-driven and not require a config saying it's a dir or file.
      log.debug("checking if it's a directory by seeing if archive suffix exits")
      try:
        _download_file(config.download_command, remote_archive_loc, cached_archive_path)
      except subprocess.CalledProcessError:
        log.debug("doesn't look like an archived directory, so treating it as a file")
        is_dir = False
      if is_dir:
        log.info("installing directory: %s <- %s <- %s", config.local_path, cached_path, remote_archive_loc)
        _decompress_dir(cached_archive_path, cached_path, force=force, make_readonly=True)
      else:
        remote_loc = self.remote_loc(config, version)
        log.info("installing file: %s <- %s <- %s", config.local_path, cached_path, remote_loc)
        _download_file(config.download_command, remote_loc, cached_path, make_readonly=True)

      _install_from_cache(cached_path, config.local_path, config.copy_type, force=force)

  @log_calls
  def purge(self):
    log.info("purging cache: %s", self.root_path)
    shutil.rmtree(self.root_path)


def version_for(config):
  """
  The version for an item is either the explicit version specified by the user, or the SHA1 hash of hashable file.
  """
  bits = []
  if config.version:
    bits.append(str(config.version))
  if config.version_hashable:
    log.debug("computing sha1 of: %s", config.version_hashable)
    bits.append(file_sha1(config.version_hashable))
  if config.version_command:
    log.debug("version command: %s", config.version_command)
    popenargs = shell_expand_to_popen(config.version_command, os.environ)
    output = subprocess.check_output(popenargs, stderr=SHELL_OUTPUT, stdin=DEV_NULL).strip()
    if not configs.CONFIG_VERSION_RE.match(output):
      raise configs.ConfigError("invalid version output from version command: '%s'" % output)
    bits.append(output)

  return "-".join(bits)

#
# ---- Command line ----

Command = Enum("Command", "publish install purge configs")
_command_list = [c.name for c in Command]


def run_command(command, config_list, force=False):
  # Don't initialize cache for nondestructive commands.
  if command == Command.configs:
    configs.print_configs(config_list)
  else:
    cache_dir = configs.set_up_cache_dir()
    file_cache = FileCache(cache_dir)

    if command == Command.publish:
      for config in config_list:
        file_cache.publish(config, version_for(config), force=force)

    elif command == Command.install:
      for config in config_list:
        file_cache.install(config, version_for(config), force=force)

    elif command == Command.purge:
      file_cache.purge()

    else:
      raise AssertionError("unknown command: " + command)

# TODO:
# - failover_command that is executed if install fails, and failover_publish
# - failover_publish flag
# - merge command-line args and config file values?
# - --no-cache option that just downloads
# - let "instaclone purge" work even if not in a dir with a config file
# - think how to auto-purge all but one resource per branch (say) (and generalize to env variable)
# - "clean" command that deletes local resources (requiring -f if not in cache)
# - "unpublish" command that deletes a remote resource (and purges from cache)
# - command to unpublish all but most recent n versions of a resource
# - support compressing files as well as archives
# - consider a pax-based hardlink tree copy option (since pax is cross platform, unlike cp's options)
# - init command to generate a config
# - "--offline" mode for install (i.e. will fail if it has to download)
# - test out more custom transport commands (s3cmd, awscli, wget, etc.)
# - for the custom transport like curl, figure out handling of shell redirects (or just require)
