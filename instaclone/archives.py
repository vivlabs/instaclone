"""
Libraries for making archives.
"""

from __future__ import print_function

__author__ = 'jlevy'

import sys
import os
import tarfile
import tempfile
import itertools
import logging as log
from collections import namedtuple

from functools32 import lru_cache  # functools32 pip

# The subprocess module has known threading issues, so prefer subprocess32.
try:
  import subprocess32 as subprocess
except ImportError:
  import subprocess

from strif import shell_expand_to_popen, DEV_NULL

SHELL_OUTPUT = sys.stderr


class ArchiveError(RuntimeError):
  pass


_Archiver = namedtuple("_Archiver", "suffix archive unarchive")


def followlink(path, max_follows=10):
  """
  Dereference a symlink repeatedly to get a non-symlink (up to max_follows times,
  to avoid cycles).
  """
  orig_path = path
  count = 0
  if not os.path.exists(path):
    raise ValueError("Not found: %r" % path)
  while os.path.islink(path):
    # Note path.join handles it correctly if the second arg is an absolute path.
    path = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
    count += 1
    if count > max_follows:
      raise ValueError("Too many symlinks: %r" % orig_path)

  return path


def targz_dir(source_dir, target_archive, dereference_ext_symlinks=True):
  norm_source_dir = os.path.normpath(source_dir)
  total = itertools.count()
  symlinks = itertools.count()
  symlinks_followed = itertools.count()

  def tarinfo_filter(tarinfo):
    total.next()
    log.debug("adding: %s", tarinfo.__dict__)
    if tarinfo.linkname:
      symlinks.next()
      target = followlink(os.path.join(norm_source_dir, tarinfo.name))
      # If it's a relative symlink, and its target is inside our source dir, leave it as is.
      # If it's absolute or outside our source dir, resolve it or error.
      if os.path.isabs(target) \
              or not os.path.normpath(os.path.join(norm_source_dir, target)).startswith(norm_source_dir):
        if dereference_ext_symlinks:
          if not os.path.exists(target):
            raise ArchiveError("Symlink target not found: %r -> %r" % (tarinfo.name, target))
          tarinfo = tarinfo.tarfile.gettarinfo(target)
          symlinks_followed.next()
        else:
          raise ArchiveError("Absolute path in symlink target not supported: %r -> %r" % (tarinfo.name, target))
    return tarinfo

  with tarfile.open(target_archive, "w:gz") as tf:
    log.info("creating archive: %s -> %s", source_dir, target_archive)
    tf.add(source_dir, arcname=".", filter=tarinfo_filter)

  log.info("added %s items to archive (%s were symlinks, %s followed)",
           total.next(), symlinks.next(), symlinks_followed.next())


def untargz_dir(source_archive, target_dir):
  with tarfile.open(source_archive, "r:gz") as tf:
    tf.extractall(path=target_dir)


TarGzArchiver = _Archiver(".tar.gz", targz_dir, untargz_dir)


# Old code:
# We tried zip for a while but found it less satisfactory.
# We use command-line standard zip/unzip instead of Python zip, since it is a bit more performant
# than the Python native alternatives.

@lru_cache()
def _autodetect_zip_command():
  try:
    zip_output = subprocess.check_output(["zip", "-v"])
    zip_cmd = "zip -q -r $ARCHIVE $DIR"
  except subprocess.CalledProcessError as e:
    raise ArchiveError("Archive handling requires 'zip' in path: %s" % e)

  if zip_output.find("ZIP64_SUPPORT") < 0:
    log.warn("installed 'zip' doesn't have Zip64 support so will fail for large archives")
  log.debug("zip command: %s", zip_cmd)
  return zip_cmd


@lru_cache()
def _autodetect_unzip_command():
  unzip_cmd = None
  unzip_output = None
  try:
    unzip_output = subprocess.check_output(["unzip", "-v"])
    unzip_cmd = "unzip -q $ARCHIVE"
  except subprocess.CalledProcessError as e:
    pass

  # On MacOS Yosemite, unzip does not support Zip64, but ditto is available.
  # See: https://github.com/vivlabs/instaclone/issues/1
  if not unzip_cmd or not unzip_output or unzip_output.find("ZIP64_SUPPORT") < 0:
    log.debug("did not find 'unzip' with Zip64 support; trying ditto")
    try:
      # ditto has no simple flag to check its version and exit with 0 status code.
      subprocess.check_call(["ditto", "-c", "/dev/null", tempfile.mktemp()])
      unzip_cmd = "ditto -x -k $ARCHIVE ."
    except subprocess.CalledProcessError as e:
      log.debug("did not find ditto")

  if not unzip_cmd:
    raise ArchiveError("Archive handling requires 'unzip' or 'ditto' in path")

  log.debug("unzip command: %s", unzip_cmd)
  return unzip_cmd


def zip_dir(source_dir, target_archive):
  popenargs = shell_expand_to_popen(_autodetect_zip_command(), {"ARCHIVE": target_archive, "DIR": "."})
  cd_to = source_dir
  log.debug("using cwd: %s", cd_to)
  log.info("compress: %s", " ".join(popenargs))
  subprocess.check_call(popenargs, cwd=cd_to, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


def unzip_dir(source_archive, target_dir):
  popenargs = shell_expand_to_popen(_autodetect_unzip_command(), {"ARCHIVE": source_archive, "DIR": target_dir})
  cd_to = target_dir
  log.debug("using cwd: %s", cd_to)
  log.info("decompress: %s", " ".join(popenargs))
  subprocess.check_call(popenargs, cwd=cd_to, stdout=SHELL_OUTPUT, stderr=SHELL_OUTPUT, stdin=DEV_NULL)


ZipArchiver = _Archiver(".zip", zip_dir, unzip_dir)
