#!/usr/bin/env python3
"""
LOWEST ODDS FINDER  (PrizePicks + Pick6)
-----------------------------------------
Finds player props whose AVERAGE price across the major sportsbooks
(DraftKings, FanDuel, Fanatics, BetMGM, Caesars, theScore Bet, BetRivers, Hard Rock)
is -500 or lower, and that PrizePicks or Pick6 also offer.

One run -> two reports that open in your browser:
    lowest_odds_PrizePicks.html
    lowest_odds_Pick6.html

HOW TO RUN
    1. Put this file on your Desktop.
    2. Open Terminal and type:   cd ~/Desktop
    3. Then type:                python3 lowest_odds.py
    4. First time only: paste your Odds API key. It gets saved.
    New key later?  python3 lowest_odds.py newkey

No installs needed. Only uses what comes with Python.
"""

import json, os, sys, time, html, unicodedata, re, webbrowser, ssl, subprocess, tempfile
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

# ================== SETTINGS (safe to change) ==================
THRESHOLD = -500            # default cutoff: only show lines whose AVERAGE price is this low or lower
APP_THRESHOLD = {           # per-app cutoffs
    "prizepicks": -500,
    "pick6": -500,
}
# Major US sportsbooks used for the average. PrizePicks + Pick6 + these 8 = 10 bookmakers,
# which the Odds API bills the same as one region. Adding an 11th doubles the credit cost.
SPORTSBOOKS = ["draftkings", "fanduel", "fanatics", "betmgm",
               "williamhill_us", "espnbet", "betrivers", "hardrockbet"]
REQUIRE_BOTH_BOOKS = False  # True = EVERY book above must carry the line
HOURS_AHEAD = 30            # only games starting within this many hours
DEFAULT_SPORTS = ["NBA"]    # what runs when you just press Enter
SPORTS = {                  # Odds API sport key -> short label
    "baseball_mlb": "MLB",
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL",
    "icehockey_nhl": "NHL",
    "basketball_wnba": "WNBA",
    "americanfootball_ncaaf": "NCAAF",
    "basketball_ncaab": "NCAAB",
}
# ===============================================================

DFS_BOOKS = {"prizepicks": "PrizePicks", "pick6": "Pick6"}
DFS_TXT = "/".join(DFS_BOOKS.values())
BOOK_LABEL = {"draftkings": "DK", "fanduel": "FD", "fanatics": "Fanatics", "betmgm": "MGM",
              "williamhill_us": "Caesars", "espnbet": "theScore", "betrivers": "Rivers",
              "hardrockbet": "Hard Rock"}
BOOK_NAME = {"draftkings": "DraftKings", "fanduel": "FanDuel", "fanatics": "Fanatics",
             "betmgm": "BetMGM", "williamhill_us": "Caesars", "espnbet": "theScore Bet",
             "betrivers": "BetRivers", "hardrockbet": "Hard Rock Bet"}
BOOKS_TXT = "the sportsbooks"
API = "https://api.the-odds-api.com/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, ".odds_api_key")
# When this runs on GitHub (the phone "Run" button), answers come from settings instead of typing.
CI = os.environ.get("GITHUB_ACTIONS") == "true"
OUT = os.environ.get("LOWEST_ODDS_OUT") or HERE
os.makedirs(OUT, exist_ok=True)
RUN = {"counts": {}, "message": "", "stamp": ""}


def ask(prompt, env_name, default=""):
    if CI:
        return os.environ.get(env_name, default).strip()
    return input(prompt).strip()
credits_left = "?"
DBG = {"listed": {}}


class Tee:
    """Shows output on screen AND saves it to lowest_odds_debug.txt."""
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.out = sys.stdout
    def write(self, x):
        self.out.write(x)
        self.f.write(x)
        self.f.flush()
    def flush(self):
        self.out.flush()


# ---------- secure connection (fixes the Mac "CERTIFICATE_VERIFY_FAILED" error) ----------
def make_ssl():
    # 1) certifi, if it happens to be installed
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    # 2) on a Mac, borrow the certificates macOS already trusts
    if sys.platform == "darwin":
        try:
            pem = subprocess.run(
                ["security", "find-certificate", "-a", "-p",
                 "/System/Library/Keychains/SystemRootCertificates.keychain"],
                capture_output=True, text=True, timeout=20).stdout
            if "BEGIN CERTIFICATE" in pem:
                f = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
                f.write(pem)
                f.close()
                return ssl.create_default_context(cafile=f.name)
        except Exception:
            pass
    # 3) Python's normal setup
    return ssl.create_default_context()


SSL_CTX = make_ssl()


# ---------- helpers ----------
def get_key():
    if "newkey" in [a.lower() for a in sys.argv[1:]] and os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)
        print("Old key deleted.")
    k = os.environ.get("ODDS_API_KEY", "").strip()
    if k:
        return k
    if os.path.exists(KEY_FILE):
        k = open(KEY_FILE).read().strip()
        if k:
            return k
    if CI:
        sys.exit("The ODDS_API_KEY secret is missing. Add it in the repo: Settings > Secrets and variables > Actions.")
    k = input("Paste your Odds API key and press Enter: ").strip()
    if not k:
        sys.exit("No key entered. Stopping.")
    try:
        with open(KEY_FILE, "w") as f:
            f.write(k)
        print("  Key saved. You won't be asked again.\n")
    except OSError:
        pass
    return k


