"""
Where a run's bytes live: a local tree or an S3 prefix, behind one surface.

A question about storage is the same on either: is this task's directory
still there, how big is it, is this published copy the recorded size, what
does it hash to, remove it. Only the proof differs. A hardlink or symlink
between work and results exists on a filesystem and not in a bucket, so
the bucket answers None to that question and reclaim withholds nothing on
its account.
"""

import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class LocalTree:
    def __init__(self, root):
        self.root = Path(root)

    def describe(self, rel=""):
        """The path as a person and the receipt see it."""
        return str(self.root / rel) if rel else str(self.root)

    def path(self, rel):
        return self.root / rel

    def is_dir(self, rel):
        return (self.root / rel).is_dir()

    def is_file(self, rel):
        return (self.root / rel).is_file()

    def size(self, rel):
        try:
            return (self.root / rel).stat().st_size
        except OSError:
            return None

    def dir_bytes(self, rel):
        """
        Bytes freed by removing the directory: regular files with one link.
        Symlinks are staged inputs, and a file with another hard link
        survives the removal, so neither gives anything back.
        """
        total = 0
        for child in (self.root / rel).rglob("*"):
            if child.is_symlink() or not child.is_file():
                continue
            stat = child.lstat()
            if stat.st_nlink == 1:
                total += stat.st_size
        return total

    def sha256(self, rel):
        return sha256_path(self.root / rel)

    def link_kind(self, rel, other, other_rel):
        """
        'symlink' when the other tree's file points at this one, 'hardlink'
        when they are one inode, else None. Only two local trees can be
        linked at all.
        """
        if not isinstance(other, LocalTree):
            return None
        work, published = self.root / rel, other.root / other_rel
        try:
            if published.is_symlink():
                return "symlink" if published.resolve() == work.resolve() else None
            a, b = work.stat(), published.stat()
            return "hardlink" if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) else None
        except OSError:
            return None

    def remove_dir(self, rel):
        shutil.rmtree(self.root / rel)

    def inside(self, described):
        """The relative directory a described path names under this root, or None if it is not under it."""
        root, target = self.root.resolve(), Path(described).resolve()
        if target == root or root not in target.parents:
            return None
        return str(target.relative_to(root))


class S3Tree:
    """A bucket prefix. A task directory is every object under its key prefix."""

    def __init__(self, url, client=None):
        parts = urlsplit(url)
        self.bucket, self.prefix = parts.netloc, parts.path.strip("/")
        if not self.bucket:
            raise SystemExit(f"{url}: an S3 root needs a bucket: s3://bucket/prefix")
        if client is None:
            from clew.graph.s3 import S3
            client = S3(self.bucket)
        self.client = client
        self._objects, self._heads = {}, {}

    def key(self, rel=""):
        return "/".join(part for part in (self.prefix, rel.strip("/")) if part)

    def describe(self, rel=""):
        return f"s3://{self.bucket}/{self.key(rel)}"

    def objects(self, rel):
        """[(key, size)] under the directory, listed once."""
        if rel not in self._objects:
            self._objects[rel] = self.client.list(self.key(rel) + "/")
        return self._objects[rel]

    def head(self, rel):
        if rel not in self._heads:
            self._heads[rel] = self.client.head(self.key(rel))
        return self._heads[rel]

    def is_dir(self, rel):
        return bool(self.objects(rel))

    def is_file(self, rel):
        return self.head(rel) is not None

    def size(self, rel):
        return self.head(rel)

    def dir_bytes(self, rel):
        return sum(size for _, size in self.objects(rel))

    def sha256(self, rel):
        return self.client.sha256(self.key(rel))

    def link_kind(self, rel, other, other_rel):
        return None

    def remove_dir(self, rel):
        self.client.delete([key for key, _ in self.objects(rel)])
        self._objects.pop(rel, None)

    def inside(self, described):
        root = self.describe() + "/"
        if not described.startswith(root):
            return None
        return described[len(root):].strip("/") or None


def open_tree(root):
    """A tree for a local directory or an s3:// URL; None for None."""
    if root is None:
        return None
    root = str(root)
    return S3Tree(root) if root.startswith("s3://") else LocalTree(root)
