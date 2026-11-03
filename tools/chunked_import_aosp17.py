#!/usr/bin/env python3
"""Publish an exact Git tree in resumable, non-forced chunks.

All Git objects must be available (or fetchable through a promisor remote) in
SOURCE_REPO. The upload branch is deliberately separate from the final ref.
Only after exact tree equality is confirmed is the final ref created.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def git(args, *, cwd, data=None, env=None):
    proc = subprocess.run(["git", *args], cwd=cwd, input=data, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        err = proc.stderr.decode('utf-8', 'replace')
        raise RuntimeError(f"git {' '.join(args)} exited {proc.returncode}: {err[-2500:]}")
    return proc.stdout


def ref_oid(repo, remote, ref):
    out = git(["ls-remote", "--heads", remote, ref], cwd=repo)
    rows = out.splitlines()
    if len(rows) > 1:
        raise RuntimeError(f"Unexpected multiple remote refs for {ref}")
    return rows[0].split(b'\t')[0].decode() if rows else None


def get_rows(repo, commit):
    out = git(["ls-tree", "-r", "-z", commit], cwd=repo)
    return [row for row in out.split(b'\0') if row]


def check_row(row):
    if b'\t' not in row:
        raise RuntimeError(f"Unexpected tree entry: {row!r}")
    spec, path = row.split(b'\t', 1)
    parts = spec.split(b' ')
    if len(parts) != 3 or parts[1] not in (b'blob', b'commit') or not path:
        raise RuntimeError(f"Unexpected tree spec: {row!r}")
    return spec, path


def push(repo, remote, sha, ref):
    git(["-c", "protocol.version=2", "push", remote, f"{sha}:{ref}"], cwd=repo)
    got = ref_oid(repo, remote, ref)
    if got != sha:
        raise RuntimeError(f"Remote verification failed for {ref}: {got} != {sha}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--remote', default='publish')
    ap.add_argument('--source', required=True)
    ap.add_argument('--tree', required=True)
    ap.add_argument('--staging-branch', required=True)
    ap.add_argument('--final-branch', required=True)
    ap.add_argument('--chunk-files', type=int, default=250)
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    if args.chunk_files < 1 or args.chunk_files > 3000:
        raise RuntimeError("Invalid chunk-files setting")
    if args.staging_branch == args.final_branch:
        raise RuntimeError("Staging and final refs must be distinct")
    for ref in (args.staging_branch, args.final_branch):
        git(["check-ref-format", "--branch", ref], cwd=repo)

    source_sha = git(["rev-parse", f"{args.source}^{{commit}}"], cwd=repo).decode().strip()
    source_tree = git(["rev-parse", f"{source_sha}^{{tree}}"], cwd=repo).decode().strip()
    if source_sha != args.source or source_tree != args.tree:
        raise RuntimeError(f"Source mismatch: commit={source_sha}, tree={source_tree}")
    rows = get_rows(repo, source_sha)
    for row in rows:
        check_row(row)
    print(f"Official source {source_sha}: {len(rows)} Git entries, tree {source_tree}", flush=True)

    final_ref = f"refs/heads/{args.final_branch}"
    stage_ref = f"refs/heads/{args.staging_branch}"
    final_now = ref_oid(repo, args.remote, final_ref)
    if final_now:
        # Ref already exists. Fetch and check equality; no implicit overwrite.
        git(["-c", "protocol.version=2", "fetch", "--no-tags", "--filter=blob:none",
             args.remote, final_ref], cwd=repo)
        final_tree = git(["rev-parse", "FETCH_HEAD^{tree}"], cwd=repo).decode().strip()
        if final_tree != source_tree:
            raise RuntimeError(f"Final ref already exists with another tree: {final_tree}")
        print(f"Final ref already contains verified release tree: {final_now}", flush=True)
        return

    stage_sha = ref_oid(repo, args.remote, stage_ref)
    if stage_sha:
        git(["-c", "protocol.version=2", "fetch", "--no-tags", "--filter=blob:none",
             args.remote, stage_ref], cwd=repo)
        current = git(["rev-parse", "FETCH_HEAD^{commit}"], cwd=repo).decode().strip()
        if current != stage_sha:
            raise RuntimeError("Staging ref raced during fetch")
        done_rows = get_rows(repo, current)
        if rows[:len(done_rows)] != done_rows:
            raise RuntimeError("Staging tree is not a byte-for-byte source prefix")
        start = len(done_rows)
        print(f"Resuming {stage_ref} at {start}/{len(rows)}", flush=True)
    else:
        empty_tree = git(["hash-object", "-t", "tree", "-w", "--stdin"], cwd=repo, data=b'').decode().strip()
        current = git(["commit-tree", empty_tree], cwd=repo,
                      data=b'Initialize isolated AOSP17 tree upload\n').decode().strip()
        push(repo, args.remote, current, stage_ref)
        start = 0
        print("Created empty, isolated upload staging branch", flush=True)

    index_file = str(repo / '.git' / 'aosp17-upload.index')
    if os.path.exists(index_file):
        os.unlink(index_file)
    idx_env = os.environ.copy()
    idx_env['GIT_INDEX_FILE'] = index_file
    git(["read-tree", current], cwd=repo, env=idx_env)

    for offset in range(start, len(rows), args.chunk_files):
        chunk = rows[offset:offset + args.chunk_files]
        git(["update-index", "-z", "--index-info"], cwd=repo,
            data=b'\0'.join(chunk) + b'\0', env=idx_env)
        next_tree = git(["write-tree"], cwd=repo, env=idx_env).decode().strip()
        msg = f"Import pinned AOSP17 framework source entries {offset + 1}-{offset + len(chunk)}\n"
        nxt = git(["commit-tree", next_tree, "-p", current], cwd=repo,
                  data=msg.encode()).decode().strip()
        push(repo, args.remote, nxt, stage_ref)
        current = nxt
        print(f"Uploaded {offset + len(chunk)}/{len(rows)} entries, stage={current[:12]}", flush=True)

    final_tree = git(["rev-parse", f"{current}^{{tree}}"], cwd=repo).decode().strip()
    if final_tree != source_tree:
        raise RuntimeError(f"Final staged tree mismatch: {final_tree} != {source_tree}")
    if ref_oid(repo, args.remote, final_ref):
        raise RuntimeError("Final ref appeared during transfer; refusing update")
    push(repo, args.remote, current, final_ref)
    print(f"PUBLISHED VERIFIED SOURCE SNAPSHOT: {current}, tree {final_tree}", flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
