# storage-explorer

See where the data on a shared filesystem is, who owns it, and how old it is, without the survey becoming a multi-day job of its own.

It has two parts:

1. **A scanner** (`scan.py`) that walks the filesystem in parallel, reading file metadata only (size, owner, dates). It never opens a file. On a 730 TB XFS volume with 39 million files it takes about 3 minutes and produces a summary of under 1 MB.
2. **An explorer**: a single self-contained HTML page with a treemap you can click into, owner and age breakdowns, the largest files, and a list of folders the scan could not read. It works offline and in a Teams file preview.

`compare.py` compares two scans, which is how you measure what a clean-up freed.

---

## Guide for the dugnad coordinator

### Before the dugnad (about 10 minutes)

```bash
git clone https://github.com/ciceroOslo/storage-explorer.git
cd storage-explorer
./refresh.sh            # scans /storage, builds explorer/standalone.html
```

Then share `explorer/standalone.html` with the group, for example in the Teams channel's Files tab. People need to **download it and open it in a browser** if the Teams preview does not show it.

Run the scan on the morning of the dugnad, so everyone works from current numbers. Keep that scan file (`data/storage_<date>.json.gz`): it is your "before" picture.

### During the dugnad

Suggested way to use the explorer:

- **Everyone filters by their own username** (the Owner menu). The page then shows only their data: where it is, how old, and their largest files.
- **Set "Count as old after"** to the threshold you agree on (5 years is the default).
- **Colour the map by "Largest owner"** to see whose data a shared project folder holds.
- **"Copy path"** copies the current folder, ready to paste into a terminal.

Keep a simple list of **holds**: things people want kept for now, with a reason and a date to look again. Requests to keep data made in a chat thread are easily forgotten, and a list shows people they were heard.

### After the dugnad

```bash
./refresh.sh
python3 compare.py data/storage_<before>.json.gz data/storage_<after>.json.gz
```

This prints the change in free space, the folders that explain most of it (deleted folders are marked `(gone)`), and the change per owner. That is your report: "the dugnad freed X TB, mostly from ...".

### Things worth telling people

- **Moving data between folders on the same disk frees nothing.** For example, `nac` and `no-backup-nac` are both on `/storage`. Moving data from a backed-up folder to a non-backed-up one saves backup cost, not disk space. Check with `df -h /storage` before and after.
- **Compression is often the easy win.** NetCDF files without internal compression often shrink 2–5x, and nobody loses data:
  ```bash
  nccopy -d1 -s in.nc out.nc          # or: ncks -4 -L 1 in.nc out.nc
  ```
  Check the output opens correctly before removing the original. Model log files (`*.log`) compress very well with `gzip`.
- **Public datasets can be downloaded again** (reanalyses such as ERA5 and MERRA-2, CMIP output). Processed or regridded versions take effort to recreate, so the person who made them should decide.
- **Data owned by deleted accounts** appears as `uid 12345`. Someone with authority needs to decide who inherits it, because the owner cannot be asked.

---

## What the numbers mean

- **Sizes are disk usage** (the blocks actually allocated, like `du`), in binary units: 1 TiB = 1024⁴ bytes. This matches what `df -h` shows as "T".
- **Age** is measured from the file's last-modified time (mtime) by default. You can switch to last-accessed time (atime). But if a copy, migration or backup ever read every file, access times were reset at that point, and the page warns you when it detects this pattern.
- **Owner** is the file's owner, which is usually whoever created it. Copying data, `chown`, or jobs run under another account can change that, so treat it as "who to ask first", not "who is responsible".
- **The folder breakdown stops 5 levels below the filesystem root** (configurable with `scan.py --depth`). Anything deeper is counted in its level-5 parent folder. The 3,000 largest files are also listed individually (`--topn`).
- **Hard-linked files** are counted once.

## Permissions: what you can and can't see

The scanner sees what your account can read. Two kinds of folder are invisible to it:

- **Folders you cannot list at all.** These appear in "Blind spots".
- **Folders you can list but not enter** (read permission without execute). You see the file names but not their sizes. These also appear in "Blind spots", with the number of hidden entries.

The whole-filesystem bar shows the total gap between what `df` reports as used and what the scan saw.

To see everything, an administrator can run the scan as root. If they do, the output reveals the names and sizes of files in folders their owners closed off, so keep that output restricted.

On XFS with quota accounting enabled (mount option `uquota` or `uqnoenforce`), an administrator can get exact per-user totals instantly:

```bash
xfs_quota -x -c 'report -u -h' /storage
```

## Privacy

The scan output and generated pages (`data/`, `explorer/`) contain usernames, folder names and file names. **Don't commit them to this repository or share them outside your organisation.** `.gitignore` excludes both folders.

## Cost and politeness

`refresh.sh` runs the scan at the lowest CPU and I/O priority (`nice -n 19 ionice -c3`) with 16 parallel workers, so it shouldn't slow down other users. Time scales with the number of files, not their size: expect roughly 200,000–250,000 files per second on a fast local filesystem, and much slower on network filesystems. Once a week, or before and after a clean-up, is plenty.

To scan a different filesystem or use fewer workers:

```bash
python3 scan.py /path/to/fs -o data/myscan.json.gz -j 8
python3 build.py data/myscan.json.gz -o explorer
```

## Requirements

- Linux and Python 3.8 or newer. No extra packages. (On a cluster with modules, `module load Python` first if `python3` is missing or old.)
- A browser to view the page.

## Files

| File | Purpose |
|---|---|
| `scan.py` | Parallel metadata scan; writes a gzipped JSON summary |
| `build.py` | Turns a scan into `explorer/standalone.html` (self-contained) and `explorer/index.html` + `data.json` (for web hosting) |
| `template.html` | The explorer page itself |
| `compare.py` | Before/after comparison of two scans |
| `refresh.sh` | Scan `/storage` and rebuild, in one step |
| `vendor/d3-hierarchy.min.js` | Treemap layout library (ISC licence), embedded into the standalone page |

## Licence

MIT. See [LICENSE](LICENSE). The bundled `d3-hierarchy` is © Mike Bostock, ISC licence (see `vendor/LICENSE-d3-hierarchy`).
