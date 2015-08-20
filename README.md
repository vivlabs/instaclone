# Instaclone

Instaclone is a simple, configurable command-line tool to publish and then later re-install files or directories, using S3 or other services as a backing store, and keeping local caches to optimize re-installation speed for repeated installations.

To be more concrete, think of it as a way to publish and install files to and from S3, while also maintaining a local cache so download is essentially instant when doing it a second time. But with some additional abilities to customize.

It's good for files you want to version but not check into git (due to size, sensitivity, platform-dependence, etc.). You git-ignore them and install them with Instaclone, and instead version the Instaclone configuration that references them.

In particular, it helps fix a pain point with `npm install` and `npm shrinkwrap` slowness on build systems. If you use this with shrinkwrap, you can switch back and forth between branches and run `instaclone install` instantly instead of `npm install` and waiting 3 minutes. Or your colleage can pull, run `instaclone install` and get a byte-for-byte exact copy of your `node_modules`. (See below for more on this.)

## Goals

- Support for publishing and installing large files and directory trees of many files.
- Manage multiple immutable versions of each file/directory.
- Local caching of items, so it's very fast to re-install a previously installed item (a single symlink).
- Configurability. External authentication and transport, using whatever backing storage system desired (so you don't have to worry about configuring credentials just for this tool, and can publish to S3 or elsewhere).
- Simplicity.

## Features

- Configurable to use [`s4cmd`](https://github.com/bloomreach/s4cmd) or any other command-line tool to upload and download files to wherever you want in S3.
- Configurable ways to manage versions, including one or more of:
  - Explicit (you the version to install)
  - Hashed (you say what file to hash to get the version id)
  - Command (you have Instaclone execute an arbitrary command, like `uname`, which means you can have different versions per platform type automatically)
- Simple, clean internal format. The file cache is just a simple file tree that you can look at and clean up as you wish. Directories are archived as .zip files.
- Good hygiene: All files, directories, and archives are created atomically, so that interruptions or problems never leave files in a partially complete state.
- You can install items as symlinks (files or directories), hardlinks (files only), or fully copy (a little slower but still better than a download).
- For symlink installs, one convenient detail is that the target of the symlink (in the cache) has the same name as the source, so symlinks still play nice with `..` paths like `../target/foo`.

## Example

Here is a marginally self-explanatory `instaclone.yml` configuration, which you drop
anywhere you want and probably check into Git. You'd create as many files like this as
you want in different directories, just making sure `remote_path`s are unique.

```yml
---
items:
  #  big file lives in this directory. It takes a while to generate, so we're going to
  # reference it here by version, and update the version manually when we regenerate.
  - local_path: my-big-and-occasionally-generated-resource.bin
    remote_path: some/big-resources
    remote_prefix: s3://my-bucket/instaclone-resources
    version: 4
    upload_command: s4cmd put -f $LOCAL $REMOTE
    download_command: s4cmd get $REMOTE $LOCAL
    copy_type: symlink
  - local_path: node_modules
    remote_path: my-app/node-stuff
    remote_prefix: s3://my-bucket/instaclone-resources
    # We generate the version as a hash of the npm-shrinkwrap.json plus the architecture we're on:
    version_hashable: npm-shrinkwrap.json
    version_command: uname
    upload_command: s4cmd put -f $LOCAL $REMOTE
    download_command: s4cmd get $REMOTE $LOCAL
    copy_type: symlink
```

See below for more on the `node_modules` one.

## Maturity

One-day hack. It works, but still under development.

## Installation

```
pip install instaclone
```

It also requires some tools in your path:

- `zip` and `unzip`
- `s3cmd`, `aws`, `s4cmd`, or any similar tool you put into your
  `upload_command` and `download_command` settings

## Configuration

Put an `instaclone.yml` file in the directory where you want resources cached. This configuration file says how those will be published and installed.

## Example usages

### External files or directory trees in source control

Put large files needed in a source tree or build in S3, but track changes in source control. The version of a file can be explicitly managed, or can be computed as a SHA1 hash. Then switch rapidly between versions, since previously used versions of files are stored locally.

### Fast, exact `npm install`

This use case takes a little explanation. Having fast and reproducible runs of `npm install` is a challenge:

- [As](https://docs.npmjs.com/cli/shrinkwrap)
  [we](http://blog.nodejs.org/2012/02/27/managing-node-js-dependencies-with-shrinkwrap/)
  [all](http://javascript.tutorialhorizon.com/2015/03/21/what-is-npm-shrinkwrap-and-when-is-it-needed/)
  [know](http://tilomitra.com/why-you-should-use-npm-shrinkwrap/),
  the state of the `node_modules` is not inherently reproducible from the `package.json` file.
- Using `npm shrinkwrap` helps lock down exact package versions, but even this doesn't completely guarantee byte-for-byte repeatable installations if you're relying on the npm.org server.
- Downloading from the global server, and even installing from the local cache, take a lot of time, e.g if you want to do rapid CI builds from a clean install.
- Operationally, you may also want a more scalable solution to distributing packages than npm.org or even your own npm or cache server (which, incidentally, is likely to be a single point of failure).

One solution is to archive the entire `node_modules` directory and put it somewhere reliable, like S3. But this can be slow if it's always published and then fetched every time you need it. It's also a headache to script. And it's doubly inconvenient in a continuous integration environment, where you want to re-install fresh on builds on all branches, every few minutes, and reinstall *only* when the checked-in `npm-shrinkwrap.json` file changes.

Instaclone works well with `npm shrinkwrap`. It lets you specify where to store your `node_modules` in S3, and version that entire tree by the SHA1 hash of the `npm-shrinkwrap.json` file. You can then work on multiple branches and swap them in and out (a bit like how `nvm` caches Node installations).

See above for sample configs.

## Contributing

Yes, please! File issues or open PRs.
