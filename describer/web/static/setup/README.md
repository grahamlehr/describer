# Setup screenshots

`setup.html` marks where each of these goes with a `<!-- screenshot: … -->`
comment. Nothing is shipped yet: take them from the Rail Data Marketplace
(raildata.org.uk) as it looks today, crop tight, and mark the thing to click.
Docs Phase 5 (`docs/INSTALL.md`) reuses the same images.

| File | Shows |
|------|-------|
| `1-create-account.png` | The site's home page, the sign-up button marked |
| `2-find-product.png` | The right product marked, the departures-only one crossed out |
| `3-subscribe.png` | The product page, the subscribe button marked |
| `4-consumer-key.png` | The subscription page, the Consumer key and its copy button marked |

When an image lands, replace its comment in `setup.html` with an `<img>`
(`loading="lazy"`, an `alt`, and a max width of the phone's column).