def get(path, key, **params):
    global credits_left
    params["apiKey"] = key
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30, context=SSL_CTX) as r:
                credits_left = r.headers.get("x-requests-remaining", credits_left)
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code == 401:
                if os.path.exists(KEY_FILE):
                    os.remove(KEY_FILE)
                sys.exit("API key was rejected. Run again and paste the right key.")
            if e.code == 429:
                time.sleep(2 + attempt * 2)
                continue
            if e.code == 422:          # market/event not available - just skip
                return None
            print(f"  ! API error {e.code}: {body[:150]}")
            return None
        except Exception as e:
            print(f"  ! Network problem ({e}). Retrying...")
            time.sleep(2)
    return None


def norm_name(n):
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode()
    n = n.lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


SIDE_MAP = {"over": "Over", "higher": "Over", "more": "Over", "above": "Over", "yes": "Over",
            "under": "Under", "lower": "Under", "less": "Under", "below": "Under", "no": "Under"}


def norm_side(x):
    return SIDE_MAP.get((x or "").strip().lower(), (x or "").strip().title())


def base_market(m):
    return m[:-len("_alternate")] if m.endswith("_alternate") else m


def is_player_market(m):
    return m.startswith(("player_", "batter_", "pitcher_"))


NICE = {
    "batter_hits_runs_rbis": "Hits+Runs+RBIs",
    "batter_rbis": "RBIs",
    "batter_total_bases": "Total Bases",
    "batter_runs_scored": "Runs",
    "batter_home_runs": "Home Runs",
    "batter_strikeouts": "Batter Ks",
    "pitcher_strikeouts": "Pitcher Ks",
    "pitcher_outs": "Pitcher Outs",
    "pitcher_hits_allowed": "Hits Allowed",
    "pitcher_earned_runs": "Earned Runs",
    "player_points_rebounds_assists": "Pts+Reb+Ast",
    "player_points_rebounds": "Pts+Reb",
    "player_points_assists": "Pts+Ast",
    "player_rebounds_assists": "Reb+Ast",
    "player_threes": "3-Pointers",
    "player_shots_on_goal": "Shots on Goal",
}


def nice_market(m):
    if m in NICE:
        return NICE[m]
    for p in ("player_", "batter_", "pitcher_"):
        if m.startswith(p):
            m = m[len(p):]
    return m.replace("_", " ").title()


def to_american(prob):
    return -100 * prob / (1 - prob) if prob >= 0.5 else 100 * (1 - prob) / prob


def avg_price(prices):
    """Average the books by their implied chance, then turn it back into odds."""
    probs = [implied(p) for p in prices]
    return to_american(sum(probs) / len(probs))


def cut(bk):
    return APP_THRESHOLD.get(bk, THRESHOLD)


def implied(price):
    p = float(price)
    return (-p / (-p + 100)) if p < 0 else (100 / (p + 100))


def fmt_odds(p):
    if p is None:
        return "—"
    p = int(round(p))
    return f"+{p}" if p > 0 else str(p)


def fmt_line(x):
    return str(int(x)) if float(x).is_integer() else str(x)


# ---------- main work ----------
def now_et():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def write_index():
    """Home page for the phone: links to each report plus the run log."""
    stamp = RUN["stamp"] or now_et().strftime("%a %b %d at %I:%M %p").replace(" 0", " ")
    links = "".join(
        f'<a class="rep" href="lowest_odds_{html.escape(app)}.html"><b>{html.escape(app)}</b>'
        f'<span>{n} line{"s" if n != 1 else ""} at {cut(k)} or better</span></a>'
        for k, app in DFS_BOOKS.items() for n in [RUN["counts"].get(app)] if n is not None)
    msg = f'<p class="msg">{html.escape(RUN["message"])}</p>' if RUN["message"] else ""
    page = INDEX_TEMPLATE.replace("__STAMP__", html.escape(stamp)).replace("__LINKS__", links).replace("__MSG__", msg)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


