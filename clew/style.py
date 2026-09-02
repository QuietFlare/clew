"""
Clew — the QuietFlare report stylesheet.

Tokens are copied verbatim from quietflare.net, in the same HSL triplet
form and under the same names, so a change on the site is a copy rather
than a translation.

    --accent      25 95% 53%    the orange in "Flare"
    --foreground  215 28% 17%   ink
    --primary     222 47% 11%   near-black
    --steel       215 16% 47%   muted text
    --border      214 20% 88%
    --background  210 40% 98%
    --radius      .5rem

LIGHT ONLY, DELIBERATELY
------------------------
The site is light and these pages match it. Every colour is painted
explicitly rather than inherited, so the page holds its own appearance on
a dark host background instead of borrowing one.

NO NETWORK, AND THE REAL FACES
------------------------------
The site loads Inter and Inter Tight from a font host. A report cannot:
one that fetches anything stops opening on a machine with no access, and
these get emailed and archived. So the faces are embedded as woff2 data
URIs instead, which costs about 185 KB and buys a page that looks like
QuietFlare wherever it is opened.

ORANGE IS BRAND, NEVER STATUS
-----------------------------
The accent sits at hue 25, which is where "warning" normally lives. If
both used it, a reader could not tell "this is QuietFlare" from "this
needs attention". So orange is reserved for identity (wordmark, eyebrow
labels, links, focus) and an unsettled verdict is rendered in steel
rather than amber. That is also truer to what UNDETERMINED means: not
alarming, unanswered.
"""

from clew.fonts import FACES

TOKENS = """
:root {
  --accent: 25 95% 53%;
  --accent-strong: 21 90% 44%;
  --accent-tint: 33 100% 96%;

  --foreground: 215 28% 17%;
  --primary: 222 47% 11%;
  --steel: 215 16% 47%;
  --border: 214 20% 88%;
  --hairline: 214 27% 94%;
  --background: 210 40% 98%;
  --card: 0 0% 100%;
  --muted: 210 40% 96%;

  /* Rotated away from the accent (hue 25): a state must never read as
     a second brand colour. */
  --ok: 175 77% 26%;
  --ok-tint: 175 45% 96%;
  --destructive: 344 82% 39%;
  --destructive-tint: 344 60% 97%;

  --radius: .5rem;

  --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif;
  --display: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont,
    "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo,
    "Cascadia Mono", monospace;
}
"""

BASE = """
* { box-sizing: border-box; }

body {
  margin: 0;
  background: hsl(var(--background));
  color: hsl(var(--foreground));
  font: 400 15px/1.6 var(--sans);
  -webkit-font-smoothing: antialiased;
}

main {
  max-width: 64rem;
  margin: 0 auto;
  padding: 0 1.5rem 5rem;
}

h1 {
  font: 800 2rem/1.15 var(--display);
  letter-spacing: -.03em;
  color: hsl(var(--foreground));
  margin: 2rem 0 .5rem;
  text-wrap: balance;
}

h2 {
  font: 500 .78rem/1.4 var(--sans);
  letter-spacing: .1em;
  text-transform: uppercase;
  color: hsl(var(--accent));
  margin: 2.75rem 0 .85rem;
}

h3 {
  font: 700 1rem/1.35 var(--display);
  letter-spacing: -.01em;
  color: hsl(var(--foreground));
  margin: 0 0 .5rem;
}

p { margin: 0 0 .75rem; }
p:last-child { margin-bottom: 0; }

.lede {
  font-size: 1.05rem;
  color: hsl(var(--steel));
  max-width: 62ch;
  margin-bottom: 1.5rem;
}

.note { font-size: .85rem; color: hsl(var(--steel)); }

code, .hash, .mono {
  font-family: var(--mono);
  font-size: .855em;
}
.hash { color: hsl(var(--steel)); word-break: break-all; }

a { color: hsl(var(--accent-strong)); }
:focus-visible { outline: 2px solid hsl(var(--accent)); outline-offset: 2px; }
"""

