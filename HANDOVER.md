# Handover: this repo is now the homebase indexer

Date: 2026-11-12. Owner from now on: the homebase-app agent.

## What happened

Until today this repo indexed every app from one codebase: homebase, werule, the trustless app and the Autonet agent directory. The apps have drifted apart: werule runs only on Ethereum networks and needs modules, projects, Autonet and elections, while homebase-app runs standard DAOs on Etherlink and needs stable Firestore doc shapes. So the repo was split:

- **This repo (EightRice/indexer, branch `master`)** is the homebase indexer. `master` is exactly what ran in production on Hetzner (`c4dfc82`, formerly branch `ec2-production`), plus this note and a config change.
- **werule's indexer** continues from branch `feat/edge-reads` (44 commits that never reached production) and is moving to its own repository. It will publish to Cloudflare R2 instead of Firestore. When that move is done, `feat/*` branches here can be deleted.
- `archive/master-2025` keeps the old 2025 `master`; `wrapped_token`, `afterme` and `redundancy` are old 2025 lines, never merged.

## What changed in this repo

- `config.yaml` enables only `homebase`. Two apps were switched off:
  - `trustless`: wrote the trustless app's Firebase (trustless-58262), which is being retired. Projects now live in werule.
  - `autonet`: mirrored `Substrate.EndpointUpdated` into the autonet-275416 Firestore `agents` directory. Nothing reads it any more (the Autonet daemon and app confirmed).
  - Their code (`apps/trustless`, `apps/autonet`) is still here, unused; delete it whenever you like.
- `afterme` was already disabled and still is.

## Production

- Hetzner: `ssh -i C:/code/engineer/hetzner_ed25519 ubuntu@65.21.186.126`, code at `~/code/indexer`, now tracking `master`.
- Services `indexer@mainnet` (Etherlink mainnet) and `indexer@shadownet` (Etherlink Shadownet), each running `app.py <network>`; restart both after a pull.
- `homebase-indexer.service` on the same box is a different thing: the Tezos v3 indexer (Docker, `/home/ash/homebase-indexers/v3-indexer`). Leave it alone.
- Deploy flow: PR into `master`, merge, `git pull` on the box, `sudo systemctl restart indexer@mainnet indexer@shadownet`, check `journalctl -u indexer@mainnet -f`.
- Writes go to Firestore project homebase-6907d: `contracts/<network>` (read for addresses and RPC; the factory deploy scripts in trustless-contracts write it), `idaosEtherlink` and `idaosEtherlink-Shadownet`.
- Never delete `contracts/Etherlink` in Firestore: the mainnet indexer won't start without it.

## Who else reads what this indexer writes, for now

Until the werule R2 migration is finished, two other readers still use homebase-6907d data written here:

- werule reads DAO docs (it is not in production, so it may break, but please don't remove fields).
- The Autonet app's jurisdiction switch lists Shadownet jurisdictions from `contracts/<network>` and the `idaos<network>` docs (name, governor, registryAddress and the registry's `jurisdiction.autonet.substrate` entry).

Keep those doc shapes backward compatible (add fields, don't rename or remove) until the trustless-contracts side says the migration is done. After that, Firestore and this indexer are homebase's alone.
