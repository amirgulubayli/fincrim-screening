#!/usr/bin/env python3
"""Adversarial attacker test (standard library only).

Takes every hit the engine does NOT clear (SAME_ENTITY or REVIEW) plus every labelled true match, and rewrites
the customer's or the hit's data the way a real true match could plausibly look elsewhere: transliteration and
romanisation variants, swapped name order, dropped middle names, a changed (married) surname, US vs UK date
order, year-only birth dates, one-character typos, countries written as codes/demonyms/former states, stripped
diacritics and hyphen/space changes. Any rewrite that makes the engine CLEAR the hit is an unsafe flip.

    python3 adversary.py <data folder> [--engine DIR] [--show N]
"""
import contextlib, copy, io, json, os, random, re, sys, unicodedata, collections

ROMAN = [  # romanisation variants seen on real lists (Arabic, Cyrillic, Persian, Chinese-pinyin/Wade-Giles)
    ["mohammed", "muhammad", "mohamed", "mohammad", "mukhammed", "mehmet", "mohamad"],
    ["yusuf", "yousef", "youssef", "yusif", "jusuf"], ["hussein", "husain", "hussain", "husayn", "hossein"],
    ["abdul", "abdel", "abd al", "abdoul"], ["osama", "usama", "usamah"], ["omar", "umar", "omer"],
    ["ahmed", "ahmad", "akhmed", "ahmet"], ["hassan", "hasan", "khasan"], ["ali", "aly", "alee"],
    ["aleksandr", "alexander", "aleksander", "oleksandr"], ["sergei", "sergey", "serhiy", "serguei"],
    ["yevgeny", "evgeny", "yevgeniy", "evgenii"], ["dmitry", "dmitri", "dmytro"], ["yuri", "yury", "iurii"],
    ["mikhail", "michail", "mykhailo"], ["nikolai", "nikolay", "mykola"], ["vladimir", "volodymyr", "wladimir"],
    ["andrei", "andrey", "andriy"], ["alexei", "aleksey", "oleksiy", "alexey"], ["tatiana", "tatyana", "tetiana"],
    ["igor", "ihor"], ["olga", "olha"], ["natalia", "natalya", "nataliya"], ["kim", "gim"],
    ["zhang", "chang"], ["xi", "hsi"], ["li", "lee"], ["wang", "wong"], ["chen", "chan"], ["zhou", "chou"],
    ["qasim", "kassem", "qassem", "kasim"], ["khalid", "khaled"], ["faisal", "faysal"], ["jamal", "gamal"],
    ["ibrahim", "ebrahim", "ibraheem"], ["suleiman", "sulaiman", "sulayman", "suleyman"],
]
SURNAMES = ["Smith", "Garcia", "Kowalski", "Nguyen", "Okafor", "Petrova", "Haddad", "Silva"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def load_engine(engine_dir):
    sys.path.insert(0, engine_dir)
    import screen  # noqa
    return screen


def strip_acc(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def name_parts(n):
    if "," in n:
        fam, giv = n.split(",", 1)
        return giv.split(), fam.split()
    p = n.split()
    return p[:-1], p[-1:]


def roman_variant(n, rng, groups):
    toks = re.split(r"(\W+)", n)
    out, changed = [], False
    for t in toks:
        g = groups.get(t.lower())
        if g and not changed:
            alt = sorted(x for x in g if x != t.lower())
            if alt:
                v = rng.choice(alt)
                out.append(v.upper() if t.isupper() else v.title())
                changed = True
                continue
        out.append(t)
    return "".join(out) if changed else None


def typo(n, rng):
    letters = [i for i, c in enumerate(n) if c.isalpha()]
    if len(letters) < 4:
        return None
    i = rng.choice(letters[1:-1])
    c = n[i]
    rep = "e" if c.lower() in "aiou" else ("a" if c.lower() == "e" else ("y" if c.lower() == "i" else c + c))
    return n[:i] + rep + n[i + 1:]


def iso_parts(v):
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", str(v or ""))
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(v or ""))
    if m:
        return int(m[3]), int(m[2]), int(m[1])
    return None


def attacks(screen, D, alert, hit, recs, rng, groups):
    """Yield (attack name, list of (object, field, new value))."""
    hn = hit.get("name") or ""
    giv, fam = name_parts(hn)
    if hit.get("entity_type") == "Company":
        giv, fam = [], []                     # name-part attacks are for people
    old_key = " ".join(sorted(re.findall(r"[a-z]+", screen.fold(hn))))

    def rename(new):                          # the list spelling changes; its web search would follow it
        key = " ".join(sorted(re.findall(r"[a-z]+", screen.fold(new))))
        ch = [(hit, "name", new)]
        if key != old_key and old_key in D.search_by_query:
            ch.append((D.search_by_query, key, D.search_by_query[old_key]))
        return ch
    if giv and fam:
        yield "name order swapped", rename(f"{' '.join(fam)}, {' '.join(giv)}" if "," not in hn
                                           else f"{' '.join(giv)} {' '.join(fam)}")
    if len(giv) >= 2:
        yield "middle name dropped", rename(f"{giv[0]} {' '.join(fam)}")
    v = roman_variant(hn, rng, groups) if giv else None
    if v:
        yield "transliteration variant (hit)", rename(v)
    t = typo(hn, rng) if giv else None
    if t:
        yield "one-character typo (hit)", rename(t)
    if strip_acc(hn) != hn:
        yield "diacritics stripped", rename(strip_acc(hn))
    if giv and ("-" in hn or len(hn.split()) >= 2):
        yield "hyphen/space change", rename(hn.replace("-", " ") if "-" in hn else
                                            hn.replace(" ", "-", 1) if "," not in hn else hn.replace(" ", "", 1))
    if fam and giv:
        yield "married surname on list", rename(f"{' '.join(giv)} {rng.choice(SURNAMES)}")
    # customer side
    checks = [k for rid in recs for app in D.applicants_by_cust.get(rid, []) for k in D.kyc_by_app.get(app, [])]
    custs = [D.customers[r] for r in recs]
    for k in checks:
        if k.get("verified_name"):
            v = roman_variant(k["verified_name"], rng, groups)
            if v:
                yield "transliteration variant (verified name)", [(k, "verified_name", v)]
                break
    for obj_list, field in ((checks, "verified_dob"), (custs, "dob")):
        ch = []
        for o in obj_list:
            p = iso_parts(o.get(field))
            if p and p[2] <= 12 and p[1] != p[2]:
                ch.append((o, field, f"{p[1]:02d}/{p[2]:02d}/{p[0]}"))      # US order mm/dd/yyyy
        if ch:
            yield f"US date order ({field})", ch
    ch = [(o, f, str(iso_parts(o.get(f))[0])) for o, f in [(k, "verified_dob") for k in checks] +
          [(c, "dob") for c in custs] if iso_parts(o.get(f))]
    if ch:
        yield "year-only customer DOB", ch
    hd = screen.as_list(hit.get("dobs"))
    if hd:
        new = []
        for x in hd:
            p = iso_parts(x)
            new.append(f"{p[1]:02d}/{p[2]:02d}/{p[0]}" if p and p[2] <= 12 else x)
        yield "US date order (hit)", [(hit, "dobs", "; ".join(new))]
        yield "year-only hit DOB", [(hit, "dobs", "; ".join(x[:4] for x in hd))]
    # countries
    for f in ("countries", "nationality", "citizenship"):
        vals = screen.as_list(hit.get(f))
        if vals:
            cc = [screen.country_code(x) for x in vals]
            alts = {"code": [c or x for c, x in zip(cc, vals)],
                    "former state": [{"RS": "Yugoslavia", "ME": "Serbia and Montenegro", "RU": "USSR",
                                      "UA": "Soviet Union", "CZ": "Czechoslovakia", "SK": "Czechoslovakia",
                                      "MM": "Burma", "CD": "Zaire", "SS": "Sudan", "ER": "Ethiopia"}.get(c, x)
                                     for c, x in zip(cc, vals)],
                    "demonym": [{"GB": "British", "US": "American", "RU": "Russian", "PK": "Pakistani",
                                 "NG": "Nigerian", "IR": "Iranian", "SY": "Syrian", "CN": "Chinese"}.get(c, x)
                                for c, x in zip(cc, vals)]}
            for k, nv in alts.items():
                if nv != vals:
                    yield f"country as {k} ({f})", [(hit, f, "; ".join(nv))]
            break
    for c in custs:
        cc = screen.country_code(c.get("residence_country"))
        if cc and cc != screen.UNKNOWN_COUNTRY:
            yield "customer country as ISO code", [(c, "residence_country", cc)]
            break


def plausible(screen, D, alert, hit, persons, r, labels):
    """A plausible true match: labelled or decided SAME_ENTITY, or the customer's name already agrees with the
    hit's name (the review is about dates/places, not about a different name)."""
    if r["decision"] == "SAME_ENTITY" or labels.get(hit["hit_id"]) == "SAME_ENTITY":
        return True
    hn = [hit.get("name")] + screen.as_list(hit.get("aliases"))
    comp = hit.get("entity_type") == "Company"
    for primary, recs in persons:
        prof = screen.person_profile(D, recs, primary)
        for cn, _ in prof["names"]:
            if any(h and screen.D_agree(D, cn, h, comp) for h in hn) if hasattr(screen, "D_agree") else \
               any(h and D.names.agree(cn, h, comp) for h in hn):
                return True
    return False


def decide(screen, D, alert, hit):
    persons, how = screen.resolve_customer(D, alert)
    r = screen.decide(D, alert, hit, persons, how)
    if r["decision"] == "DIFFERENT_ENTITY":
        r = screen.second_check(D, alert, hit, persons, r)
        if hasattr(screen, "final_gate"):
            r = screen.final_gate(D, alert, hit, persons, r)
    return r, persons


def main(folder, engine_dir, show):
    screen = load_engine(engine_dir)
    screen.loader.reset()
    with contextlib.redirect_stderr(io.StringIO()):
        D = screen.Data(folder)
    groups = {}
    for g in ROMAN:
        for n in g:
            groups[n] = set(g)
    nv = os.path.join(folder, "name_variants.json")
    if os.path.exists(nv):
        for g in json.load(open(nv, encoding="utf-8")).get("given_name_groups", []):
            for n in g:
                groups.setdefault(n.lower(), set()).update(x.lower() for x in g)
    labels = {}
    lp = os.path.join(folder, "practice_labels.jsonl")
    if os.path.exists(lp):
        labels = {r["hit_id"]: r["decision"] for r in map(json.loads, open(lp, encoding="utf-8"))}
    alerts = {a["alert_id"]: a for a in D.alerts}
    targets = []
    for hid in sorted(D.hits):
        hit = D.hits[hid]
        alert = alerts.get(hit.get("alert_id"))
        if not alert:
            continue
        r, persons = decide(screen, D, alert, hit)
        if r["decision"] != "DIFFERENT_ENTITY" or labels.get(hid) == "SAME_ENTITY":
            targets.append((hid, alert, hit, persons, r, plausible(screen, D, alert, hit, persons, r, labels)))
    n_att, unsafe = 0, []
    by_kind = collections.Counter()
    for hid, alert, hit, persons, base, plaus in targets:
        rng = random.Random(hid)
        recs = sorted({x for _, rs in persons for x in rs})
        for name, changes in list(attacks(screen, D, alert, hit, recs, rng, groups)):
            saved = [(o, f, copy.deepcopy(o.get(f)), f in o) for o, f, _ in changes]
            for o, f, v in changes:
                o[f] = v
            try:
                r, _ = decide(screen, D, alert, hit)
            except Exception as e:
                r = {"decision": "CRASH", "reason": repr(e)}
            finally:
                for o, f, v, had in saved:
                    if had:
                        o[f] = v
                    else:
                        o.pop(f, None)
            n_att += 1
            by_kind[name] += 1
            if r["decision"] in ("DIFFERENT_ENTITY", "CRASH"):
                unsafe.append((hid, name, base["decision"], r.get("reason_code"), r["reason"],
                               [(f, v) for _, f, v in changes][:1], plaus))
    print(f"targets: {len(targets)} hits not cleared (incl. {sum(1 for t in targets if labels.get(t[0]) == 'SAME_ENTITY')} labelled true matches)")
    print(f"attacks run: {n_att}")
    pl = [u for u in unsafe if u[6]]
    print(f"plausible true matches among targets: {sum(1 for t in targets if t[5])}")
    print(f"UNSAFE FLIPS on plausible true matches (name already agreed, or SAME/labelled): {len(pl)}")
    print(f"clearances on the other reviews (the name did not agree to begin with): {len(unsafe) - len(pl)}")
    for title, group in (("plausible", pl), ("other", [u for u in unsafe if not u[6]])):
        kinds = collections.Counter(u[1] for u in group)
        for k, c in kinds.most_common():
            print(f"  [{title}] {k}: {c} of {by_kind[k]}")
    for u in (pl + [u for u in unsafe if not u[6]])[:show]:
        print(f"  - {'PLAUSIBLE ' if u[6] else ''}{u[0]} [{u[1]}] was {u[2]} -> {u[3]}: {u[4][:200]}  change={u[5]}")
    return len(pl)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    engine = os.path.dirname(os.path.abspath(__file__))
    show = 12
    if "--engine" in args:
        i = args.index("--engine"); engine = args[i + 1]; del args[i:i + 2]
    if "--show" in args:
        i = args.index("--show"); show = int(args[i + 1]); del args[i:i + 2]
    sys.exit(1 if main(args[0], engine, show) else 0)
