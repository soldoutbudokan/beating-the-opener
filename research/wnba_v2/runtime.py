"""Run a frozen shadow candidate with append-only Git evidence.

The active checkout supplies reviewed code. A separate, explicitly authorized
repository supplies the frozen bundle and prospective ledger. This adapter has
no fitting, wagering, routine-management, or automatic evaluation interface.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile

from . import shadow


PREFIX = "data/wnba-shadow"
CONFIG = PREFIX + "/runtime.json"
STUDY = PREFIX + "/ledger/study.json"
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
REQUIRED_CODE = {"research/wnba_v2/" + name + ".py" for name in
                 ("runtime", "shadow", "capture", "outcomes", "future", "model", "sources")}
REQUIRED_CODE |= {"research/engine/model.py", "research/engine/store.py", "research/engine/sources.py",
                  "research/engine/requirements.txt", "research/clocks.py", "research/operations/collection.py",
                  "research/__init__.py", "research/engine/__init__.py", "wnba/src/live_pipeline.py"}


class RuntimeFailure(RuntimeError):
    """Messages are stable error codes; never include tokens or provider bodies."""


def safe_relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or "\0" in value
            or value.startswith("/") or any(x in ("", ".", "..") for x in value.split("/"))):
        raise RuntimeFailure("UNSAFE_RELATIVE_PATH")
    return value


def regular_tree(root):
    """Read regular files without following any symlink or special file."""
    root = Path(root)
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise RuntimeFailure("LOCAL_SYMLINK")
    if not root.exists():
        return {}
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeFailure("LOCAL_SPECIAL_FILE")
        for name in files:
            path = Path(parent) / name
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def document(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, TypeError):
        raise RuntimeFailure("INVALID_JSON") from None
    if not isinstance(value, dict):
        raise RuntimeFailure("JSON_OBJECT_REQUIRED")
    return value


def git(repo, *args, data=None, env=None, check=True):
    completed = subprocess.run(["git", "-C", str(repo), *args], input=data,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, **(env or {})})
    if check and completed.returncode:
        raise RuntimeFailure("GIT_" + args[0].upper().replace("-", "_") + "_FAILED")
    return completed


def _oid(raw):
    value = raw.decode("ascii").strip()
    if not SHA1.fullmatch(value):
        raise RuntimeFailure("INVALID_GIT_OBJECT_ID")
    return value


def verify_destination(repo, repository_full_name, *, allow_public=False, opener=urllib.request.urlopen):
    """Confirm the actual remote and privacy before any evidence can be pushed.

    allow_public only records a deployment choice. It does not grant permission
    to disclose evidence; that permission must already exist outside this code.
    """
    if not REPOSITORY.fullmatch(repository_full_name):
        raise RuntimeFailure("INVALID_REPOSITORY")
    remote = git(repo, "remote", "get-url", "origin").stdout.decode().strip()
    accepted = {f"https://github.com/{repository_full_name}",
                f"https://github.com/{repository_full_name}.git",
                f"git@github.com:{repository_full_name}.git"}
    if remote not in accepted:
        raise RuntimeFailure("UNEXPECTED_GIT_REMOTE")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeFailure("GITHUB_TOKEN_REQUIRED")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository_full_name,
        headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                 "User-Agent": "wnba-shadow-runtime", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=20) as response:
            metadata = document(response.read())
    except (OSError, urllib.error.URLError):
        raise RuntimeFailure("REPOSITORY_METADATA_UNAVAILABLE") from None
    if metadata.get("full_name") != repository_full_name or metadata.get("default_branch") != "main":
        raise RuntimeFailure("REPOSITORY_IDENTITY_OR_BRANCH_MISMATCH")
    if type(metadata.get("private")) is not bool:
        raise RuntimeFailure("REPOSITORY_VISIBILITY_UNKNOWN")
    if not metadata["private"] and not allow_public:
        raise RuntimeFailure("PUBLIC_EVIDENCE_NOT_AUTHORIZED")
    return metadata["private"]


class GitArchive:
    """Append immutable files using a temporary index and normal fast-forwards.

    Every retry starts from the newest remote tree. Existing paths must be byte
    identical; no caller can replace a ledger row or remove unrelated files.
    """
    def __init__(self, repo, repository_full_name, *, clock=shadow.utcnow, retries=3):
        self.repo = Path(repo)
        self.repository_full_name = repository_full_name
        self.clock = clock
        self.retries = retries
        self.controls = {}
        self.restored_head = None
        self.restored_entries = {}

    def fetch(self):
        git(self.repo, "fetch", "--no-tags", "origin", "refs/heads/main")
        head = _oid(git(self.repo, "rev-parse", "FETCH_HEAD").stdout)
        if self.restored_head is not None and git(
                self.repo, "merge-base", "--is-ancestor", self.restored_head, head, check=False).returncode:
            raise RuntimeFailure("REMOTE_HISTORY_REWRITTEN")
        return head

    def entries(self, commit):
        entries = {}
        raw = git(self.repo, "ls-tree", "-r", "-z", commit, "--", "data").stdout
        for item in raw.split(b"\0"):
            if not item:
                continue
            meta, name = item.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            path = safe_relative(name.decode("utf-8"))
            if path in ("data", PREFIX) or path.startswith(PREFIX + "/"):
                if mode != "100644" or kind != "blob":
                    raise RuntimeFailure("REMOTE_SPECIAL_FILE")
                entries[path] = oid
        return entries

    def read(self, oid):
        return git(self.repo, "cat-file", "blob", oid).stdout

    def restore(self, destination):
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeFailure("RESTORE_DESTINATION_NOT_EMPTY")
        head = self.fetch()
        entries = self.entries(head)
        for path, oid in entries.items():
            target = shadow._safe_path(destination, path)
            shadow.write_once(target, self.read(oid))
        if CONFIG not in entries or STUDY not in entries:
            raise RuntimeFailure("DEPLOYMENT_NOT_INITIALIZED")
        config = document(self.read(entries[CONFIG]))
        bundle = safe_relative(config.get("bundle_path"))
        if not bundle.startswith(PREFIX + "/frozen/") or bundle not in entries:
            raise RuntimeFailure("FROZEN_BUNDLE_MISSING")
        self.controls = {path: self.read(entries[path]) for path in (CONFIG, STUDY, bundle)}
        self.restored_head = head
        self.restored_entries = dict(entries)
        return head

    @staticmethod
    def permitted(path):
        safe_relative(path)
        return any(path.startswith(PREFIX + "/" + part + "/") for part in
                   ("ledger/batches", "ledger/settlements", "ledger/captures", "history", "runs"))

    def verify(self, commit, payload):
        entries = self.entries(commit)
        for path, raw in payload.items():
            if path not in entries or self.read(entries[path]) != raw:
                raise RuntimeFailure("REMOTE_BYTES_NOT_VERIFIED")

    def verify_receipt(self, receipt, payload, *, head=None):
        if (receipt.get("provider") != "github"
                or receipt.get("repository_full_name") != self.repository_full_name
                or not SHA1.fullmatch(receipt.get("commit_sha", ""))):
            raise RuntimeFailure("GIT_RECEIPT_IDENTITY_MISMATCH")
        head = self.fetch() if head is None else head
        commit = receipt["commit_sha"]
        if git(self.repo, "merge-base", "--is-ancestor", commit, head, check=False).returncode:
            raise RuntimeFailure("RECEIPT_COMMIT_NOT_REACHABLE")
        self.verify(commit, payload)
        artifact = receipt.get("artifact_path")
        if artifact not in payload or shadow.digest(payload[artifact]) != receipt.get("stored_artifact_sha256"):
            raise RuntimeFailure("RECEIPT_ARTIFACT_MISMATCH")

    def verify_ledger(self, ledger, *, head, now):
        """Verify every admitted historical batch against its real Git object."""
        for directory in sorted((ledger.root / "batches").glob("*")):
            if not (directory / "manifest.json").exists():
                continue
            manifest, _ = ledger.batch(directory.name, now)
            receipt_path = directory / "seal.json"
            if not receipt_path.exists():
                continue
            receipt = document(receipt_path.read_bytes())
            ledger._validate_seal(manifest, receipt, now)
            if receipt.get("provider") != "github":
                raise RuntimeFailure("NON_GIT_BATCH_RECEIPT_UNVERIFIABLE")
            prefix = PREFIX + "/ledger/batches/" + directory.name + "/"
            payload = {prefix + name: raw for name, raw in regular_tree(directory).items()
                       if name != "seal.json"}
            if receipt.get("artifact_path") != prefix + "manifest.json":
                raise RuntimeFailure("BATCH_RECEIPT_ARTIFACT_MISMATCH")
            self.verify_receipt(receipt, payload, head=head)

    def validate_merge(self, base, entries, payload):
        """Reject conflicting forecast identities and settlement revision forks."""
        if STUDY not in entries:
            return
        try:
            with tempfile.TemporaryDirectory(prefix="wnba-merged-ledger-") as directory:
                ledger_root = Path(directory) / "ledger"
                prefix = PREFIX + "/ledger/"
                included = {path: self.read(oid) for path, oid in entries.items()
                            if path.startswith(prefix) and not path.startswith(prefix + "captures/")}
                included.update({path: raw for path, raw in payload.items()
                                 if path.startswith(prefix) and not path.startswith(prefix + "captures/")})
                for path, raw in included.items():
                    shadow.write_once(shadow._safe_path(ledger_root, path[len(prefix):]), raw)
                ledger = shadow.Ledger(ledger_root)
                now = self.clock()
                self.verify_ledger(ledger, head=base, now=now)
                ledger.population(now)
                all_rows = {}
                for batch in sorted((ledger_root / "batches").glob("*")):
                    if (batch / "manifest.json").exists():
                        all_rows.update({row["forecast_id"]: row for row in ledger.batch(batch.name, now)[1]})
                ledger.settlements(all_rows, now)
        except (ValueError, KeyError, OSError) as error:
            raise RuntimeFailure("MERGED_LEDGER_CONFLICT") from error

    def append(self, payload, *, run_id):
        shadow.identifier(run_id, "run_id")
        if not payload or any(not self.permitted(path) or not isinstance(raw, bytes)
                              for path, raw in payload.items()):
            raise RuntimeFailure("APPEND_SCOPE_INVALID")
        payload = dict(payload)
        for attempt in range(self.retries):
            base = self.fetch()
            entries = self.entries(base)
            for path, raw in self.controls.items():
                if path not in entries or self.read(entries[path]) != raw:
                    raise RuntimeFailure("FROZEN_DEPLOYMENT_CHANGED")
            if any(entries.get(path) != oid for path, oid in self.restored_entries.items()):
                raise RuntimeFailure("RESTORED_EVIDENCE_CHANGED_OR_DELETED")
            additions = {}
            for path, raw in payload.items():
                if any(str(parent) in entries for parent in PurePosixPath(path).parents):
                    raise RuntimeFailure("REMOTE_PATH_COLLISION")
                if any(existing.startswith(path + "/") for existing in entries):
                    raise RuntimeFailure("REMOTE_PATH_COLLISION")
                if path in entries:
                    if self.read(entries[path]) != raw:
                        raise RuntimeFailure("IMMUTABLE_PATH_CONFLICT")
                else:
                    additions[path] = raw
            self.validate_merge(base, entries, payload)
            commit = base
            if additions:
                with tempfile.TemporaryDirectory(prefix="wnba-index-") as temp:
                    environment = {"GIT_INDEX_FILE": str(Path(temp) / "index"),
                                   "GIT_AUTHOR_NAME": "WNBA shadow research",
                                   "GIT_AUTHOR_EMAIL": "wnba-shadow@users.noreply.github.com",
                                   "GIT_COMMITTER_NAME": "WNBA shadow research",
                                   "GIT_COMMITTER_EMAIL": "wnba-shadow@users.noreply.github.com"}
                    git(self.repo, "read-tree", base, env=environment)
                    for path, raw in sorted(additions.items()):
                        blob = _oid(git(self.repo, "hash-object", "-w", "--stdin", data=raw).stdout)
                        git(self.repo, "update-index", "--add", "--cacheinfo", "100644", blob, path,
                            env=environment)
                    tree = _oid(git(self.repo, "write-tree", env=environment).stdout)
                    commit = _oid(git(self.repo, "commit-tree", tree, "-p", base,
                                      data=f"Record WNBA shadow run {run_id}\n".encode(),
                                      env=environment).stdout)
                pushed = git(self.repo, "push", "origin", commit + ":refs/heads/main", check=False)
                if pushed.returncode:
                    current = self.fetch()
                    # A normal concurrent fast-forward can be reconciled. An
                    # authentication/policy failure with unchanged main cannot.
                    if current == base or attempt + 1 == self.retries:
                        raise RuntimeFailure("GIT_PUSH_FAILED")
                    continue
            current = self.fetch()
            if git(self.repo, "merge-base", "--is-ancestor", commit, current, check=False).returncode:
                raise RuntimeFailure("PUBLISHED_COMMIT_NOT_REACHABLE")
            self.verify(commit, payload)
            self.verify(current, payload)
            current_entries = self.entries(current)
            if any(current_entries.get(path) != oid for path, oid in self.restored_entries.items()):
                raise RuntimeFailure("RESTORED_EVIDENCE_CHANGED_OR_DELETED")
            self.restored_entries = current_entries
            # Observe the clock only AFTER remote reachability and byte checks.
            return {"repository_full_name": self.repository_full_name, "commit_sha": commit,
                    "durable_at": shadow.stamp(self.clock()), "files": len(payload)}
        raise RuntimeFailure("PUBLISH_RETRY_EXHAUSTED")


def restore_bundle(path, destination, study, code_repo, code_commit):
    """Verify the actual ZIP, all members, recipe, and pinned implementation."""
    path, destination, code_repo = Path(path), Path(destination), Path(code_repo)
    if path.is_symlink() or path.stat().st_size > MAX_BUNDLE_BYTES:
        raise RuntimeFailure("BUNDLE_SIZE_OR_TYPE_INVALID")
    raw = path.read_bytes()
    if shadow.digest(raw) != study["bundle_sha256"]:
        raise RuntimeFailure("BUNDLE_HASH_MISMATCH")
    if not SHA1.fullmatch(code_commit) or _oid(git(code_repo, "rev-parse", "HEAD").stdout) != code_commit:
        raise RuntimeFailure("CODE_COMMIT_MISMATCH")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        members = archive.infolist()
        names = [safe_relative(item.filename) for item in members]
        if (len(names) != len(set(names)) or "MANIFEST.json" not in names
                or any(item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                       or item.file_size > MAX_MEMBER_BYTES for item in members)
                or sum(item.file_size for item in members) > MAX_BUNDLE_BYTES):
            raise RuntimeFailure("INVALID_BUNDLE_MEMBERS")
        manifest = document(archive.read("MANIFEST.json"))
        files = manifest.get("files")
        if (manifest.get("schema") != "wnba-shadow-bundle-v1" or not isinstance(files, dict)
                or set(names) != {"MANIFEST.json", *files}):
            raise RuntimeFailure("BUNDLE_MANIFEST_MISMATCH")
        for key in ("candidate_id", "frozen_at", "implementation_sha256", "model_recipe_hash"):
            if manifest.get(key) != study.get(key):
                raise RuntimeFailure("BUNDLE_STUDY_MISMATCH")
        implementations = manifest.get("implementation_files")
        if (not isinstance(implementations, dict) or not REQUIRED_CODE <= set(implementations)
                or shadow.digest(shadow.encode(implementations)) != study["implementation_sha256"]):
            raise RuntimeFailure("IMPLEMENTATION_MANIFEST_MISMATCH")
        for name, metadata in implementations.items():
            filename = shadow._safe_path(code_repo, safe_relative(name))
            content = filename.read_bytes()
            if metadata != {"sha256": shadow.digest(content), "bytes": len(content)}:
                raise RuntimeFailure("IMPLEMENTATION_BYTES_CHANGED")
        contents = {}
        for name, metadata in files.items():
            content = archive.read(safe_relative(name))
            if metadata != {"sha256": shadow.digest(content), "bytes": len(content)}:
                raise RuntimeFailure("BUNDLE_MEMBER_HASH_MISMATCH")
            contents[name] = content
        recipe_path, seed_path = (safe_relative(manifest.get(k)) for k in ("recipe_file", "seed_file"))
        if recipe_path not in contents or seed_path not in contents:
            raise RuntimeFailure("BUNDLE_MODEL_INPUT_MISSING")
        if shadow.digest(contents[recipe_path]) != study["recipe_sha256"]:
            raise RuntimeFailure("RECIPE_FILE_HASH_MISMATCH")
        if "seed_sha256" in study and shadow.digest(contents[seed_path]) != study["seed_sha256"]:
            raise RuntimeFailure("SEED_FILE_HASH_MISMATCH")
        recipe = document(contents[recipe_path])
        if recipe.get("recipe_hash") != study["model_recipe_hash"]:
            raise RuntimeFailure("MODEL_RECIPE_IDENTITY_MISMATCH")
        for name, content in contents.items():
            shadow.write_once(shadow._safe_path(destination, name), content)
    except (zipfile.BadZipFile, KeyError):
        raise RuntimeFailure("INVALID_BUNDLE_ARCHIVE") from None
    finally:
        if "archive" in locals():
            archive.close()
    return recipe, destination / seed_path


def _changed(before, working):
    after = regular_tree(working)
    if any(after.get(path) != raw for path, raw in before.items()):
        raise RuntimeFailure("EXISTING_EVIDENCE_WAS_CHANGED")
    return {path: raw for path, raw in after.items() if path not in before}


def run_once(data_repo, code_repo, run_id, *, repository_full_name, clock=shadow.utcnow,
             scan=None, refresh=None, archive=None):
    """Restore, refresh, forecast, persist, verify and seal one capture.

    Tests inject source adapters and a local bare Git archive. The CLI supplies
    the real bounded network adapters after checking the destination's privacy.
    """
    shadow.identifier(run_id, "run_id")
    archive = archive or GitArchive(data_repo, repository_full_name, clock=clock)
    started = clock()
    with tempfile.TemporaryDirectory(prefix="wnba-shadow-run-") as temporary:
        working = Path(temporary) / "working"
        archive.restore(working)
        before = regular_tree(working)
        config = document(before[CONFIG])
        if (config.get("schema") != "wnba-shadow-runtime-v1"
                or config.get("repository_full_name") != repository_full_name
                or config.get("ledger_path") != PREFIX + "/ledger"):
            raise RuntimeFailure("INVALID_RUNTIME_CONFIGURATION")
        ledger = shadow.Ledger(working / config["ledger_path"])
        study = ledger.study(clock())
        archive.verify_ledger(ledger, head=archive.restored_head, now=clock())
        status, error, staged, sealed, publication = "FAILED", None, None, None, None
        run_path = working / PREFIX / "runs" / run_id / "runtime.json"
        if run_path.exists():
            previous = document(run_path.read_bytes())
            return {**previous, "already_recorded": True}
        try:
            recipe, seed_path = restore_bundle(working / config["bundle_path"],
                                              Path(temporary) / "bundle", study,
                                              code_repo, config.get("code_commit", ""))
            from .future import FutureOnlyModel, load_observations
            batch_path = ledger.root / "batches" / run_id
            if (batch_path / "manifest.json").exists():
                manifest, rows = ledger.batch(run_id, clock())
                staged = {"batch_id": run_id, "manifest_sha256": shadow.digest(shadow.encode(manifest)),
                          "forecasts": len(rows), "capture_status": "RESUMED"}
                update = {"status": "NO_EVENTS"}
            else:
                observations = list(load_observations(seed_path))
                history_root = working / PREFIX / "history"
                if refresh is None:
                    from .outcomes import refresh_outcomes
                    refresh = refresh_outcomes
                update = refresh(history_root, ledger, shadow.instant(study["frozen_at"]),
                                 run_id=run_id, repo=Path(code_repo), clock=clock)
                observations.extend(update["restored_observations"])
                observations.extend(update["observations"])
                model = FutureOnlyModel(observations, recipe, frozen_at=shadow.instant(study["frozen_at"]))
                endpoint = ledger.status(clock())["status"] in {
                    "WAITING_FOR_SETTLEMENT", "EVALUATION_DUE", "EVALUATED", "EVALUATION_INTERRUPTED"}
                if update.get("status") not in ("OK", "NO_EVENTS") or endpoint:
                    reason = "ENDPOINT_REACHED" if endpoint else "BLOCKED_HISTORY"
                    staged = {**ledger.stage(run_id, [], {}, now=clock(), attempts=[{
                        "source": "runtime", "status": reason,
                        "history_status": update.get("status")}]), "capture_status": reason}
                else:
                    if scan is None:
                        from .capture import scan as source_scan
                        scan = source_scan
                    staged = scan(model, ledger, run_id, repo=Path(code_repo), clock=clock)
            ledger.batch(run_id, clock())
            pending = _changed(before, working)
            manifest_path = config["ledger_path"] + "/batches/" + run_id + "/manifest.json"
            # Include all immutable batch files even on recovery after a crash.
            batch_prefix = config["ledger_path"] + "/batches/" + run_id + "/"
            batch_files = {p: raw for p, raw in regular_tree(working).items()
                           if p.startswith(batch_prefix) and not p.endswith("/seal.json")}
            publication = archive.append({**pending, **batch_files}, run_id=run_id)
            before = regular_tree(working)
            receipt_path = batch_path / "seal.json"
            if not receipt_path.exists():
                receipt = {"schema": "wnba-shadow-seal-v1", "provider": "github",
                           **publication, "batch_id": run_id,
                           "manifest_sha256": staged["manifest_sha256"],
                           "artifact_path": manifest_path,
                           "stored_artifact_sha256": staged["manifest_sha256"]}
                sealed = ledger.seal(run_id, receipt, clock())
            else:
                existing = document(receipt_path.read_bytes())
                ledger._validate_seal(ledger.batch(run_id, clock())[0], existing, clock())
                archive.verify_receipt(existing, batch_files)
                sealed = {"batch_id": run_id, "durable_at": existing["durable_at"], "resumed": True}
            status = "OK" if (staged.get("capture_status") in ("OK", "NO_EVENTS", "RESUMED", "ENDPOINT_REACHED")
                               and update.get("status") in ("OK", "NO_EVENTS")) else "DEGRADED"
        except Exception as caught:
            # Keep source failures and incomplete batches, but never expose
            # exception strings that may include source bodies or credentials.
            error = str(caught) if isinstance(caught, RuntimeFailure) else type(caught).__name__
            status = "FAILED"
        result = {"schema": "wnba-shadow-runtime-run-v1", "run_id": run_id,
                  "started_at": shadow.stamp(started), "completed_at": shadow.stamp(clock()),
                  "status": status, "error_code": error,
                  "staged": staged, "seal": sealed, "batch_publication": publication,
                  "parameter_refits": 0, "wagers": 0, "automatic_evaluations": 0}
        shadow.write_once(run_path, shadow.encode(result))
        pending = _changed(before, working)
        try:
            final_publication = archive.append(pending, run_id=run_id)
        except RuntimeFailure as caught:
            if str(caught) not in ("MERGED_LEDGER_CONFLICT", "IMMUTABLE_PATH_CONFLICT"):
                raise
            # A concurrent settlement revision can invalidate only our proposed
            # merge. Preserve the complete rejected addition under this run's
            # diagnostic directory, without poisoning the active ledger.
            result.update(status="FAILED", error_code=str(caught), rejected_additions=True)
            quarantine = {PREFIX + "/runs/" + run_id + "/rejected/" + path: raw
                          for path, raw in pending.items()}
            quarantine[PREFIX + "/runs/" + run_id + "/runtime.json"] = shadow.encode(result)
            final_publication = archive.append(quarantine, run_id=run_id)
        return {**result, "receipt_publication": final_publication}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo", type=Path, required=True)
    parser.add_argument("--code-repo", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-public-evidence", action="store_true",
                        help="Use only after explicit permission to publish private research evidence")
    args = parser.parse_args()
    try:
        if Path(__file__).resolve() != (args.code_repo / "research/wnba_v2/runtime.py").resolve():
            raise RuntimeFailure("EXECUTED_CODE_CHECKOUT_MISMATCH")
        verify_destination(args.data_repo, args.repository, allow_public=args.allow_public_evidence)
        result = run_once(args.data_repo, args.code_repo, args.run_id,
                          repository_full_name=args.repository)
        # This output belongs to the authorized evidence repository's log.
        print(json.dumps({"run_id": args.run_id, "status": result["status"],
                          "receipt_saved": True}, sort_keys=True))
        return 0 if result["status"] == "OK" else 2
    except (RuntimeFailure, ValueError, TypeError, KeyError, OSError) as error:
        code = str(error) if isinstance(error, RuntimeFailure) else type(error).__name__
        print(json.dumps({"run_id": args.run_id, "status": "FAILED", "error_code": code,
                          "receipt_saved": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
