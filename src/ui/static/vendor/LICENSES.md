# Vendored front-end assets (SPRINT-12.1, D-054)

No CDN is referenced anywhere under `src/ui`; the two files below are served from
`/app/static/vendor/` by the application itself. `tests/ui/test_vendor_assets.py` verifies
that the on-disk SHA-256 of each file matches this table.

| File | Project | Version | Licence (SPDX) | SHA-256 |
|------|---------|---------|----------------|---------|
| `htmx.min.js` | [htmx](https://htmx.org) | 2.0.4 | 0BSD | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `pico.min.css` | [Pico CSS](https://picocss.com) | 2.0.6 | MIT | `dd5fd5591afd81ee21dcc117ad85c014dc3f1f19dc2d7b7d101ea0acc29274c2` |

Sources (fetched unchanged):

* `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` — © Big Sky Software, Zero-Clause BSD.
* `https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css` — © 2019-2024 Pico CSS, MIT.

Verify locally: `sha256sum src/ui/static/vendor/*.js src/ui/static/vendor/*.css`.
