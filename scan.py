#!/usr/bin/env python3
"""Metadata-only parallel scan of a directory tree for the storage explorer.

Never opens files: only readdir + lstat. Output is a gzipped JSON of rollups:
  per directory (truncated at --depth), per owner uid, per mtime-age bucket,
  per atime-age bucket -> [files, apparent_bytes, disk_bytes]
plus the newest mtime/atime per directory, the top-N largest files, and
unreadable directories.

Run politely:  nice -n 19 ionice -c3 python3 scan.py /storage -o out.json.gz
"""
import argparse
import gzip
import heapq
import json
import multiprocessing as mp
import os
import queue
import stat
import sys
import time

# Age bucket edges in days (bucket i means age < EDGES[i]; last bucket is older)
EDGES = [90, 365, 2 * 365, 3 * 365, 5 * 365, 10 * 365]
LABELS = ["<3mo", "3-12mo", "1-2y", "2-3y", "3-5y", "5-10y", ">10y"]


def bucket(age_days):
    for i, e in enumerate(EDGES):
        if age_days < e:
            return i
    return len(EDGES)


def worker(root, depth, now, topn, q, results, max_queue):
    root_parts = len(root.rstrip("/").split("/"))
    agg = {}        # (dirkey, uid, mb, ab) -> [n, size, blocks]
    newest = {}     # dirkey -> [max_mtime, max_atime]
    nfiles = {}     # dirkey -> number of subdirectories seen (for info)
    top = []        # min-heap of (blocks, path, size, uid, mtime, atime)
    links = {}      # ino -> (dirkey, uid, mb, ab, size, blocks, mtime, atime)  nlink>1 files
    unreadable = []
    hidden = {}     # dirkey -> entries whose metadata we may not read (dir lacks x)
    noexec = []     # dirs that are listable but not searchable
    myears = {}     # mtime year -> disk bytes
    ayears = {}     # atime year -> disk bytes
    errors = 0
    scanned_dirs = 0

    def dirkey_of(path):
        parts = path.split("/")
        if len(parts) - root_parts > depth:
            parts = parts[: root_parts + depth]
        return "/".join(parts)

    while True:
        item = q.get()
        if item is None:
            break
        stack = [item]
        while stack:
            d = stack.pop()
            scanned_dirs += 1
            dk = dirkey_of(d)
            nw = newest.get(dk)
            if nw is None:
                nw = newest[dk] = [0, 0]
                nfiles[dk] = 0
            try:
                it = os.scandir(d)
            except PermissionError:
                unreadable.append(d)
                continue
            except OSError:
                errors += 1
                continue
            denied = 0
            with it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except PermissionError:
                        denied += 1
                        continue
                    except OSError:
                        errors += 1
                        continue
                    mode = st.st_mode
                    if stat.S_ISDIR(mode):
                        if st.st_dev != rootdev:
                            continue  # don't cross filesystems
                        p = e.path
                        if q.qsize() < max_queue:
                            q.put(p)
                        else:
                            stack.append(p)
                        nfiles[dk] += 1
                        continue
                    if stat.S_ISLNK(mode):
                        continue
                    size = st.st_size
                    blocks = st.st_blocks * 512
                    mt, at = st.st_mtime, st.st_atime
                    mb = bucket((now - mt) / 86400.0)
                    ab = bucket((now - at) / 86400.0)
                    if st.st_nlink > 1:
                        links[st.st_ino] = (dk, st.st_uid, mb, ab, size, blocks, mt, at)
                        continue
                    k = (dk, st.st_uid, mb, ab)
                    a = agg.get(k)
                    if a is None:
                        agg[k] = [1, size, blocks]
                    else:
                        a[0] += 1
                        a[1] += size
                        a[2] += blocks
                    y = time.gmtime(mt).tm_year
                    myears[y] = myears.get(y, 0) + blocks
                    y = time.gmtime(at).tm_year
                    ayears[y] = ayears.get(y, 0) + blocks
                    if mt > nw[0]:
                        nw[0] = mt
                    if at > nw[1]:
                        nw[1] = at
                    if len(top) < topn:
                        heapq.heappush(top, (blocks, e.path, size, st.st_uid, mt, at))
                    elif blocks > top[0][0]:
                        heapq.heapreplace(top, (blocks, e.path, size, st.st_uid, mt, at))
            if denied:
                hidden[dk] = hidden.get(dk, 0) + denied
                noexec.append([d, denied])
        q.task_done()

    results.put({
        "agg": [[*k, *v] for k, v in agg.items()],
        "newest": newest,
        "subdirs": nfiles,
        "top": top,
        "links": links,
        "unreadable": unreadable,
        "hidden": hidden, "noexec": noexec, "myears": myears, "ayears": ayears,
        "errors": errors,
        "dirs": scanned_dirs,
    })


