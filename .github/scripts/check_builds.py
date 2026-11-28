"""Look up the newest Modern F-15E builds and record them in build.json.

Runs in GitHub Actions (see ../workflows/watch-builds.yml), never on anyone's PC.
- Tester builds: the private repo 65thhaack/Modern-F15e-Mod-Testers, read with the
  TESTER_TOKEN repository secret.
- Stable and Preview builds: the public repo 65thhaack/Modern-F15E-Mod.
build.json is only rewritten when a version changes, so the repo is not spammed.
When Stable moves past the version in autoreset.json, it sets the new_stable output and the
workflow runs auto_reset.py, which resets the tracker like a dev's Reset button.
"""
import json, os, re, sys, urllib.request, urllib.error

TESTER_REPO = "65thhaack/Modern-F15e-Mod-Testers"
PUBLIC_REPO = "65thhaack/Modern-F15E-Mod"
OUT = "build.json"
MARK = "autoreset.json"  # the Stable build the tracker was last reset for (see auto_reset.py)


def releases(repo, token=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=20",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mf15e-tracker-build-watch"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def newest(rels, channel=None):
    """Newest version from release asset names like DcsWebDatalink-2.7.3-tester-full.nupkg,
    falling back to the release tag."""
    best = None
    for rel in rels:
        if rel.get("draft"):
            continue
        found = []
        for a in rel.get("assets", []):
            m = re.match(r"DcsWebDatalink-(\d+\.\d+\.\d+)(?:-([a-z]+))?-full\.nupkg$", a.get("name", ""))
            if m and (m.group(2) or "stable") == (channel or "stable"):
                found.append(m.group(1))
        if not found and channel in (None, "stable", "tester"):
            tag = re.sub(r"^[vV]", "", rel.get("tag_name", ""))
            if re.match(r"^\d+\.\d+\.\d+", tag):
                found.append(re.match(r"^\d+\.\d+\.\d+", tag).group(0))
        for v in found:
            if best is None or vkey(v) > vkey(best):
                best = v
    return best


def main():
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
    new = dict(old)
    problems = []

    token = os.environ.get("TESTER_TOKEN", "").strip()
    if not token:
        problems.append("TESTER_TOKEN secret is not set, so tester builds cannot be checked.")
    else:
        try:
            v = newest(releases(TESTER_REPO, token), "tester")
            if v:
                new["tester"] = v
            else:
                problems.append("No tester build found in " + TESTER_REPO + ".")
        except urllib.error.HTTPError as e:
            problems.append(f"Tester repo check failed: HTTP {e.code} (is the TESTER_TOKEN still valid?)")

    try:
        pub = releases(PUBLIC_REPO)
        for ch in ("stable", "preview"):
            v = newest(pub, ch)
            if v:
                new[ch] = v
    except urllib.error.HTTPError as e:
        problems.append(f"Public repo check failed: HTTP {e.code}")

    for p in problems:
        print("WARNING:", p)
    changed = {k: new[k] for k in ("tester", "stable", "preview") if new.get(k) != old.get(k)}
    if changed:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({k: new[k] for k in ("tester", "stable", "preview") if k in new}, f, indent=1)
            f.write("\n")
        print("New builds:", changed)
    else:
        print("No new builds.", {k: new.get(k) for k in ("tester", "stable", "preview")})
    # A newer Stable build than the last automatic reset → tell the workflow to reset the tracker.
    stable = new.get("stable")
    mark = {}
    if os.path.exists(MARK):
        with open(MARK, encoding="utf-8") as f:
            mark = json.load(f)
    if stable and not mark.get("stable"):
        with open(MARK, "w", encoding="utf-8") as f:  # first run: remember today's Stable, reset nothing
            json.dump({"stable": stable, "at": "", "skipped": True}, f, indent=1)
            f.write("\n")
    elif stable and vkey(stable) > vkey(mark["stable"]):
        print(f"Stable went from {mark['stable']} to {stable}: the tracker will be reset.")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"new_stable={stable}\ntester={new.get('tester', '')}\n")
    # Fail the run (so it shows red in Actions) only if the tester check could not run at all.
    if any(p.startswith(("TESTER_TOKEN", "Tester repo")) for p in problems):
        sys.exit(1)


if __name__ == "__main__":
    main()
