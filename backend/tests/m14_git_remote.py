"""A local git remote for the M14 tests: bare repositories served over real smart HTTP.

``GitRemote`` runs a ``wsgiref`` server in a thread that wraps ``git http-backend`` (the CGI that
ships with git), so ``git clone`` / ``fetch`` / ``push`` in the code under test talk to a real git
server over HTTP, with a Basic-auth check of ``x-access-token:<token>``:

* no / wrong token -> 401 with ``WWW-Authenticate`` (git then asks the askpass script);
* a read-only token -> 403 on ``git-receive-pack`` (both the advertisement and the POST);
* ``receive.denyNonFastForwards`` is on, so a non-fast-forward push is rejected by the server;
* every request is recorded (``requests``) with its raw ``Authorization`` header, so a test can
  prove the token only ever travelled over HTTP (never argv, never a file).

Nothing here touches the network beyond ``127.0.0.1``. The server also exposes plain-git helpers
(``seed``, ``commit``, ``merge_tree`` ...) that the fake GitHub API and the tests use to act as a
second developer working on the remote.
"""

from __future__ import annotations

import base64
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

GIT = shutil.which("git")

# What a token may do on the remote.
READ = "read"
WRITE = "write"

_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "GIT_AUTHOR_NAME": "Human Dev",
    "GIT_AUTHOR_EMAIL": "human@example.test",
    "GIT_COMMITTER_NAME": "Human Dev",
    "GIT_COMMITTER_EMAIL": "human@example.test",
}


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", **_ENV}
    env.update(extra or {})
    return env


def run_git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [GIT or "git", *args],
        cwd=str(cwd) if cwd else None,
        env=git_env(env),
        input=input,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr}")
    return proc


@dataclass
class RecordedRequest:
    method: str
    path: str  # e.g. /acme/app.git/info/refs
    query: str
    authorization: str | None  # the raw header ("Basic ...") or None
    user: str | None  # decoded Basic user
    token: str | None  # decoded Basic password
    status: int = 0
    service: str | None = None  # git-upload-pack | git-receive-pack


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - wsgiref's signature
        return


