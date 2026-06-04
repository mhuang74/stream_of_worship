"use client"

export function FontPreviewStylesheets() {
  return (
    <>
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=LXGW+WenKai+TC&family=Noto+Serif+TC&family=Chiron+GoRound+TC&family=Chocolate+Classical+Sans&display=swap"
      />
      <style
        dangerouslySetInnerHTML={{
          __html: `
            :root {
              --font-lxgw-wenkai-tc: 'LXGW WenKai TC', serif;
              --font-chocolate-classical-sans: 'Chocolate Classical Sans', sans-serif;
              --font-chiron-goround-tc: 'Chiron GoRound TC', sans-serif;
              --font-noto-serif-tc: 'Noto Serif TC', serif;
            }
          `,
        }}
      />
    </>
  )
}