COMPONENTS = """
.panel {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: var(--radius);
  padding: 1.05rem 1.2rem;
  margin: .75rem 0 1.25rem;
}

/* Unsettled, not alarming. Steel rather than amber, so orange stays brand. */
.panel.warn {
  background: hsl(var(--muted));
  border-left: 3px solid hsl(var(--steel));
}
.panel.stop {
  background: hsl(var(--destructive-tint));
  border-left: 3px solid hsl(var(--destructive));
}

.counts { display: flex; flex-wrap: wrap; gap: .6rem; margin: .25rem 0 1.25rem; }
.count {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: var(--radius);
  padding: .7rem .95rem;
  min-width: 7.5rem;
}
.count b {
  display: block;
  font: 800 1.6rem/1.1 var(--display);
  letter-spacing: -.02em;
  color: hsl(var(--foreground));
  font-variant-numeric: tabular-nums;
}
.count span {
  font: 500 .7rem/1.5 var(--sans);
  letter-spacing: .08em;
  text-transform: uppercase;
  color: hsl(var(--steel));
}
.count.unknown { border-color: hsl(var(--steel)); background: hsl(var(--muted)); }

.tag {
  display: inline-block;
  padding: .12rem .5rem;
  border-radius: 4px;
  border: 1px solid hsl(var(--border));
  background: hsl(var(--card));
  font: 500 .72rem/1.55 var(--mono);
  color: hsl(var(--steel));
  white-space: nowrap;
}
.tag.ok {
  color: hsl(var(--ok)); border-color: hsl(var(--ok));
  background: hsl(var(--ok-tint));
}
.tag.bad {
  color: hsl(var(--destructive)); border-color: hsl(var(--destructive));
  background: hsl(var(--destructive-tint));
}
.tag.unknown {
  color: hsl(var(--steel)); border-color: hsl(var(--steel));
  background: hsl(var(--muted));
}

.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td {
  text-align: left;
  padding: .55rem .7rem;
  border-bottom: 1px solid hsl(var(--hairline));
  vertical-align: baseline;
}
thead th {
  font: 600 .7rem/1.5 var(--sans);
  letter-spacing: .07em;
  text-transform: uppercase;
  color: hsl(var(--steel));
  border-bottom: 1px solid hsl(var(--border));
}
td.num, td .num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.why { color: hsl(var(--steel)); font-size: .9em; }

ul.coverage { margin: .4rem 0 0; padding-left: 1.15rem; }
ul.coverage li { margin: .25rem 0; }

.chain { font-family: var(--mono); font-size: .8rem; color: hsl(var(--steel)); }

/* The one picture: how much of the run this reaches. */
.bar { height: 12px; border-radius: 99px; background: hsl(var(--muted));
  overflow: hidden; display: flex; margin: .5rem 0 .35rem; }
.bar i { display: block; height: 100%; }
.bar i.hit { background: hsl(var(--accent)); }
.bar i.open { background: hsl(var(--steel)); opacity: .45; }
.barkey { display: flex; gap: 1.25rem; flex-wrap: wrap;
  font: 500 .74rem/1.5 var(--sans); color: hsl(var(--steel));
  letter-spacing: .04em; text-transform: uppercase; }
.barkey b { color: hsl(var(--foreground)); font-weight: 600; }
.dot { display: inline-block; width: .55rem; height: .55rem; border-radius: 2px;
  margin-right: .35rem; vertical-align: baseline; }

/* Share of work per machine, as length rather than a number to compare. */
.spread { display: flex; flex-direction: column; gap: .55rem; }
.spread-row { display: grid; grid-template-columns: minmax(8rem, 14rem) 1fr auto;
  gap: .75rem; align-items: center; }
.track { height: 8px; border-radius: 99px; background: hsl(var(--muted)); }
.track i { display: block; height: 100%; border-radius: 99px;
  background: hsl(var(--accent)); }
.spread-row .num { font: 500 .82rem/1 var(--mono); color: hsl(var(--steel));
  font-variant-numeric: tabular-nums; }

/* Detail is present, not prominent. */
details { border-top: 1px solid hsl(var(--border)); padding: .85rem 0 0; }
details + details { margin-top: .25rem; }
summary { cursor: pointer; list-style: none;
  font: 500 .78rem/1.4 var(--sans); letter-spacing: .1em;
  text-transform: uppercase; color: hsl(var(--steel)); }
summary::-webkit-details-marker { display: none; }
summary::before { content: "+ "; color: hsl(var(--accent)); font-weight: 700; }
details[open] summary::before { content: "− "; }
summary:hover { color: hsl(var(--foreground)); }
details > *:not(summary) { margin-top: .85rem; }

.masthead {
  border-top: 3px solid hsl(var(--accent));
  background: hsl(var(--card));
  border-bottom: 1px solid hsl(var(--border));
}
.masthead-inner {
  max-width: 64rem;
  margin: 0 auto;
  padding: 1.15rem 1.5rem;
  display: flex;
  align-items: baseline;
  gap: .75rem;
  flex-wrap: wrap;
}
.wordmark {
  font: 700 1.05rem/1 var(--display);
  letter-spacing: -.02em;
  color: hsl(var(--foreground));
}
.wordmark b { color: hsl(var(--accent)); font-weight: 700; }
.masthead .tagline { color: hsl(var(--steel)); font-size: .88rem; }
.masthead .org {
  margin-left: auto;
  font: 400 .78rem/1 var(--mono);
  color: hsl(var(--steel));
}
"""

PRINT = """
@media print {
  body { background: #fff; font-size: 10.5pt; }
  main { padding: 0; max-width: none; }
  .panel { break-inside: avoid; border-color: #bbb; }
  h2 { break-after: avoid; }
  .masthead { border-top-width: 2px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

STYLE = FACES + TOKENS + BASE + COMPONENTS + PRINT


def masthead(product: str) -> str:
    """
    Who made this and with what. Nothing else.

    A strapline here would be marketing voice on an operational
    document, and it repeats whatever the headline already says.
    """
    return (
        '<header class="masthead"><div class="masthead-inner">'
        '<span class="wordmark">Quiet<b>Flare</b></span>'
        f'<span class="tagline">{product}</span>'
        "</div></header>"
    )
