"""python -m publisher --network shadownet [--networks-index] --target dir:C:\\path | r2:<bucket>"""
import argparse
import logging
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m publisher")
    ap.add_argument("--network", required=True, help="key of apps/homebase/config.NETWORKS")
    ap.add_argument("--target", required=True, help="dir:<path> or r2:<bucket>")
    ap.add_argument("--networks-index", action="store_true", help="also publish networks.json")
    ap.add_argument("--credentials", default=None, help="service account file (default: homebase.json)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=os.getenv("PUBLISHER_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("publisher")

    sys.path.insert(0, os.getcwd())
    from apps.homebase import config as hb_config
    if args.network not in hb_config.NETWORKS:
        ap.error(f"unknown network {args.network!r}; known: {', '.join(hb_config.NETWORKS)}")
    net = hb_config.NETWORKS[args.network]
    N, C = net["firestore_doc_name"], net["dao_collection_name"]
    if args.network == "localhost" and not os.getenv("FIRESTORE_EMULATOR_HOST"):
        ap.error("network localhost needs FIRESTORE_EMULATOR_HOST (the emulator to read)")

    from .targets import make_target
    from .core import Publisher
    from .notify import Notifier
    from .source import init_db, attach

    target = make_target(args.target)
    notifier = None
    url = os.getenv("NOTIFY_URL")
    if url:
        secret = os.getenv("NOTIFY_SECRET")
        if not secret:
            ap.error("NOTIFY_URL is set but NOTIFY_SECRET is not")
        notifier = Notifier(url, secret)
    pub = Publisher(N, C, target, notifier=notifier, networks_index=args.networks_index)
    log.info("publishing %s (%s) to %s%s%s", N, C, args.target,
             " with networks.json" if args.networks_index else "",
             f", notifying {url}" if url else ", no notify")

    db = init_db(args.credentials or hb_config.FIREBASE_CREDENTIALS)
    listeners = attach(db, C, pub, args.networks_index)
    if pub.fatal is not None:
        raise SystemExit(f"fatal: {pub.fatal}")
    # a listener whose stream died would leave the files silently stale: exit and let the
    # supervisor restart (a restart costs one full read and sends only the real diff)
    pub.health = lambda: next((f"listener {li.name} stopped" for li in listeners if li.dead()), None)
    if notifier is not None:
        notifier.start()
    pub.startup()
    try:
        pub.run()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        for li in listeners:
            li.stop()
        if notifier is not None:
            notifier.stop()


if __name__ == "__main__":
    main()