INDEX_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lowest odds</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;600&family=Barlow+Condensed:wght@700&display=swap" rel="stylesheet">
<style>
:root{--paper:#F2F4F1;--surface:#FFFFFF;--ink:#13221B;--muted:#5E6E66;--rule:#D9E0DB;--turf:#1D6B48;--amber:#8F5D00;--amber-soft:#F4EAD3}
@media (prefers-color-scheme: dark){:root{--paper:#101814;--surface:#16211C;--ink:#E4ECE7;--muted:#93A49B;--rule:#26352E;--turf:#4FBF8A;--amber:#E2B45A;--amber-soft:#3A2F17}}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 Barlow,system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:32px 18px}
h1{font:700 46px/1 "Barlow Condensed",sans-serif;margin:0 0 6px}
.when{color:var(--muted);margin:0 0 24px}
.rep{display:block;background:var(--surface);border:1px solid var(--rule);border-left:6px solid var(--turf);border-radius:12px;
  padding:18px 18px;margin-bottom:12px;color:var(--ink);text-decoration:none}
.rep b{display:block;font:700 30px/1.05 "Barlow Condensed",sans-serif}
.rep span{color:var(--muted)}
.msg{background:var(--amber-soft);color:var(--amber);border-radius:10px;padding:12px 14px;font-weight:600}
.small{margin-top:22px;font-size:14px;color:var(--muted)}
.small a{color:var(--muted)}
a:focus-visible{outline:2px solid var(--turf);outline-offset:2px}
</style></head><body><main>
<h1>Lowest odds</h1><p class="when">Last run __STAMP__ (Eastern)</p>
__MSG____LINKS__
<p class="small">To refresh: GitHub app, this repo, Actions, Lowest odds, Run workflow. Takes about 2 minutes.
<br><a href="lowest_odds_debug.txt">Run log</a> &nbsp; <a href="lowest_odds_all_lines.csv">Every line (CSV)</a></p>
</main></body></html>"""


def main():
    sys.stdout = Tee(os.path.join(OUT, "lowest_odds_debug.txt"))
    print(f"\n=== LOWEST ODDS FINDER ({DFS_TXT}) ===\n")
    key = get_key()

    active = get("/sports", key)                # free call
    if active is None:
        sys.exit("\nCouldn't connect to the Odds API. Check your internet and try again.\n"
                 "If you see CERTIFICATE_VERIFY_FAILED again, run this once in Terminal:\n"
                 "    open \"/Applications/Python 3*/Install Certificates.command\"")
    active_keys = {s["key"] for s in active if s.get("active")}
    sports = [s for s in SPORTS if s in active_keys]
    if not sports:
        sys.exit("None of the sports in SETTINGS are in season right now.")
    print("In season: " + ", ".join(SPORTS[s] for s in sports))
    default = ",".join(DEFAULT_SPORTS)
    pick = ask(f"Which sports? (Enter = {default}, 'all' = everything, or type like nba,nfl): ", "SPORTS").lower()
    if pick != "all":
        wanted = {w.strip() for w in (pick or default.lower()).split(",")}
        sports = [s for s in sports if SPORTS[s].lower() in wanted]
        if not sports:
            sys.exit(f"{default if not pick else pick.upper()} has no games listed right now. Stopping.")

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=HOURS_AHEAD)
    all_books = ",".join(SPORTSBOOKS + list(DFS_BOOKS))

    # 1) find games (free)
    games = []
    for s in sports:
        evs = get(f"/sports/{s}/events", key) or []
        for e in evs:
            t = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
            if now < t <= cutoff:
                games.append((s, e, t))
    if not games:
        sys.exit(f"No games in the next {HOURS_AHEAD} hours.")
    print(f"\nFound {len(games)} upcoming games. Checking which props each app has...")

    # 2) see which prop markets each book has for each game (1 credit per game)
    plan = []
    for s, e, t in games:
        info = get(f"/sports/{s}/events/{e['id']}/markets", key, bookmakers=all_books)
        if not info:
            continue
        have = {}
        for b in info.get("bookmakers", []):
            have[b["key"]] = {m["key"] for m in b.get("markets", []) if is_player_market(m["key"])}
        for bk_, ms_ in have.items():
            DBG["listed"].setdefault(bk_, set()).update(ms_)
        sb_bases = {base_market(m) for b in SPORTSBOOKS for m in have.get(b, set())}
        dfs_bases = {base_market(m) for b in DFS_BOOKS for m in have.get(b, set())}
        shared = sb_bases & dfs_bases
        if not shared:
            continue
        mkts = sorted({m for ms in have.values() for m in ms if base_market(m) in shared})
        plan.append((s, e, t, mkts))

    if not plan:
        sys.exit(f"No props are shared between {BOOKS_TXT} and {DFS_TXT} right now.")
    est = sum(len(m) for *_, m in plan)
    print(f"\n{len(plan)} games have matching props. Pulling odds will cost about {est} credits.")
    print(f"Credits left on your plan: {credits_left}")
    if CI:
        cap = int(os.environ.get("MAX_CREDITS", "500") or 500)
        if est > cap:
            sys.exit(f"This run would cost about {est} credits, over your limit of {cap}. Raise the limit or pick fewer sports.")
    elif input("Go ahead? (Enter = yes, n = no): ").strip().lower().startswith("n"):
        sys.exit("Stopped. No odds pulled.")

    # 3) pull the odds
    sb = {}                                   # (name, mkt, side, line) -> {book: price}
    dfs = {b: {} for b in DFS_BOOKS}           # book -> key -> row
    for i, (s, e, t, mkts) in enumerate(plan, 1):
        game = f"{e['away_team']} @ {e['home_team']}"
        print(f"  [{i}/{len(plan)}] {SPORTS[s]}  {game}")
        data = get(f"/sports/{s}/events/{e['id']}/odds", key,
                   bookmakers=all_books, markets=",".join(mkts), oddsFormat="american")
        if not data:
            continue
        for b in data.get("bookmakers", []):
            bk = b["key"]
            for m in b.get("markets", []):
                mk = base_market(m["key"])
                alt = m["key"].endswith("_alternate")
                for o in m.get("outcomes", []):
                    player = o.get("description") or ""
                    if not player or o.get("point") is None or o.get("price") is None:
                        continue
                    side = norm_side(o["name"])
                    k = (norm_name(player), mk, side, float(o["point"]))
                    price = float(o["price"])
                    if bk in SPORTSBOOKS:
                        cur = sb.setdefault(k, {})
                        cur[bk] = min(price, cur.get(bk, 9999))
                    elif bk in DFS_BOOKS:
                        dfs[bk][k] = {
                            "player": player, "market": nice_market(mk), "side": side,
                            "line": float(o["point"]), "dfs_price": price, "alt": alt,
                            "sport": SPORTS[s], "game": game, "start": t,
                        }

    # 4) match: DFS line that DK/FD price at THRESHOLD or lower
    ladder = {}                               # (name, mkt, side) -> sorted book lines
    for (n_, mk_, sd_, pt_) in sb:
        ladder.setdefault((n_, mk_, sd_), []).append(pt_)
    for v in ladder.values():
        v.sort()

    def book_prices(k):
        """Exact book line if there is one; otherwise the nearest HARDER book line
        (Over: next line up, Under: next line down), so the price shown is a floor."""
        if k in sb:
            return sb[k], None
        pts = ladder.get(k[:3], [])
        if k[2] == "Over":
            harder = [p for p in pts if p > k[3]]
            use = min(harder) if harder else None
        elif k[2] == "Under":
            harder = [p for p in pts if p < k[3]]
            use = max(harder) if harder else None
        else:
            use = None
        if use is None:
            return None, None
        return sb[k[:3] + (use,)], use

    results = {}
    for bk, rows in dfs.items():
        out = []
        for k, r in rows.items():
            q, used = book_prices(k)
            if not q:
                continue
            if REQUIRE_BOTH_BOOKS and not all(b in q for b in SPORTSBOOKS):
                continue
            avg = avg_price(q.values())
            if avg > cut(bk):
                continue
            row = dict(r)
            row["books"] = q
            row["est_from"] = used
            row["deep"] = avg
            row["prob"] = implied(row["deep"])
            other = [o for o in DFS_BOOKS if o != bk]
            row["also"] = [DFS_BOOKS[o] for o in other if k in dfs[o]]
            out.append(row)
        out.sort(key=lambda x: x["deep"])
        results[bk] = out

    # 4b) quick check so an empty list can be explained
    sb_names = {k[0] for k in sb}
    print("\n=== CHECK ===")
    for bk_ in SPORTSBOOKS + list(DFS_BOOKS):
        ms_ = sorted(DBG["listed"].get(bk_, set()))
        print(f"{bk_} listed {len(ms_)} prop markets: {', '.join(ms_[:12])}{' ...' if len(ms_) > 12 else ''}")
    print(f"Sportsbook player lines pulled: {len(sb)}")
    for b_ in SPORTSBOOKS:
        n_ = sum(1 for q_ in sb.values() if b_ in q_)
        print(f"  {BOOK_NAME.get(b_, b_):<14} {n_:>6} lines" + ("   <- nothing came back for this book" if n_ == 0 else ""))
    for bk, rows in dfs.items():
        name = DFS_BOOKS[bk]
        alts = sum(1 for r in rows.values() if r["alt"])
        same = [(avg_price(sb[k].values()), r) for k, r in rows.items() if k in sb]
        near = sum(1 for k in rows if k not in sb and book_prices(k)[0])
        unknown = sorted({r["player"] for k, r in rows.items() if k[0] not in sb_names})
        print(f"\n{name}: {len(rows)} lines pulled ({alts} goblin/alt lines)")
        print(f"  same line also on {BOOKS_TXT}: {len(same)}")
        print(f"  no exact match, priced off nearest harder line: {near}")
        print(f"  average {cut(bk)} or lower: {len(results[bk])}")
        if unknown:
            print(f"  players not found on {BOOKS_TXT} at all ({len(unknown)}): {', '.join(unknown[:8])}"
                  + (" ..." if len(unknown) > 8 else ""))
        if same and not results[bk]:
            same.sort(key=lambda x: x[0])
            print(f"  closest ones (average {BOOKS_TXT} price on a matching line):")
            for p, r in same[:5]:
                print(f"    {fmt_odds(p):>6}  {r['player']} {r['side']} {fmt_line(r['line'])} {r['market']}")

    # 4c) save EVERY app line (matched or not) so odd results can be checked later
    import csv
    sb_lines = {}
    for (n_, mk_, sd_, pt_) in sb:
        sb_lines.setdefault((n_, mk_, sd_), set()).add(pt_)
    with open(os.path.join(OUT, "lowest_odds_all_lines.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["app", "player", "stat", "side", "line", "alt_line", "app_price"]
                   + [BOOK_LABEL.get(b, b) for b in SPORTSBOOKS]
                   + ["average", "priced_off_line", "book_lines_for_this_player_stat"])
        for bk, rows in dfs.items():
            for k, r in sorted(rows.items(), key=lambda kv: (kv[1]["player"], kv[1]["market"], kv[1]["line"])):
                q, used = book_prices(k)
                q = q or {}
                avail = sorted(sb_lines.get(k[:3], set()))
                w.writerow([DFS_BOOKS[bk], r["player"], r["market"], r["side"], fmt_line(r["line"]),
                            "yes" if r["alt"] else "", fmt_odds(r["dfs_price"])]
                           + [fmt_odds(q.get(b)) if b in q else "" for b in SPORTSBOOKS]
                           + [fmt_odds(avg_price(q.values())) if q else "", fmt_line(used) if used is not None else "",
                              " ".join(fmt_line(x) for x in avail)])

    # 5) write reports
    stamp = now_et().strftime("%a %b %d at %I:%M %p").replace(" 0", " ")
    RUN["stamp"] = stamp
    paths = []
    for bk, rows in results.items():
        RUN["counts"][DFS_BOOKS[bk]] = len(rows)
        p = os.path.join(OUT, f"lowest_odds_{DFS_BOOKS[bk]}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(build_html(DFS_BOOKS[bk], rows, stamp, cut(bk)))
        paths.append(p)

        print(f"\n--- {DFS_BOOKS[bk]}: {len(rows)} lines averaging {cut(bk)} or lower ---")
        for r in rows[:10]:
            print(f"  {fmt_odds(r['deep']):>6}  {r['player']:<24} {r['side']:<5} "
                  f"{fmt_line(r['line']):<5} {r['market']:<16} {r['sport']}")

    print(f"\nCredits left: {credits_left}")
    if CI:
        write_index()
        return
    print("Opening reports in your browser...")
    for p in paths:
        webbrowser.open("file://" + p)


# ---------- HTML ----------
def build_html(app, rows, stamp, threshold):
    """Writes one self-contained report page. Rows go in as JSON and the page draws itself."""
    other_app = [v for v in DFS_BOOKS.values() if v != app]
    data = []
    for r in rows:
        data.append({
            "player": r["player"],
            "market": r["market"],
            "side": "MORE" if r["side"] in ("Over", "Yes") else "LESS",
            "line": fmt_line(r["line"]),
            "alt": bool(r["alt"]),
            "est": fmt_line(r["est_from"]) if r.get("est_from") is not None else None,
            "also": r.get("also") or [],
            "sport": r["sport"],
            "game": r["game"],
            "t": int(r["start"].timestamp()),
            "books": {BOOK_LABEL.get(b, b): round(p) for b, p in r["books"].items()},
            "avg": round(r["deep"]),
            "prob": round(r["prob"], 4),
        })
    payload = json.dumps({
        "app": app,
        "other": other_app,
        "threshold": threshold,
        "stamp": stamp,
        "hours": HOURS_AHEAD,
        "books": [BOOK_LABEL.get(b, b) for b in SPORTSBOOKS],
        "bookNames": {BOOK_LABEL.get(b, b): BOOK_NAME.get(b, b) for b in SPORTSBOOKS},
        "rows": data,
    }, ensure_ascii=True).replace("</", "<\\/")
    return (PAGE_TEMPLATE
            .replace("__TITLE__", html.escape(f"{app} locks"))
            .replace("__DATA__", payload))


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#F2F4F1; --surface:#FFFFFF; --ink:#13221B; --muted:#5E6E66; --rule:#D9E0DB;
  --turf:#1D6B48; --turf-ink:#FFFFFF; --plum:#7A3D66; --amber:#8F5D00; --amber-soft:#F4EAD3;
  --sky:#1F5E86; --sky-soft:#DDEAF3; --focus:#1F5E86; --row-hover:#F6F8F6; --shade-base:#FFFFFF;
}
@media (prefers-color-scheme: dark){
  :root{
    --paper:#101814; --surface:#16211C; --ink:#E4ECE7; --muted:#93A49B; --rule:#26352E;
    --turf:#4FBF8A; --turf-ink:#0D1A13; --plum:#D9A2CB; --amber:#E2B45A; --amber-soft:#3A2F17;
    --sky:#7DB8DD; --sky-soft:#1B2D3A; --focus:#7DB8DD; --row-hover:#1A2722; --shade-base:#16211C;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.45 Barlow,system-ui,-apple-system,Segoe UI,sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:1320px;margin:0 auto;padding:28px 20px 120px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px 32px;margin-bottom:20px}
h1{font:700 52px/0.95 "Barlow Condensed",Barlow,sans-serif;margin:0;letter-spacing:-0.5px}
h1 span{display:block;font-weight:500;font-size:22px;color:var(--muted);letter-spacing:0;margin-top:6px}
.summary{max-width:46ch;color:var(--muted);margin:0}
.summary b{color:var(--ink);font-weight:600}

.controls{position:sticky;top:0;z-index:5;background:var(--paper);padding:12px 0;
  display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;border-bottom:1px solid var(--rule)}
.controls input[type=search],.controls select{font:inherit;color:var(--ink);background:var(--surface);
  border:1px solid var(--rule);border-radius:8px;padding:8px 10px;min-height:38px}
.controls input[type=search]{width:220px}
.controls select{max-width:100%}
#game{max-width:300px}
.controls label{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:14px}
.controls .group{display:flex;gap:6px;flex-wrap:wrap}
.chip{font:600 14px Barlow,sans-serif;color:var(--ink);background:var(--surface);border:1px solid var(--rule);
  border-radius:999px;padding:6px 14px;cursor:pointer;min-height:34px}
.chip[aria-pressed=true]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.count{margin-left:auto;color:var(--muted);font-size:14px}

.tablewrap{overflow-x:auto;margin-top:14px;background:var(--surface);border:1px solid var(--rule);border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:1080px}
th{font:600 13px Barlow,sans-serif;color:var(--muted);text-align:left;padding:10px 10px;border-bottom:1px solid var(--rule);
  white-space:nowrap;background:var(--surface);position:sticky;top:0}
th.book,td.book{text-align:center;width:64px}
td{padding:10px;border-top:1px solid var(--rule);vertical-align:middle}
tbody tr.line:hover{background:var(--row-hover)}
tr.gamehead td{background:var(--paper);font:600 15px Barlow,sans-serif;padding:14px 10px 8px;border-top:2px solid var(--rule)}
tr.gamehead .when{font:700 18px "Barlow Condensed",sans-serif;margin-right:10px}
tr.gamehead .n{color:var(--muted);font-weight:500;margin-left:8px}

td.pick{width:36px;text-align:center}
td.pick input{width:18px;height:18px;accent-color:var(--turf);cursor:pointer}
td.chance{width:136px;min-width:136px;padding:0}
.heat{display:flex;flex-direction:column;justify-content:center;height:100%;min-height:60px;padding:8px 12px;color:var(--ink)}
.heat .pct{font:700 26px/1 "Barlow Condensed",sans-serif}
.heat .avg{font-size:12px;margin-top:4px;opacity:.9;line-height:1.25}
.heat .avg span{display:block}
.heat.hot{color:var(--turf-ink)}
.player{min-width:180px}
.player b{font-weight:600;font-size:16px}
.player .game{display:block;color:var(--muted);font-size:13px}
table.grouped .player .game{display:none}
.what{white-space:nowrap}
.side{font:700 13px Barlow,sans-serif;padding:2px 8px;border-radius:6px;margin-right:6px;display:inline-block}
.side.MORE{background:color-mix(in oklab,var(--turf) 16%,var(--surface));color:var(--turf)}
.side.LESS{background:color-mix(in oklab,var(--plum) 16%,var(--surface));color:var(--plum)}
.what .ln{font:700 19px "Barlow Condensed",sans-serif;margin-right:4px}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.tag{font-size:12px;font-weight:600;border-radius:5px;padding:1px 7px}
.tag.alt{background:var(--amber-soft);color:var(--amber)}
.tag.est{background:var(--sky-soft);color:var(--sky)}
.tag.also{border:1px solid var(--rule);color:var(--muted)}
td.book{font:600 15px "Barlow Condensed",sans-serif;padding:6px 4px}
td.book .cell{border-radius:6px;padding:6px 2px}
td.book .none{color:var(--rule)}
td.start{white-space:nowrap;color:var(--muted);font-size:14px}

.empty{padding:48px 20px;text-align:center;color:var(--muted)}
.empty b{display:block;color:var(--ink);font-size:18px;margin-bottom:6px}
.notes{color:var(--muted);font-size:13px;max-width:78ch;margin-top:16px}
.notes p{margin:6px 0}

.tray{position:fixed;left:0;right:0;bottom:0;z-index:10;background:var(--ink);color:var(--paper);
  transform:translateY(110%);transition:transform .2s ease}
.tray.open{transform:none}
.tray .in{max-width:1320px;margin:0 auto;padding:14px 20px;display:flex;flex-wrap:wrap;gap:8px 28px;align-items:center}
.tray .legs{flex:1 1 420px;font-size:14px}
.tray .legs div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tray .big{font:700 30px/1 "Barlow Condensed",sans-serif}
.tray .lbl{font-size:12px;opacity:.75}
.tray .warn{color:#F2C46A;font-size:13px;font-weight:600}
.tray button{font:600 14px Barlow,sans-serif;background:transparent;color:var(--paper);border:1px solid currentColor;
  border-radius:8px;padding:7px 12px;cursor:pointer}

:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
@media (prefers-reduced-motion: reduce){.tray{transition:none}}
@media (max-width:640px){
  h1{font-size:40px}
  .controls input[type=search]{width:100%}
  .count{margin-left:0}
}
</style></head>
<body>
<div class="wrap">
  <header>
    <h1 id="title"></h1>
    <p class="summary" id="summary"></p>
  </header>

  <div class="controls" role="region" aria-label="Filters">
    <input type="search" id="q" placeholder="Search player" aria-label="Search player">
    <div class="group" id="sports"></div>
    <select id="game" aria-label="Game"><option value="">All games</option></select>
    <select id="minbooks" aria-label="Books carrying the line"></select>
    <label><input type="checkbox" id="std"> Standard lines only</label>
    <label><input type="checkbox" id="exact"> Exact matches only</label>
    <select id="sort" aria-label="Sort">
      <option value="odds">Strongest first</option>
      <option value="time">By game time</option>
      <option value="books">Most books agree</option>
      <option value="player">Player A–Z</option>
    </select>
    <span class="count" id="count" aria-live="polite"></span>
  </div>

  <div class="tablewrap">
    <table>
      <thead><tr id="headrow"></tr></thead>
      <tbody id="tb"></tbody>
    </table>
    <div class="empty" id="empty" hidden></div>
  </div>

  <div class="notes" id="notes"></div>
</div>

<div class="tray" id="tray" aria-live="polite">
  <div class="in">
    <div class="legs" id="legs"></div>
    <div><div class="big" id="both"></div><div class="lbl">chance they all hit</div></div>
    <div><div class="big" id="fair"></div><div class="lbl">break-even payout</div></div>
    <div class="warn" id="warn"></div>
    <button type="button" id="clear">Clear picks</button>
  </div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const rows = D.rows.map((r, i) => Object.assign({id: i, nbooks: Object.keys(r.books).length}, r));
const picked = new Set();
let sport = 'ALL';

const fmtOdds = p => p > 0 ? '+' + p : '−' + Math.abs(p);
const pct = p => (p * 100).toFixed(1) + '%';
const implied = o => o < 0 ? -o / (-o + 100) : 100 / (o + 100);
const when = t => new Date(t * 1000).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'});
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// heat: 70% chance = no tint, 95% = full turf
function heat(p) {
  const k = Math.max(0, Math.min(1, (p - 0.70) / 0.25));
  return {mix: Math.round(8 + k * 82), hot: k > 0.55};
}

// header + summary
document.getElementById('title').innerHTML = esc(D.app) + ' locks<span>Lines the sportsbooks price at ' +
  fmtOdds(D.threshold) + ' or better</span>';
const thrPct = pct(implied(D.threshold));
document.getElementById('summary').innerHTML = '<b>' + rows.length + ' lines</b> where the average of ' +
  D.books.length + ' sportsbooks is ' + fmtOdds(D.threshold) + ' or better (about ' + thrPct +
  ' to hit). Games in the next ' + D.hours + ' hours, pulled ' + esc(D.stamp) + '.';

// controls
const sports = [...new Set(rows.map(r => r.sport))].sort();
const sportsEl = document.getElementById('sports');
if (sports.length > 1) {
  ['ALL', ...sports].forEach(s => {
    const b = document.createElement('button');
    b.className = 'chip'; b.type = 'button'; b.textContent = s === 'ALL' ? 'All sports' : s;
    b.setAttribute('aria-pressed', s === sport);
    b.onclick = () => { sport = s; sportsEl.querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', x === b)); draw(); };
    sportsEl.appendChild(b);
  });
}
const games = [...new Map(rows.map(r => [r.game, r.t])).entries()].sort((a, b) => a[1] - b[1]);
const gameSel = document.getElementById('game');
games.forEach(([g, t]) => { const o = document.createElement('option'); o.value = g; o.textContent = when(t) + '  ' + g; gameSel.appendChild(o); });
const minSel = document.getElementById('minbooks');
for (let n = 1; n <= D.books.length; n++) {
  const o = document.createElement('option'); o.value = n;
  o.textContent = n === 1 ? 'Any number of books' : 'At least ' + n + ' books';
  minSel.appendChild(o);
}
document.getElementById('headrow').innerHTML =
  '<th scope="col"><span class="sr" aria-label="Add to pair"></span></th><th scope="col">Chance</th><th scope="col">Player</th><th scope="col">Pick</th>' +
  D.books.map(b => '<th scope="col" class="book" title="' + esc(D.bookNames[b] || b) + '">' + esc(b) + '</th>').join('') +
  '<th scope="col">Starts</th>';

document.getElementById('notes').innerHTML =
  '<p><b>Chance</b> is the average of the sportsbooks that carry that exact line, turned into a percent. Darker green means a stronger line. Each book’s own price is shaded the same way.</p>' +
  '<p><b>Goblin / lower payout</b> lines are ' + esc(D.app) + '’s easier alternate lines. They pay less than a normal pick, so check the payout in the app.</p>' +
  '<p><b>Books price X</b> means no book had this exact number, so the price shown is for the next harder number. The real odds on this line are at least that good.</p>' +
  '<p><b>Break-even payout</b> in the pick tray: if the app pays more than this for the picks you ticked, the entry is worth it on paper. Same-game picks can move together, so the real chance may differ.</p>';

function visible() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const g = gameSel.value, mb = +minSel.value || 1;
  const std = document.getElementById('std').checked, exact = document.getElementById('exact').checked;
  let v = rows.filter(r =>
    (sport === 'ALL' || r.sport === sport) && (!g || r.game === g) && r.nbooks >= mb &&
    (!std || !r.alt) && (!exact || !r.est) && (!q || r.player.toLowerCase().includes(q)));
  const k = document.getElementById('sort').value;
  const by = {
    odds: (a, b) => b.prob - a.prob,
    time: (a, b) => a.t - b.t || a.game.localeCompare(b.game) || b.prob - a.prob,
    books: (a, b) => b.nbooks - a.nbooks || b.prob - a.prob,
    player: (a, b) => a.player.localeCompare(b.player) || b.prob - a.prob,
  }[k];
  return {v: v.sort(by), grouped: k === 'time'};
}

function rowHtml(r) {
  const h = heat(r.prob);
  const tags = [];
  if (r.alt) tags.push('<span class="tag alt">Goblin / lower payout</span>');
  if (r.est) tags.push('<span class="tag est">Books price ' + esc(r.est) + '</span>');
  r.also.forEach(a => tags.push('<span class="tag also">Also on ' + esc(a) + '</span>'));
  const cells = D.books.map(b => {
    const p = r.books[b];
    if (p === undefined) return '<td class="book"><span class="none" aria-label="not offered">–</span></td>';
    const bh = heat(implied(p));
    return '<td class="book"><div class="cell" style="background:color-mix(in oklab,var(--turf) ' +
      Math.round(bh.mix * 0.8) + '%,var(--shade-base))">' + fmtOdds(p) + '</div></td>';
  }).join('');
  return '<tr class="line" data-id="' + r.id + '">' +
    '<td class="pick"><input type="checkbox" aria-label="Add ' + esc(r.player) + ' to pair"' + (picked.has(r.id) ? ' checked' : '') + '></td>' +
    '<td class="chance"><div class="heat' + (h.hot ? ' hot' : '') + '" style="background:color-mix(in oklab,var(--turf) ' + h.mix + '%,var(--shade-base))">' +
      '<span class="pct">' + pct(r.prob) + '</span><span class="avg"><span>' + fmtOdds(r.avg) + ' average</span><span>' + r.nbooks + ' of ' + D.books.length + ' books</span></span></div></td>' +
    '<td class="player"><b>' + esc(r.player) + '</b><span class="game">' + esc(r.game) + '</span></td>' +
    '<td><div class="what"><span class="side ' + r.side + '">' + r.side + '</span><span class="ln">' + esc(r.line) + '</span>' + esc(r.market) + '</div>' +
      (tags.length ? '<div class="tags">' + tags.join('') + '</div>' : '') + '</td>' +
    cells + '<td class="start">' + when(r.t) + '</td></tr>';
}

function draw() {
  const {v, grouped} = visible();
  const tb = document.getElementById('tb'), empty = document.getElementById('empty');
  let out = '', last = null;
  const per = {};
  v.forEach(r => per[r.game] = (per[r.game] || 0) + 1);
  v.forEach(r => {
    if (grouped && r.game !== last) {
      out += '<tr class="gamehead"><td colspan="' + (5 + D.books.length) + '"><span class="when">' + when(r.t) +
        '</span>' + esc(r.game) + '<span class="n">' + per[r.game] + (per[r.game] === 1 ? ' line' : ' lines') + '</span></td></tr>';
      last = r.game;
    }
    out += rowHtml(r);
  });
  tb.innerHTML = out;
  tb.closest('table').classList.toggle('grouped', grouped);
  document.getElementById('count').textContent = 'Showing ' + v.length + ' of ' + rows.length;
  if (!rows.length) {
    empty.innerHTML = '<b>No lines at ' + fmtOdds(D.threshold) + ' or better right now.</b>Books usually tighten closer to game time, so run it again then.';
    empty.hidden = false;
  } else if (!v.length) {
    empty.innerHTML = '<b>Nothing matches these filters.</b>Clear the search or lower the number of books.';
    empty.hidden = false;
  } else empty.hidden = true;
}

function drawTray() {
  const tray = document.getElementById('tray');
  const legs = [...picked].map(id => rows[id]);
  tray.classList.toggle('open', legs.length > 0);
  if (!legs.length) return;
  const both = legs.reduce((a, r) => a * r.prob, 1);
  document.getElementById('legs').innerHTML = legs.map(r =>
    '<div>' + esc(r.player) + ' — ' + r.side + ' ' + esc(r.line) + ' ' + esc(r.market) + ' (' + pct(r.prob) + ')</div>').join('');
  document.getElementById('both').textContent = pct(both);
  document.getElementById('fair').textContent = (1 / both).toFixed(2) + 'x';
  const gs = legs.map(r => r.game);
  document.getElementById('warn').textContent = new Set(gs).size < gs.length ? 'Two picks are in the same game' : '';
}

document.getElementById('tb').addEventListener('change', e => {
  if (e.target.type !== 'checkbox') return;
  const id = +e.target.closest('tr').dataset.id;
  e.target.checked ? picked.add(id) : picked.delete(id);
  drawTray();
});
document.getElementById('clear').onclick = () => { picked.clear(); draw(); drawTray(); };
['q', 'game', 'minbooks', 'std', 'exact', 'sort'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(el.type === 'search' ? 'input' : 'change', draw);
});
try { const s = localStorage.getItem('lo_sort'); if (s) document.getElementById('sort').value = s; } catch (e) {}
document.getElementById('sort').addEventListener('change', e => { try { localStorage.setItem('lo_sort', e.target.value); } catch (x) {} });
draw();
</script>
</body></html>
"""


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except SystemExit as e:
        if not CI:
            raise
        RUN["message"] = str(e.code) if e.code not in (None, 0) else ""
        print(RUN["message"])
        write_index()
