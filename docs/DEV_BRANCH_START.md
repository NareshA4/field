# How we work on this repo

1. `git checkout dev && git pull origin dev`, then create your own branch: `git checkout -b feature/<module>`.
2. Build only inside your module's folders (`src/apps/<module>`, `src/templates/<module>`, `tests/`). Register your app in
   `INSTALLED_APPS` (`src/config/settings/base.py`) and your API router / URL include in `src/config/urls.py`.
3. Follow the layers: views/API (thin) -> `services.py` (rules, transaction, audit) -> models. See M01 (`src/apps/sites`)
   as the reference module. Your spec is `docs/modules/Mxx_*.md`.
4. Commit and push your branch often. When the module works, open a Pull Request into `dev` and get a review.
5. After another module is merged, `git checkout dev && git pull origin dev`, then `git merge dev` into your branch.
6. Never commit `.env`, passwords or keys. Never push straight to `dev` or `main`.

Assets (M02): `sites` shows asset counts and blocks deactivating a site that has active assets only when
`apps.assets` is in `INSTALLED_APPS`; until then those parts are skipped.
