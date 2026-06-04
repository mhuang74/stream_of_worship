# Font Manifest

Vendored font files for the render worker. All fonts are Regular weight only.

| Font Display Name | File | Source | License | License URL |
|---|---|---|---|---|
| LXGW WenKai TC | `LXGWWenKaiTC-Regular.ttf` | [GitHub Release](https://github.com/lxgw/LxgwWenKaiTC/releases) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Chocolate Classical Sans | `ChocolateClassicalSans-Regular.ttf` | [GitHub Release](https://github.com/ButTaiwan/ChocolateClassicalSans) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Chiron GoRound TC | `ChironGoRoundTC-Regular.ttf` | [GitHub Release](https://github.com/nicholasgasior/chiron-goround-tc) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |
| Noto Serif TC | `NotoSerifTC-Regular.ttf` | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Serif+TC) | SIL Open Font License 1.1 | https://scripts.sil.org/OFL |

## Checksums

| File | SHA-256 | Date Downloaded |
|---|---|---|
| `LXGWWenKaiTC-Regular.ttf` | (to be filled after download) | (to be filled after download) |
| `ChocolateClassicalSans-Regular.ttf` | (to be filled after download) | (to be filled after download) |
| `ChironGoRoundTC-Regular.ttf` | (to be filled after download) | (to be filled after download) |
| `NotoSerifTC-Regular.ttf` | (to be filled after download) | (to be filled after download) |

## Notes

- These fonts are vendored for the Lambda render worker which runs in an isolated container without internet access.
- The system package `google-noto-sans-cjk-fonts` provides Noto Sans CJK as an emergency fallback.
- Font files must be downloaded and placed in this directory before building the Docker image.
