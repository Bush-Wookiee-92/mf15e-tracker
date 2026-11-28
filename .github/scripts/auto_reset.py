"""Reset the test tracker automatically when a new Stable build is released.

Runs in GitHub Actions (see ../workflows/watch-builds.yml), never on anyone's PC.
check_builds.py decides when a new Stable build is out; this script then does exactly
what a dev's Reset button does (without the two options):
  1. saves the finished round to builds/ (Build history),
  2. puts every check back to Not tested,
  3. clears every tester result and reported problem,
  4. logs it in Who did what as "Build watcher".
It needs the FIREBASE_SERVICE_ACCOUNT repository secret (a Firebase service-account key).
With --dry-run it only reads and prints what it would do.
"""
import datetime, json, os, re, sys, time

MODES = ["vr", "d2", "pilot", "wso", "mc"]
MARK = "autoreset.json"
RECENT_HOURS = 12  # a hand Reset this recent means the round already started fresh


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def b36(n):
    s, d = "", "0123456789abcdefghijklmnopqrstuvwxyz"
    while n:
        n, r = divmod(n, 36)
        s = d[r] + s
    return s or "0"


def nrep(r):
    """Same as the page's nRep(): a tester's entry for one check, or None."""
    if not isinstance(r, dict):
        return None
    m = {k: v for k, v in (r.get("m") or {}).items() if k in MODES and v in ("ok", "bad")}
    v = list(m.values())
    err = bool(r.get("err")) or r.get("s") == "err"
    look = bool(r.get("look")) or r.get("s") == "look"
    s = "err" if err else "bad" if "bad" in v else "ok" if "ok" in v else "look" if look else (r.get("s") if r.get("s") in ("ok", "bad") else "")
    return {"s": s, "m": m} if s else None


def snapshot(items, reports, names, build):
    """Same shape as the page's snapshotRound()."""
    out, testers, tnames, counts = {}, {}, {}, {"ok": 0, "bad": 0, "open": 0, "n": 0}
    for iid, it in items.items():
        if not it:
            continue
        reps = [(u, nrep(((r or {}).get("r") or {}).get(iid))) for u, r in reports.items()]
        reps = [(u, x) for u, x in reps if x]
        m = {}
        for k in MODES:
            if any(x["m"].get(k) == "bad" for _, x in reps):
                m[k] = "bad"
            elif any(x["m"].get(k) == "ok" for _, x in reps):
                m[k] = "ok"
        st = it.get("status") or "open"
        out[iid] = {"t": it.get("tile", ""), "x": it.get("text", ""), "s": st, "m": m, "sev": it.get("sev") or ""}
        for u, _ in reps:
            testers[u] = testers.get(u, 0) + 1
            tnames[u] = names.get(u, "")
        counts["n"] += 1
        counts[st if st in ("ok", "bad") else "open"] += 1
    return {"build": build, "at": now_iso(), "by": "auto", "byName": "Build watcher",
            "counts": counts, "testers": testers, "names": tnames, "items": out}


def main():
    dry = "--dry-run" in sys.argv
    stable = os.environ.get("NEW_STABLE", "").strip()
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        print("ERROR: the FIREBASE_SERVICE_ACCOUNT secret is not set, so the tracker can't be reset automatically.")
        sys.exit(1)
    import firebase_admin
    from firebase_admin import credentials, firestore
    firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    db = firestore.client()

    items = {d.id: d.to_dict() for d in db.collection("items").stream()}
    reports = {d.id: d.to_dict() for d in db.collection("reports").stream()}
    names = {d.id: (d.to_dict() or {}).get("name", "") for d in db.collection("people").stream()}
    road = db.document("config/roadmap").get()
    build = ""
    if road.exists:
        try:
            build = (json.loads(road.to_dict().get("json") or "{}").get("meta") or {}).get("current", "")
        except ValueError:
            pass
    build = build or os.environ.get("TESTER_BUILD", "") or stable

    # Skip if someone already pressed Reset by hand in the last few hours.
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=RECENT_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = [d.to_dict() for d in db.collection("log").order_by("at", direction=firestore.Query.DESCENDING).limit(60).stream()]
    hand = [e for e in recent if e.get("kind") == "team" and str(e.get("text", "")).startswith("reset the checklist") and str(e.get("at", "")) >= cutoff]

    snap = snapshot(items, reports, names, build)
    c = snap["counts"]
    print(f"Stable {stable}: round for build {build or '?'} has {c['ok']} working, {c['bad']} broken, {c['open']} not tested, "
          f"{len(snap['testers'])} tester(s), {len(reports)} report doc(s), {len(items)} check(s).")
    mref = db.document("config/autoreset")
    msnap = mref.get()
    marker = msnap.to_dict() if msnap.exists else {}
    print(f"Last automatic reset was for Stable {marker.get('stable') or '(none yet)'}.")
    if hand:
        print(f"A dev already reset the checklist at {hand[0].get('at')}, so no automatic reset is needed.")
    if dry:
        print("Dry run: nothing written.")
        return

    @firestore.transactional
    def claim(tx):
        snap = mref.get(transaction=tx)
        cur = snap.to_dict() if snap.exists else {}
        if cur.get("stable") and vkey(stable) <= vkey(cur["stable"]):
            return False
        tx.set(mref, {"stable": stable, "at": now_iso(), "by": "auto"})
        return True

    won = claim(db.transaction())
    if not won:
        print(f"A dev's open tracker page already reset for Stable {stable}; nothing to do.")
    if won and not hand:
      try:
        if c["ok"] + c["bad"] > 0 or snap["testers"]:
            bid = re.sub(r"[^0-9A-Za-z.-]", "", build or "build") + "-" + b36(int(time.time() * 1000))
            snap["stable"] = stable
            db.document("builds/" + bid).set(snap)
            print("Saved the round to Build history as builds/" + bid)
        blank = {"status": "open", "note": "", "ver": "", "by": "", "at": "", "fixed": False, "retest": False, "sev": ""}
        ops = [("u", db.document("items/" + i)) for i in items] + [("d", db.document("reports/" + u)) for u in reports]
        for i in range(0, len(ops), 400):
            b = db.batch()
            for kind, ref in ops[i:i + 400]:
                if kind == "u":
                    b.update(ref, blank)
                else:
                    b.delete(ref)
            b.commit()
        db.collection("log").add({"at": now_iso(), "by": "auto", "kind": "team",
                                  "text": f"reset the checklist automatically because Stable {stable} was released"})
        print(f"Reset {len(items)} checks and cleared {len(reports)} tester report doc(s).")
      except Exception:
        mref.set(marker) if marker else mref.delete()  # let the next run (or a dev's page) try again
        raise
    with open(MARK, "w", encoding="utf-8") as f:
        json.dump({"stable": stable, "at": now_iso(), "skipped": bool(hand)}, f, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