rootdev = None


def main():
    global rootdev
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("-j", "--jobs", type=int, default=12)
    ap.add_argument("--depth", type=int, default=5, help="rollup depth below root's parent dir")
    ap.add_argument("--topn", type=int, default=3000)
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    rootdev = os.lstat(root).st_dev
    now = time.time()
    t0 = time.time()
    # depth counted from "/storage" level so test runs on subdirs give
    # the same keys as a full run
    base = "/".join(root.split("/")[:2]) or "/"
    eff_depth = args.depth - (len(root.split("/")) - len(base.split("/")))

    q = mp.JoinableQueue()
    results = mp.Queue()
    q.put(root)
    procs = [mp.Process(target=worker, args=(root, eff_depth, now, args.topn, q, results, 4 * args.jobs))
             for _ in range(args.jobs)]
    for p in procs:
        p.start()
    q.join()
    for _ in procs:
        q.put(None)
    parts = [results.get() for _ in procs]
    for p in procs:
        p.join()

    # merge
    agg, newest, subdirs, top, links, unreadable = {}, {}, {}, [], {}, []
    errors = dirs = 0
    hidden, noexec, myears, ayears = {}, [], {}, {}
    for r in parts:
        for k, v in r["hidden"].items():
            hidden[k] = hidden.get(k, 0) + v
        noexec += r["noexec"]
        for src, dst in ((r["myears"], myears), (r["ayears"], ayears)):
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v
        for dk, uid, mb, ab, n, s, b in r["agg"]:
            a = agg.setdefault((dk, uid, mb, ab), [0, 0, 0])
            a[0] += n; a[1] += s; a[2] += b
        for dk, (m, a_) in r["newest"].items():
            o = newest.setdefault(dk, [0, 0])
            o[0] = max(o[0], m); o[1] = max(o[1], a_)
        for dk, n in r["subdirs"].items():
            subdirs[dk] = subdirs.get(dk, 0) + n
        top.extend(r["top"])
        links.update(r["links"])  # same inode seen twice -> counted once
        unreadable += r["unreadable"]
        errors += r["errors"]; dirs += r["dirs"]
    for dk, uid, mb, ab, size, blocks, mt, at in links.values():
        a = agg.setdefault((dk, uid, mb, ab), [0, 0, 0])
        a[0] += 1; a[1] += size; a[2] += blocks
        o = newest.setdefault(dk, [0, 0])
        o[0] = max(o[0], mt); o[1] = max(o[1], at)
    top = heapq.nlargest(args.topn, top)

    elapsed = time.time() - t0
    nf = sum(v[0] for v in agg.values())
    out = {
        "root": root, "scan_time": now, "elapsed_s": elapsed, "jobs": args.jobs,
        "depth": args.depth, "age_labels": LABELS, "age_edges_days": EDGES,
        "files": nf, "dirs": dirs, "errors": errors, "hardlinked_files": len(links),
        "agg": [[*k, *v] for k, v in agg.items()],
        "newest": newest,
        "top": [{"path": p, "size": s, "blocks": b, "uid": u, "mtime": m, "atime": a}
                for b, p, s, u, m, a in top],
        "unreadable": unreadable,
        "hidden": hidden,
        "noexec": sorted(noexec, key=lambda x: -x[1]),
        "myears": myears, "ayears": ayears,
        "statfs": dict(zip(("bsize", "blocks", "bfree", "bavail"),
                           (lambda v: (v.f_frsize, v.f_blocks, v.f_bfree, v.f_bavail))(os.statvfs(root)))),
    }
    with gzip.open(args.out, "wt") as f:
        json.dump(out, f)
    print(f"{nf:,} files, {dirs:,} dirs, {errors} errors, {len(unreadable)} unreadable dirs, "
          f"{sum(hidden.values()):,} hidden entries in {len(noexec)} dirs, "
          f"{elapsed:.0f}s ({nf / max(elapsed, 1e-9):,.0f} files/s)", file=sys.stderr)


if __name__ == "__main__":
    main()
