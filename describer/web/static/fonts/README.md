# Self-hosted fonts

The board falls back to system fonts, so it works with nothing in this
directory. To pin the exact look on the Pi (and stay independent of any
network at boot), drop the woff2 files here and add the matching
`@font-face` rules to the theme CSS that uses them:

| Theme      | Font family expected | Suggested file            |
|------------|----------------------|---------------------------|
| modern     | Inter                | `inter-regular.woff2`, `inter-semibold.woff2` |
| crt        | IBM Plex Mono        | `plex-mono-regular.woff2` |
| splitflap  | Roboto Mono          | `roboto-mono-medium.woff2` |

Example, added at the top of `themes/modern.css`:

```css
@font-face {
  font-family: "Inter";
  src: url("/static/fonts/inter-regular.woff2") format("woff2");
  font-weight: 400;
  font-display: swap;
}
```

Both the CRT and split-flap themes need a monospace face; any fixed-width
font keeps the character grid aligned.
