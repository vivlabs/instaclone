"""
A few generally useful string- and file-related utilities.
"""

__author__ = 'jlevy'

from string import Template
import os
import random
import shutil
import shlex
import pipes
import hashlib
from contextlib import contextmanager
from datetime import datetime

DEV_NULL = open(os.devnull, 'wb')

BACKUP_SUFFIX = ".bak"

_RANDOM = random.SystemRandom()
_RANDOM.seed()


def new_uid(bits=64):
  """
  A random alphanumeric value with at least the specified bits of randomness. We use base 36,
  i.e. not case sensitive. Note this makes it suitable for filenames even on case-insensitive disks.
  """
  return "".join(_RANDOM.sample("0123456789abcdefghijklmnopqrstuvwxyz",
                                int(bits / 5.16) + 1))  # log(26 + 10)/log(2) = 5.16


def iso_timestamp():
  """
  ISO timestamp.
  """
  return datetime.now().isoformat() + 'Z'


#
# ---- Templating ----

def expand_variables(template_str, value_map, transformer=None):
  """
  Expand a template string like "blah blah $FOO blah" using given value mapping.
  """
  if template_str is None:
    return None
  else:
    if transformer is None:
      transformer = lambda v: v
    try:
      transformed_value_map = {k: transformer(v) for (k, v) in value_map.iteritems()}
      return Template(template_str).substitute(transformed_value_map)
    except Exception as e:
      raise ValueError("could not expand variable names in command '%s': %s" % (template_str, e))


def shell_expand_variables(template_str, value_map):
  """
  Expand a shell template string like "cp $SOURCE $TARGET/blah", also quoting values as needed
  to ensure shell safety.
  """
  return expand_variables(template_str, value_map, transformer=pipes.quote)


def shell_expand_to_popen(template, values):
  """
  Expand a template like "cp $SOURCE $TARGET/blah" into a list of popen arguments.
  """
  return [expand_variables(item, values) for item in shlex.split(template)]


#
# ---- File operations ----

def move_to_backup(path, backup_suffix=BACKUP_SUFFIX):
  """
  Move the given file or directory to the same name, with a backup suffix.
  If backup_suffix not supplied, move it to the extension ".bak".
  NB: If backup_suffix is supplied and is None, don't do anything.
  """
  if backup_suffix and os.path.exists(path):
    backup_path = path + backup_suffix
    # Some messy corner cases need to be handled for existing backups.
    # TODO: Note if this is a directory, and we do this twice at once, there is a potential race
    # that could leave one backup inside the other.
    if os.path.islink(backup_path):
      os.unlink(backup_path)
    elif os.path.isdir(backup_path):
      shutil.rmtree(backup_path)
    shutil.move(path, backup_path)


def make_all_dirs(path, mode=0777):
  """
  Ensure local dir, with all its parent dirs, are created.
  Unlike os.makedirs(), will not fail if the path already exists.
  """
  if not os.path.isdir(path):
    os.makedirs(path, mode=mode)


def make_parent_dirs(path):
  """
  Ensure parent directories of a file are created as needed.
  """
  dir = os.path.dirname(path)
  if dir and not os.path.isdir(dir):
    os.makedirs(dir)
  return path


@contextmanager
def atomic_output_file(dest_path, make_parents=False, backup_suffix=None, suffix=".partial.%s"):
  """
  A context manager for convenience in writing a file or directory in an atomic way. Set up
  a temporary name, then rename it after the operation is done, optionally making a backup of
  the previous file or directory, if present.
  """
  tmp_path = ("%s" + suffix) % (dest_path, new_uid())
  if make_parents:
    make_parent_dirs(tmp_path)
  yield tmp_path
  if not os.path.exists(tmp_path):
    raise IOError("failure in writing file '%s': target file '%s' missing" % (dest_path, tmp_path))
  if backup_suffix:
    move_to_backup(dest_path, backup_suffix=backup_suffix)
  # If the target already exists, and is a directory, it has to be removed.
  if os.path.isdir(dest_path):
    shutil.rmtree(dest_path)
  shutil.move(tmp_path, dest_path)


def read_string_from_file(path):
  """
  Read entire contents of file into a string.
  """
  with open(path, "rb") as f:
    value = f.read()
  return value


def write_string_to_file(path, string, make_parents=False, backup_suffix=BACKUP_SUFFIX):
  """
  Write entire file with given string contents, atomically. Keeps backup by default.
  """
  with atomic_output_file(path, make_parents=make_parents, backup_suffix=backup_suffix) as tmp_path:
    with open(tmp_path, "wb") as f:
      f.write(string)


def set_file_mtime(path, mtime, atime=None):
  """Set access and modification times on a file."""
  if not atime:
    atime = mtime
  f = file(path, 'a')
  try:
    os.utime(path, (atime, mtime))
  finally:
    f.close()


def copyfile_atomic(source_path, dest_path, make_parents=False, backup_suffix=None):
  """
  Copy file on local filesystem in an atomic way, so partial copies never exist. Preserves timestamps.
  """
  with atomic_output_file(dest_path, make_parents=make_parents, backup_suffix=backup_suffix) as tmp_path:
    shutil.copyfile(source_path, tmp_path)
    set_file_mtime(tmp_path, os.path.getmtime(source_path))


def copytree_atomic(source_path, dest_path, make_parents=False, backup_suffix=None, symlinks=False):
  """
  Copy a file or directory recursively, and atomically, reanaming file or top-level dir when done.
  Unlike shutil.copytree, this will not fail on a file.
  """
  if os.path.isdir(source_path):
    with atomic_output_file(dest_path, make_parents=make_parents, backup_suffix=backup_suffix) as tmp_path:
      shutil.copytree(source_path, tmp_path, symlinks=symlinks)
  else:
    copyfile_atomic(source_path, dest_path, make_parents=make_parents, backup_suffix=backup_suffix)


def movefile(source_path, dest_path, make_parents=False, backup_suffix=None):
  """
  Move file. With a few extra options.
  """
  if make_parents:
    make_parent_dirs(dest_path)
  move_to_backup(dest_path, backup_suffix=backup_suffix)
  shutil.move(source_path, dest_path)


def rmtree_or_file(path, ignore_errors=False, onerror=None):
  """
  rmtree fails on files or symlinks. This removes the target, whatever it is.
  """
  if os.path.isdir(path) and not os.path.islink(path):
    shutil.rmtree(path, ignore_errors=ignore_errors, onerror=onerror)
  else:
    os.unlink(path)


def file_sha1(path):
  """
  Compute SHA1 hash of a file.
  """
  sha1 = hashlib.sha1()
  with open(path, "rb") as f:
    while True:
      block = f.read(2 ** 10)
      if not block:
        break
      sha1.update(block)
    return sha1.hexdigest()
