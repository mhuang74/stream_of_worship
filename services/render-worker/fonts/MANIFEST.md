# Font Manifest

Vendored font files for the render worker. All fonts are Regular weight only.

| Font Display Name | File | Source | License | License URL |
|---|---|---|---|---|
| LXGW WenKai TC | `LXGWWenKaiTC-Regular.ttf` | [GitHub Release v1.522](https://github.com/lxgw/LxgwWenkaiTC/releases/tag/v1.522) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Chocolate Classical Sans | `ChocolateClassicalSans-Regular.ttf` | [Google Fonts](https://fonts.google.com/specimen/Chocolate+Classical+Sans) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Chiron GoRound TC | `ChironGoRoundTC-Regular.ttf` | [Google Fonts](https://fonts.google.com/specimen/Chiron+GoRound+TC) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Noto Serif TC | `NotoSerifTC-Regular.ttf` | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Serif+TC) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |

## Checksums

| File | SHA-256 | Date Downloaded |
|---|---|---|
| `LXGWWenKaiTC-Regular.ttf` | `b1a0795862c1415bf3f393ea50b2a4ea6275012cf5bad3f94feeb1222f555731` | 2026-06-04 |
| `ChocolateClassicalSans-Regular.ttf` | `330b3161911a7439766296c640c22f34fc91403e94efec420fb332f9ee65d587` | 2026-06-04 |
| `ChironGoRoundTC-Regular.ttf` | `bb3c9426990ea0a9c1818ea7b6568b5ca8f5a402209043da2584255c065d6498` | 2026-06-04 |
| `NotoSerifTC-Regular.ttf` | `5bd260cf7ec3ab0f45285ff620c6888d10a915b826331ee3d4f6dad49950c090` | 2026-06-04 |

## Notes

- These fonts are vendored for the Lambda render worker which runs in an isolated container without internet access.
- The system package `google-noto-sans-cjk-fonts` provides Noto Sans CJK as an emergency fallback.
- Font files must be placed in this directory before building the Docker image.
