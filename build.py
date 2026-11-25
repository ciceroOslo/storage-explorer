#!/usr/bin/env python3
"""Turn a scan.py output into the explorer page.

  python3 build.py data/storage_YYYY-MM-DD.json.gz -o explorer/

Writes explorer/index.html + explorer/data.json (for publishing next to each
other) and explorer/standalone.html (data inlined; open directly from disk).
"""
import argparse
import gzip
import json
import os
import pwd

HERE = os.path.dirname(os.path.abspath(__file__))


def uname(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("-o", "--outdir", default="explorer")
    args = ap.parse_args()
    d = json.load(gzip.open(args.scan, "rt"))

    # --- nodes: every rollup dir plus all ancestors up to the fs root ---
    root = "/" + d["root"].strip("/").split("/")[0]  # e.g. /storage
    paths = set(d["newest"]) | {r[0] for r in d["agg"]} | set(d["hidden"])
    for p in list(paths):
        while p != root and p.startswith(root):
            p = p.rsplit("/", 1)[0]
            paths.add(p)
    paths.add(root)
    order = sorted(paths, key=lambda p: (p.count("/"), p))
    idx = {p: i for i, p in enumerate(order)}
    # carry newest dates and hidden counts up to every ancestor
    newest = {p: list(d["newest"].get(p, [0, 0])) for p in order}
    hidden = {p: d["hidden"].get(p, 0) for p in order}
    for p in sorted(order, key=lambda p: -p.count("/")):
        if p == root:
            continue
        q = p.rsplit("/", 1)[0]
        newest[q][0] = max(newest[q][0], newest[p][0])
        newest[q][1] = max(newest[q][1], newest[p][1])
        hidden[q] += hidden[p]
    nodes = []
    for p in order:
        parent = -1 if p == root else idx[p.rsplit("/", 1)[0]]
        nw = newest[p]
        nodes.append([p if p == root else p.rsplit("/", 1)[1], parent,
                      int(nw[0]), int(nw[1]), hidden[p]])

    # --- owners, ranked by total disk use (color follows rank once, globally) ---
    tot = {}
    for r in d["agg"]:
        tot[r[1]] = tot.get(r[1], 0) + r[6]
    for f in d["top"]:
        tot.setdefault(f["uid"], 0)
    uids = sorted(tot, key=lambda u: -tot[u])
    uix = {u: i for i, u in enumerate(uids)}

    rows = []
    for dk, uid, mb, ab, n, size, blocks in d["agg"]:
        rows += [idx[dk], uix[uid], mb, ab, n, blocks]

    out = {
        "root": root,
        "scanned_root": d["root"],
        "scan_time": int(d["scan_time"]),
        "elapsed_s": round(d["elapsed_s"]),
        "files": d["files"], "dirs": d["dirs"],
        "age_labels": d["age_labels"], "age_edges_days": d["age_edges_days"],
        "statfs": d["statfs"],
        "users": [uname(u) for u in uids],
        "nodes": nodes,
        "rows": rows,
        "top": [[f["path"], uix[f["uid"]], f["blocks"], int(f["mtime"]), int(f["atime"])]
                for f in d["top"]],
        "unreadable": d["unreadable"],
        "noexec": d["noexec"],
        "myears": {int(k): v for k, v in d["myears"].items()},
        "ayears": {int(k): v for k, v in d["ayears"].items()},
    }
    os.makedirs(args.outdir, exist_ok=True)
    js = json.dumps(out, separators=(",", ":"))
    with open(os.path.join(args.outdir, "data.json"), "w") as f:
        f.write(js)
    tpl = open(os.path.join(HERE, "template.html")).read()
    with open(os.path.join(args.outdir, "index.html"), "w") as f:
        f.write(tpl.replace("/*__DATA__*/", ""))
    with open(os.path.join(args.outdir, "standalone.html"), "w") as f:
        f.write('<!doctype html>\n<html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                '<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}</style>\n')
        lib = open(os.path.join(HERE, "vendor", "d3-hierarchy.min.js")).read()
        cdn = '<script src="https://cdn.jsdelivr.net/npm/d3-hierarchy@3.1.2/dist/d3-hierarchy.min.js"></script>'
        assert cdn in tpl
        tpl = tpl.replace(cdn, "<script>" + lib.replace("</", "<\\/") + "</script>")
        f.write(tpl.replace("/*__DATA__*/", "window.__DATA__=" + js.replace("</", "<\\/") + ";"))
    print(f"{len(nodes)} dirs, {len(rows) // 6} rows, {len(uids)} owners, data.json {len(js) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
