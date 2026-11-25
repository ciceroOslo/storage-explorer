#!/usr/bin/env python3
"""Compare two scans: what was freed (or added), where, and by whom.

  python3 compare.py data/storage_2026-11-25.json.gz data/storage_2026-12-08.json.gz

Prints the change in free space, the folders that explain most of the change,
and the change per owner. Folder changes are shown at the deepest level that
still explains at least 90% of a parent's change, so you get
"amoc/project/run3  -1.4 TiB" rather than a long list of every ancestor.
"""
import argparse
import collections
import gzip
import json
import pwd

TIB = 2 ** 40


def load(path):
    d = json.load(gzip.open(path, "rt"))
    root_parts = len(d["root"].rstrip("/").split("/"))
    by_dir, by_owner = collections.Counter(), collections.Counter()
    for dk, uid, _mb, _ab, _n, _size, blocks in d["agg"]:
        parts = dk.split("/")
        for k in range(root_parts + 1, len(parts) + 1):
            by_dir["/".join(parts[:k])] += blocks
        by_owner[uid] += blocks
    free = d["statfs"]["bfree"] * d["statfs"]["bsize"]
    return d, by_dir, by_owner, free


def uname(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid} (deleted account)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--min-gib", type=float, default=50, help="hide folder changes smaller than this")
    ap.add_argument("--owners", type=int, default=15, help="number of owners to list")
    args = ap.parse_args()

    a, da, oa, fa = load(args.before)
    b, db, ob, fb = load(args.after)
    if a["root"] != b["root"]:
        print(f"warning: scans have different roots ({a['root']} vs {b['root']})")

    print(f"Free space: {fa / TIB:.2f} TiB -> {fb / TIB:.2f} TiB ({(fb - fa) / TIB:+.2f} TiB)")
    print(f"Seen by scan: {(sum(ob.values()) - sum(oa.values())) / TIB:+.2f} TiB")
    print("(A gap between the two usually means changes inside folders the scan cannot read.)\n")

    diff = {k: db[k] - da[k] for k in set(da) | set(db)}
    big = {k: v for k, v in diff.items() if abs(v) >= args.min_gib * 2 ** 30}

    def explained_by_child(k, v):
        return any(c.startswith(k + "/") and abs(big[c]) >= 0.9 * abs(v) for c in big)

    rows = sorted((v, k) for k, v in big.items() if not explained_by_child(k, v))
    # inside a folder that was deleted outright, its subfolders are just noise
    gone = {k for v, k in rows if not db.get(k)}
    rows = [(v, k) for v, k in rows if not any(k.startswith(g + "/") for g in gone)]
    print("Largest folder changes:")
    for v, k in rows:
        status = "" if k in db and db[k] > 0 else "  (gone)"
        print(f"  {v / TIB:+8.2f} TiB  {k}{status}")
    if not rows:
        print("  none above threshold")

    print("\nChange by owner:")
    owners = sorted(set(oa) | set(ob), key=lambda u: ob[u] - oa[u])
    shown = [u for u in owners if ob[u] != oa[u]]
    for u in shown[: args.owners]:
        print(f"  {(ob[u] - oa[u]) / TIB:+8.2f} TiB  {uname(u)}")
    grew = [u for u in reversed(owners) if ob[u] > oa[u]][:5]
    if grew:
        print("Largest growth:")
        for u in grew:
            print(f"  {(ob[u] - oa[u]) / TIB:+8.2f} TiB  {uname(u)}")


if __name__ == "__main__":
    main()
