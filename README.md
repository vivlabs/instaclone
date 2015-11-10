# Instaclone

[![Boink](images/clone-140.jpg)](http://www.gocomics.com/calvinandhobbes/1990/01/10)

Instaclone is a simple, configurable command-line tool to publish and later install snapshots of files or directories in S3 (or another store).
It keeps a local cache of downloaded snapshots so switching between previously cached snapshots is almost instant -- just a symlink or local copy from the cache.

It works nicely when you might want to save things in Git, but can't, due to files or directories being large or sensitive, or because you have multiple variations of files for one Git revision (for example, Mac and Linux).
You can git-ignore the original files, publish them with Instaclone, and instead check in the Instaclone configuration file that references them.

Note that if all you want is to do is put big files in Git, [LFS](https://git-lfs.github.com/) may be what you want.
Instaclone is more flexible about backend storage and versioning schemes, and offers a local cache.

## Basic idea

Every item (a file or directory) can be published as an immutable snapshot.
A snapshot has a local path name, a published location (such as an S3 prefix), and a _version string_.
You can assign a version string yourself and reference it directly, or -- and this is where it's more useful -- assign it implicitly from other sources, such as the hash of another file, the platform, or the output of an arbitrary command.
The snapshot is then published with that version string.

Another client can then install that snapshot whenever it requires the same version of the item.
Installing the first time on a new client requires a download.
Subsequent installs use the local cache.

## Exact, cached node_modules snapshots

This tool isn't only for use with Node, but this is a good motivating use case.

If you `instaclone publish` after you `npm install` and `npm shrinkwrap`, you can switch back and forth between Git branches and run `instaclone install` instantly instead of `npm install` and waiting around minutes for npm to download, copy dependencies, etc.
Your colleagues can do this too -- after you publish, they can run `instaclone install` and get a byte-for-byte exact copy of your `node_modules` on their machines, more quickly and reliably than if they had done the `npm install` themselves.
Finally, your CI builds will speed up most of the time -- possibly by a lot!

[See below](#why-you-should-instaclone-node_modules) for more info on this.

## Features

- **Scales to large directories.** Works with large directories containing many (100,000+) files. Uses rsync to make file copying and deletion very fast.
- **Configurable storage.** Upload/download is via configurable shell commands, using whatever backing storage system desired, so you don't have to worry about configuring credentials just for this tool, and can publish to S3 or elsewhere.
- **High bandwidth upload/download.** While not a feature of Instaclone, I recommend using [`s4cmd`](https://github.com/bloomreach/s4cmd) for high-performance multi-connection access to S3.
- **Configurable versioning.** Version strings can be explicit or specified indirectly:
  - Explicit (you just say what version to use in the config file);
  - SHA1 of a file (you say another file that is hashed to get a unique string); or
  - Command (you have Instaclone execute an arbitrary command, like `uname`, which means you automatically publish different versions per platform)
- **Good hygiene.** All files, directories, and archives are created atomically, so that interruptions exceptions never leave files in a partially complete state.
- **Read-only or writeable installs.** You can install items as symlinks to the read-only cache (usually what you want), or fully copy all the files (in case you want to modify them). In the latter case, we use rsync for faster copying and to speed up repeat installs of large directories that haven't changed a lot in content.
- **Simple internals.** The format for the cache and published storage is dead simple.
  - The files are uploaded under unique paths with the version string as a suffix.
  - Files are cached locallyin `~/.instaclone`, but you can set the `INSTACLONE_DIR` environment variable to set this directory to something else.
  - The file cache just merges the published paths, so is just a file tree that you can look at. (Currently, clean-up is not automated, but you can delete it any time.)
- **Symlink details.** Symlink installs and directories containing symlinks work pretty well:
   - The file permissions on items in the cache is read-only, so that if you inadvertently try to modify the contents of the cache by following the symlink and changing a file, it will fail.
   - The target of the symlink (in the cache) has the same name as the source, so installed symlinks will play nice paths like `../target/foo` (where `target` is the symlink).
   - Internally within an archive, relative symlinks are preserved. But instaclone is smart enough to check for and abort if it sees symlinks to absolute paths or to relative paths outside the source directory (which would usually be a mistake).

## Installation

Requires Python 2.7+ on Mac or Linux. (Windows untested and probably broken.)
Then (with sudo if desired):

```
pip install instaclone
```

It requires `rsync` for faster file operations, as well as `s3cmd`, `aws`, `s4cmd`,
or any similar tool you put into your `upload_command` and `download_command` settings.
These must be in your path.

## Configuration

Instaclone requires two things to run:
- A config file, which can be called `instaclone.yml` or `instaclone.json` in the current directory. (YAML or JSON syntax is fine.) This configuration file says how resources will be published and installed.
- A main directory to store the cache in, which defaults to `$HOME/.instaclone` but can be overridden by the `INSTACLONE_DIR` environment variable. If you want, you can put a global `instaclone.{yml,json}` file there instead.

As an example, here is a marginally self-explanatory `instaclone.yml` configuration, which you would drop anywhere you want and probably should check into Git. You'd create as many files like this as desired in different directories, taking care you give them distinct `remote_path`s are unique.

```yml
---
# You can have as many items as you like and all will be installed.
# You'll want to git-ignore the local_paths below.
items:
  # A big file lives in this directory. It takes a while to generate, so we're going to
  # reference it in this file by version, instaclone publish, and anyone can
  # instaclone install it. We update the version string manually when we regenerate it.
  - local_path: my-big-and-occasionally-generated-resource.bin
    remote_prefix: s3://my-bucket/instaclone-resources
    remote_path: some/big-resources
    upload_command: s4cmd put -f $LOCAL $REMOTE
    download_command: s4cmd get $REMOTE $LOCAL
    # This is an explicitly set version of the file. It can be any string.
    version_string: 42a

  - local_path: node_modules
    remote_prefix: s3://my-bucket/instaclone-resources
    remote_path: my-app/node-stuff
    upload_command: s4cmd put -f $LOCAL $REMOTE
    download_command: s4cmd get $REMOTE $LOCAL
    # We generate the version string as a hash of the npm-shrinkwrap.json plus the architecture we're on:
    version_hashable: npm-shrinkwrap.json
    version_command: uname
```

See below for more on the `node_modules` one.

## Usage

Once Instaclone is configured, run:

- `instaclone publish`: upload configured items (and add to cache)
- `instaclone install`: download configured items (and add to cache)
- `instaclone configs`: sanity check configuration
- `instaclone purge`: delete entire cache (published resources are never deleted)
- `instaclone remote`: prints the current remote location to standard output

Run `instaclone --help` for a complete list of flags and settings.

If you have multiple items defined in the `instaclone.yml` file, you can list them as arguments to
`instaclone publish` or `instaclone install`, e.g. `instaclone install node_modules`.

Finally, note that by default, installations are done with a symlink,
but this can be customized in the config file to copy files.
As a shortcut, if you run `instaclone install --copy`,
it will perform a fast rsync-based copy of the files.
You should use the `--copy` option if you plan to modify the files after installation.

## Why you should Instaclone node_modules

This use case deserves a little more explanation.

While npm is amazingly convenient during development, managing the workflow around `npm install` can be a pain point in terms of speed, reliability, and reproducibility as you scale out builds and in production:

- As [we](http://blog.nodejs.org/2012/02/27/managing-node-js-dependencies-with-shrinkwrap/)
[all](http://javascript.tutorialhorizon.com/2015/03/21/what-is-npm-shrinkwrap-and-when-is-it-needed/)
[know](http://tilomitra.com/why-you-should-use-npm-shrinkwrap/),
the state of the `node_modules` is not inherently reproducible from the `package.json` file, so you should use [`npm shrinkwrap`](https://docs.npmjs.com/cli/shrinkwrap).
- While `npm shrinkwrap` mostly locks down exact package versions, even this *doesn't guarantee byte-for-byte repeatable installations*. Think about it: Reliability requires controlling change. If you change some single piece of code somewhere unrelated to your dependencies, and your build system reruns `npm install`, what if one of your hundreds of packages was unpublished, or you have an issue connecting to npmjs.org? In addition, shrinkwrap [doesn't prevent churn in peer dependencies](https://github.com/npm/npm/issues/5135). It's impossible to ensure exact repeatability unless you just make an exact copy and use it everywhere.
- Operationally, you also want a more scalable solution to distributing packages than hitting npmjs.org every time. You don't want lots of servers or build machines doing this continuously.
- You can set up a [local npm repository](https://www.npmjs.com/package/sinopia) or a [local npm cache server](https://github.com/mixu/npm_lazy) to help, but this is more infrastructure for devops to maintain and scale. And incidentally, it also is likely to be a single point of failure: Not being able to push new builds reliably is a Bad Thing (precisely when you don't need it). Plus, if you use [private modules](https://www.npmjs.com/private-modules) and pay npm, Inc. to host private code for you, you probably don't otherwise need another local repository.
- Finally, downloading from the global server and even installing from the local cache, take a lot of time, e.g if you want to do rapid CI builds from a clean install. With all these solutions, `npm install` still takes minutes for large projects *even when you haven't changed anything*.

A simpler and more scalable solution to this is to archive the entire `node_modules` directory, and put it somewhere reliable, like S3. But it can be large and slow to manage if it's always published and then fetched every time you need it. It's also a headache to script, especially in a continuous integration environment, where you want to re-install fresh on builds on all branches, every few minutes, and reinstall *only* when the checked-in `npm-shrinkwrap.json` file changes. Oh, and also the builds are platform-dependent, so you need to publish separately on MacOS and Linux.

Instaclone does all this for you. If you already have an `npm shrinkwrap` workflow, it's pretty easy. It lets you specify where to store your `node_modules` in S3, and version that entire tree by the SHA1 hash of the `npm-shrinkwrap.json` file togetehr with the architecture. You can then work on multiple branches and swap them in and out -- a bit like how `nvm` caches Node installations.

Copy and edit [the example config file](examples/npm-install/instaclone.yml) to try it. On your CI system, you might want to have some sort of automation that tries to reuse pre-published versions, but if not, publishes automatically:
```sh
    echo "Running instaclone install or publish..."
    instaclone install || (rm -rf ./node_modules && npm install && instaclone publish)
```

Note that in normal scenarios, the installed files are symlinked to the read-only cache.
If you want to `npm install` after doing an `instaclone install`, use
`instaclone install --copy` instead, and all files will be copied instead.

## Maturity

Mostly a one-day hack, but it should now be fairly workable.
It performs well in at least one continuous build environment with quite large directories synced regularly on Mac and Linux.

## Caveats

- There is no `unpublish` functionality -- if you publish something by mistake, go find it in S3 (or wherever you put it) and delete it.
- You have to clean up the cache manually by running `instaclone purge` or deleting files in `~/.instaclone/cache` -- there's no automated process for this yet, so if you publish a lot it will begin to accumulate.
- If you are obsessed with Node, you'll somehow have to accept that this is written in Python.
- See [issues](issues) and [the TODOs list](instaclone/instaclone.py) for further work.

## Running tests

Tests require `s4cmd`:

```
$ TEST_BUCKET=my-s3-bucket tests/run.sh
```

This is a bash-based harness that runs the test script at `tests/tests.sh`. Its output can then be `git diff`ed with the previous output.

## Contributing

Yes, please! File issues for bugs or general discussion. PRs welcome as well -- just figure out how to run the tests and document any other testing that's been done.

## License

Apache 2.
