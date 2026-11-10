"""Today's search slice.

  python3 scripts/rotation.py      -> writes out/queries.json and advances the pointers in
                                      STATE_DIR/rotation.json (committed with the run)
Queries come from sources.yaml (search). Connector searches look back 3 days (the run is daily;
Monday looks back 4 days to cover the weekend).
"""
from common import outp, load, save, now_et, dp
from config import get as cfg_get


def take(lst, start, n):
    if not lst:
        return [], 0
    out = [lst[(start + i) % len(lst)] for i in range(min(n, len(lst)))]
    return out, (start + n) % len(lst)


def main():
    cfg = dict(cfg_get("sources.search"))             # queries: CONFIG_DIR/sources.yaml (read-only)
    state = load(dp("rotation.json"), {"pointer": {"connector": 0, "web": 0, "short_term": 0}})
    ptr, per = state["pointer"], cfg["per_run"]
    conn, ptr["connector"] = take(cfg["connector_queries"], ptr["connector"], per["connector"])
    web, ptr["web"] = take(cfg["web_queries"], ptr["web"], per["web"])
    st, ptr["short_term"] = take(cfg["short_term_queries"], ptr["short_term"], per["short_term"])
    days = 4 if now_et().weekday() == 0 else 3
    q = {"posted_within_days": days, "connector": conn, "web": web, "short_term": st}
    save(outp("queries.json"), q)
    save(dp("rotation.json"), state)                   # pointers: run state, committed with the run
    print(f"{len(conn)} connector, {len(web)} web, {len(st)} short-term queries; posted within {days} days")


if __name__ == "__main__":
    main()
