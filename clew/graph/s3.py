"""
A small S3 client on the standard library: list, head, stream, delete.

Enough to treat a bucket prefix as a tree of task directories. Signature
Version 4 is a few dozen lines of hmac, and writing them keeps Clew's
base install dependency-free, which is the promise the README makes.
Credentials come from the environment or ~/.aws, the same places the AWS
CLI reads, so nothing new has to be configured.
"""

import base64
import configparser
import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
PAGE = 1000


def credentials(profile=None):
    """(key id, secret, session token or None), environment first, then ~/.aws/credentials."""
    key, secret = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
    if key and secret:
        return key, secret, os.environ.get("AWS_SESSION_TOKEN")
    profile = profile or os.environ.get("AWS_PROFILE", "default")
    ini = configparser.ConfigParser()
    ini.read(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials"))
    if ini.has_section(profile):
        section = ini[profile]
        if section.get("aws_access_key_id") and section.get("aws_secret_access_key"):
            return (section["aws_access_key_id"], section["aws_secret_access_key"],
                    section.get("aws_session_token"))
    raise SystemExit("no AWS credentials: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, "
                     f"or put profile {profile!r} in ~/.aws/credentials")


def region(profile=None):
    """The region from the environment, then ~/.aws/config, then us-east-1."""
    found = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if found:
        return found
    profile = profile or os.environ.get("AWS_PROFILE", "default")
    ini = configparser.ConfigParser()
    ini.read(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config"))
    section = profile if profile == "default" else f"profile {profile}"
    return ini.get(section, "region", fallback="us-east-1")


def _hmac(key, message):
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class S3:
    """One bucket. `endpoint` (or AWS_ENDPOINT_URL) switches to path-style addressing, for MinIO and tests."""

    def __init__(self, bucket, endpoint=None, region_name=None, creds=None):
        self.bucket = bucket
        self.endpoint = (endpoint or os.environ.get("AWS_ENDPOINT_URL_S3")
                         or os.environ.get("AWS_ENDPOINT_URL") or "").rstrip("/")
        self.region = region_name or region()
        self._creds = creds

    def url(self, key="", query=""):
        base = (f"{self.endpoint}/{self.bucket}" if self.endpoint
                else f"https://{self.bucket}.s3.{self.region}.amazonaws.com")
        return base + "/" + urllib.parse.quote(key, safe="/-_.~") + (f"?{query}" if query else "")

    def request(self, method, key="", query="", body=b"", headers=None):
        """One signed request. Raises urllib.error.HTTPError as the server answered."""
        if self._creds is None:
            self._creds = credentials()
        key_id, secret, token = self._creds
        url = self.url(key, query)
        parts = urllib.parse.urlsplit(url)
        now = datetime.now(timezone.utc)
        amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload = hashlib.sha256(body).hexdigest()
        signed_headers = {"host": parts.netloc, "x-amz-content-sha256": payload,
                          "x-amz-date": amz_date}
        if token:
            signed_headers["x-amz-security-token"] = token
        signed_headers.update({k.lower(): v for k, v in (headers or {}).items()})
        names = ";".join(sorted(signed_headers))
        canonical_query = "&".join(
            sorted(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                   for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
        canonical = "\n".join([
            method, parts.path or "/", canonical_query,
            "".join(f"{k}:{signed_headers[k].strip()}\n" for k in sorted(signed_headers)),
            names, payload])
        scope = f"{date}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        signing_key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), date),
                                        self.region), "s3"), "aws4_request")
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        signed_headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
                                           f"SignedHeaders={names}, Signature={signature}")
        request = urllib.request.Request(
            url, data=body or None, method=method,
            headers={k: v for k, v in signed_headers.items() if k != "host"})
        return urllib.request.urlopen(request)

    def list(self, prefix, limit=None):
        """[(key, size)] under prefix, every page unless `limit` caps it."""
        found, token = [], None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if limit:
                query["max-keys"] = str(min(limit, PAGE))
            if token:
                query["continuation-token"] = token
            with self.request("GET", "", urllib.parse.urlencode(query)) as response:
                tree = ElementTree.fromstring(response.read())
            for entry in tree.findall(NS + "Contents"):
                found.append((entry.findtext(NS + "Key"), int(entry.findtext(NS + "Size") or 0)))
            if tree.findtext(NS + "IsTruncated") != "true" or (limit and len(found) >= limit):
                return found
            token = tree.findtext(NS + "NextContinuationToken")

    def head(self, key):
        """The object's size, or None when there is no such key."""
        try:
            with self.request("HEAD", key) as response:
                return int(response.headers["Content-Length"])
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def sha256(self, key):
        """The object's content hash, streamed. There is no shortcut: S3 stores an ETag, not a digest."""
        digest = hashlib.sha256()
        with self.request("GET", key) as response:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def delete(self, keys):
        """Remove every key, a thousand per request. Any key the bucket refused raises."""
        for start in range(0, len(keys), PAGE):
            batch = keys[start:start + PAGE]
            body = ("<Delete><Quiet>true</Quiet>"
                    + "".join(f"<Object><Key>{escape(k)}</Key></Object>" for k in batch)
                    + "</Delete>").encode()
            md5 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
            with self.request("POST", "", "delete", body,
                              {"Content-MD5": md5, "Content-Type": "application/xml"}) as response:
                tree = ElementTree.fromstring(response.read() or b"<DeleteResult/>")
            refused = [(e.findtext(NS + "Key"), e.findtext(NS + "Message"))
                       for e in tree.findall(NS + "Error")]
            if refused:
                raise OSError("S3 refused to delete " + "; ".join(f"{k}: {m}" for k, m in refused))