def _parse_cgi(raw: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    if not _:
        head, _, body = raw.partition(b"\n\n")
    status = 200
    headers: list[tuple[str, str]] = []
    for line in head.decode("latin-1").splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        value = value.strip()
        if name.lower() == "status":
            status = int(value.split()[0])
        else:
            headers.append((name.strip(), value))
    return status, headers, body


class GitRemote:
    """Bare repositories under ``root`` served at ``self.url`` (``http://127.0.0.1:<port>``).

    Repositories live at ``<root>/<owner>/<name>.git``; the clone URL is
    ``<url>/<owner>/<name>.git``. Tokens: ``add_token(token, WRITE | READ)``. A fake GitHub can
    replace ``authorizer`` so that one token table decides both the API and git."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.tokens: dict[str, str] = {}
        self.requests: list[RecordedRequest] = []
        self.authorizer: Callable[[str | None, str, bool], int] = self._own_authorizer
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.port = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> GitRemote:
        if GIT is None:
            raise RuntimeError("git is not installed")
        self._server = make_server(
            "127.0.0.1", 0, self._app, server_class=_Server, handler_class=_Quiet
        )
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> GitRemote:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    def clone_url(self, full_name: str) -> str:
        return f"{self.url}/{full_name}.git"

    # ------------------------------------------------------------ tokens and auth

    def add_token(self, token: str, perm: str = WRITE) -> str:
        self.tokens[token] = perm
        return token

    def _own_authorizer(self, token: str | None, full_name: str, write: bool) -> int:
        perm = self.tokens.get(token or "")
        if perm is None:
            return 401
        if write and perm != WRITE:
            return 403
        return 200

    # ------------------------------------------------------------ repositories

    def repo_path(self, full_name: str) -> Path:
        return self.root / f"{full_name}.git"

    def exists(self, full_name: str) -> bool:
        return self.repo_path(full_name).is_dir()

    def create_repo(
        self,
        full_name: str,
        *,
        default_branch: str = "main",
        files: dict[str, str] | None = None,
        deny_non_ff: bool = True,
    ) -> Path:
        path = self.repo_path(full_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        run_git("init", "--bare", "-b", default_branch, str(path))
        run_git(
            "config", "receive.denyNonFastForwards", "true" if deny_non_ff else "false", cwd=path
        )
        run_git("config", "receive.denyDeletes", "false", cwd=path)
        run_git("config", "http.receivepack", "true", cwd=path)
        run_git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=path)
        if files:
            self.seed(full_name, files, branch=default_branch)
        return path

    def set_deny_non_ff(self, full_name: str, value: bool) -> None:
        run_git(
            "config",
            "receive.denyNonFastForwards",
            "true" if value else "false",
            cwd=self.repo_path(full_name),
        )

    def seed(
        self,
        full_name: str,
        files: dict[str, str],
        *,
        branch: str = "main",
        message: str = "Initial commit",
        author: str = "Human Dev <human@example.test>",
    ) -> str:
        """Commit ``files`` as the first (or next) commit of ``branch`` and return its SHA."""
        return self.commit(full_name, branch, files, message=message, author=author)

    def commit(
        self,
        full_name: str,
        branch: str,
        files: dict[str, str | None],
        *,
        message: str = "Human change",
        author: str = "Human Dev <human@example.test>",
        parent_branch: str | None = None,
    ) -> str:
        """A developer's commit straight onto the remote: clone, change, commit, push. ``None``
        as content deletes the file. ``parent_branch`` starts a new ``branch`` from that one."""
        bare = self.repo_path(full_name)
        name, email = _split_author(author)
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        work = Path(tempfile.mkdtemp(prefix="m14-dev-"))
        try:
            run_git("init", "-q", str(work), env=env)
            source = parent_branch or branch
            has_source = (
                run_git(
                    "rev-parse", "--verify", "-q", f"refs/heads/{source}", cwd=bare, check=False
                ).returncode
                == 0
            )
            if has_source:
                run_git("fetch", "-q", str(bare), f"refs/heads/{source}", cwd=work, env=env)
                run_git("checkout", "-q", "-B", branch, "FETCH_HEAD", cwd=work, env=env)
            else:
                run_git("checkout", "-q", "-B", branch, cwd=work, env=env)
            for rel, content in files.items():
                target = work / rel
                if content is None:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            run_git("add", "-A", cwd=work, env=env)
            run_git("commit", "-q", "--allow-empty", "-m", message, cwd=work, env=env)
            sha = run_git("rev-parse", "HEAD", cwd=work, env=env).stdout.strip()
            run_git("push", "-q", str(bare), f"HEAD:refs/heads/{branch}", cwd=work, env=env)
            return sha
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def branch_sha(self, full_name: str, branch: str) -> str | None:
        proc = run_git(
            "rev-parse",
            "--verify",
            "-q",
            f"refs/heads/{branch}",
            cwd=self.repo_path(full_name),
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def branches(self, full_name: str) -> dict[str, str]:
        out = run_git(
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/heads",
            cwd=self.repo_path(full_name),
        ).stdout
        return dict(line.split(" ", 1) for line in out.splitlines() if line)

    def is_empty(self, full_name: str) -> bool:
        return not self.branches(full_name)

    def read_file(self, full_name: str, ref: str, path: str) -> str | None:
        proc = run_git("show", f"{ref}:{path}", cwd=self.repo_path(full_name), check=False)
        return proc.stdout if proc.returncode == 0 else None

    def path_exists(self, full_name: str, ref: str, path: str) -> bool:
        proc = run_git(
            "cat-file", "-e", f"{ref}:{path}", cwd=self.repo_path(full_name), check=False
        )
        return proc.returncode == 0

    def log(self, full_name: str, ref: str, *, fmt: str = "%H %s", n: int = 50) -> list[str]:
        proc = run_git(
            "log", f"--format={fmt}", f"-{n}", ref, cwd=self.repo_path(full_name), check=False
        )
        return proc.stdout.splitlines() if proc.returncode == 0 else []

    def is_ancestor(self, full_name: str, ancestor: str, descendant: str) -> bool:
        proc = run_git(
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            cwd=self.repo_path(full_name),
            check=False,
        )
        return proc.returncode == 0

    def commit_exists(self, full_name: str, sha: str) -> bool:
        proc = run_git(
            "cat-file", "-e", f"{sha}^{{commit}}", cwd=self.repo_path(full_name), check=False
        )
        return proc.returncode == 0

    def commit_date(self, full_name: str, sha: str) -> str:
        """The committer date in UTC, ISO-8601 with a trailing Z."""
        out = run_git(
            "show", "-s", "--format=%ct", sha, cwd=self.repo_path(full_name)
        ).stdout.strip()
        from datetime import UTC, datetime

        return datetime.fromtimestamp(int(out), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def delete_branch(self, full_name: str, branch: str) -> bool:
        proc = run_git(
            "update-ref", "-d", f"refs/heads/{branch}", cwd=self.repo_path(full_name), check=False
        )
        return proc.returncode == 0

    def set_branch(self, full_name: str, branch: str, sha: str) -> None:
        run_git("update-ref", f"refs/heads/{branch}", sha, cwd=self.repo_path(full_name))

    def rewrite_branch(self, full_name: str, branch: str) -> str:
        """A force-push by a human: replace the branch with a fresh, unrelated root commit."""
        bare = self.repo_path(full_name)
        tree = run_git(
            "hash-object", "-t", "tree", "-w", "--stdin", cwd=bare, input=""
        ).stdout.strip()
        sha = run_git("commit-tree", tree, "-m", "Rewritten history", cwd=bare).stdout.strip()
        self.set_branch(full_name, branch, sha)
        return sha

    # ------------------------------------------------------------ merge plumbing (fake GitHub)

    def merge_tree(self, full_name: str, base: str, head: str) -> tuple[bool, str]:
        """``(clean, tree_or_conflict_report)`` of merging ``head`` into ``base`` (no commit)."""
        proc = run_git(
            "merge-tree",
            "--write-tree",
            "--name-only",
            base,
            head,
            cwd=self.repo_path(full_name),
            check=False,
        )
        out = proc.stdout.strip().splitlines()
        if proc.returncode == 0:
            return True, out[0] if out else ""
        return False, "\n".join(out[1:]) if out else proc.stderr.strip()

    def commit_tree(
        self, full_name: str, tree: str, parents: Iterable[str], message: str, author: str
    ) -> str:
        name, email = _split_author(author)
        args = ["commit-tree", tree, "-m", message]
        for parent in parents:
            args += ["-p", parent]
        return run_git(
            *args,
            cwd=self.repo_path(full_name),
            env={
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            },
        ).stdout.strip()

    def update_ref(self, full_name: str, branch: str, new: str, old: str | None = None) -> None:
        args = ["update-ref", f"refs/heads/{branch}", new]
        if old:
            args.append(old)
        run_git(*args, cwd=self.repo_path(full_name))

    def ahead_behind(self, full_name: str, base: str, head: str) -> tuple[int, int]:
        out = run_git(
            "rev-list", "--left-right", "--count", f"{base}...{head}", cwd=self.repo_path(full_name)
        ).stdout.split()
        return int(out[1]), int(out[0])  # (head ahead of base, head behind base)

    # ------------------------------------------------------------ recorded traffic

    def tokens_seen(self) -> set[str]:
        return {r.token for r in self.requests if r.token}

    def requests_for(self, service: str | None = None) -> list[RecordedRequest]:
        return [r for r in self.requests if service is None or r.service == service]

    # ------------------------------------------------------------ WSGI

    def _app(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")
        method = environ["REQUEST_METHOD"]
        auth_header = environ.get("HTTP_AUTHORIZATION")
        user = token = None
        if auth_header and auth_header.lower().startswith("basic "):
            with contextlib.suppress(Exception):
                decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
                user, _, token = decoded.partition(":")
        service = None
        match = re.search(r"service=(git-[a-z-]+)", query)
        if match:
            service = match.group(1)
        elif path.endswith(("/git-upload-pack", "/git-receive-pack")):
            service = path.rsplit("/", 1)[1]
        record = RecordedRequest(method, path, query, auth_header, user, token, service=service)
        with self._lock:
            self.requests.append(record)

        repo = re.match(r"^/([^/]+/[^/]+?)\.git(/.*)?$", path)
        if repo is None:
            return self._respond(start_response, record, 404, b"not found\n")
        full_name = repo.group(1)
        if not self.exists(full_name):
            return self._respond(start_response, record, 404, b"repository not found\n")
        write = service == "git-receive-pack"
        verdict = self.authorizer(token if user == "x-access-token" else None, full_name, write)
        if verdict == 401:
            return self._respond(
                start_response,
                record,
                401,
                b"authentication required\n",
                [("WWW-Authenticate", 'Basic realm="fake-github"')],
            )
        if verdict != 200:
            return self._respond(start_response, record, verdict, b"forbidden\n")

        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        cgi_env = git_env(
            {
                "GIT_PROJECT_ROOT": str(self.root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "REQUEST_METHOD": method,
                "QUERY_STRING": query,
                "CONTENT_TYPE": environ.get("CONTENT_TYPE", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "x-access-token",
                "REMOTE_ADDR": "127.0.0.1",
            }
        )
        if environ.get("HTTP_GIT_PROTOCOL"):
            cgi_env["GIT_PROTOCOL"] = environ["HTTP_GIT_PROTOCOL"]
        if environ.get("HTTP_CONTENT_ENCODING"):
            cgi_env["HTTP_CONTENT_ENCODING"] = environ["HTTP_CONTENT_ENCODING"]
        proc = subprocess.run(
            [GIT or "git", "http-backend"], input=body, env=cgi_env, capture_output=True
        )
        status, headers, out = _parse_cgi(proc.stdout)
        return self._respond(start_response, record, status, out, headers)

    @staticmethod
    def _respond(start_response, record: RecordedRequest, status: int, body: bytes, headers=None):
        record.status = status
        reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found"}.get(
            status, "Status"
        )
        out_headers = list(headers or [])
        if not any(h[0].lower() == "content-length" for h in out_headers):
            out_headers.append(("Content-Length", str(len(body))))
        start_response(f"{status} {reason}", out_headers)
        return [body]


def _split_author(author: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)\s*<([^>]*)>$", author)
    return (match.group(1), match.group(2)) if match else (author, "dev@example.test")


@dataclass
class RemoteFiles:
    """Convenience: the files of a repository at a ref (for assertions)."""

    remote: GitRemote
    full_name: str
    ref: str = "main"
    cache: dict[str, str | None] = field(default_factory=dict)

    def __getitem__(self, path: str) -> str | None:
        return self.remote.read_file(self.full_name, self.ref, path)
