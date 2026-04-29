# Pacheco Carpentry — pitch site

A single-page static website for **Pacheco Carpentry Services** (Houston, TX).
Built as a sales-pitch demo: zero build step, opens in any browser, easy to
host anywhere.

## Files

```
site/
├── index.html   — all markup
├── styles.css   — design system + layout
├── script.js    — sticky nav, mobile menu, scroll reveal, lightbox, image fallback
└── README.md    — this file
```

Nothing else is needed. No npm, no bundler, no backend.

## Demo it locally

Open `index.html` directly, or run a tiny local server (recommended so anchors
and Google Fonts behave normally):

```bash
python3 -m http.server 8000 --directory site
# then visit http://localhost:8000
```

## Real business info already on the site

Pulled from the owner's Facebook page — change here only if it changes there:

| Field   | Value                                                      | Where to edit                           |
|---------|------------------------------------------------------------|-----------------------------------------|
| Phone   | 832-639-2649                                               | search `832-639-2649` in `index.html`   |
| Email   | julissa.velasquez1997@gmail.com                            | search `julissa.velasquez1997` in `index.html` |
| Address | Indigo St, Houston, TX                                     | search `Indigo St` in `index.html`      |
| Hours   | Always open                                                | search `Always open` in `index.html`    |
| Tagline | "We make custom cabinets, install doors, install cabinets, baseboards, trim, crown molding, closets, beams and more." | search `We make custom cabinets` in `index.html` |

## Editable copy

Look for `<!-- EDITABLE: ... -->` comments in `index.html`. The two most likely
edits are:

1. **About paragraphs** — currently in the owner's voice but plain. Swap with
   the owner's own words once you have them.
2. **Selected work captions** — neighborhoods (Bellaire, Heights, Memorial,
   West U, Spring Branch, Cypress) and durations are illustrative. Replace
   with actual past projects once you have them.

There are **no fake testimonials**. The site has 2 reviews on Facebook and
inventing quotes would be dishonest, so the "Why homeowners call us" section
uses real differentiators (always open, free estimates, Houston-local) instead.

## Photos

All photos are loaded from the Unsplash CDN
(`https://images.unsplash.com/photo-…`). They are free for commercial use under
the Unsplash License, no attribution required.

If any image fails to load (rare — only if Unsplash removes a specific photo),
`script.js` will swap it for a deterministic Picsum placeholder and log the
broken URL in the browser console (DevTools → Console). To replace one
permanently:

1. Find a new image on https://unsplash.com.
2. Click "Download free" → right-click the image → "Copy image address".
3. Paste it into the matching `<img src="…">` in `index.html`. Keep the
   `?w=…&q=80&auto=format&fit=crop` query string for size + format
   optimization.

## Form behaviour

The contact form's `action` is `mailto:julissa.velasquez1997@gmail.com`, so
submitting opens the visitor's email app with the fields prefilled. **No
backend required.**

If/when the client signs and you want a proper inbox-delivered form:

- **Easiest**: change the form to a [Formspree](https://formspree.io) endpoint
  — replace `action="mailto:..."` with `action="https://formspree.io/f/YOUR_ID"`
  and `method="post"`. ~5 minutes.
- **If hosted on Netlify**: add `netlify` and `name="contact"` attributes to
  the `<form>` tag. Netlify catches submissions automatically.

## Deployment options (in order of effort)

1. **Netlify Drop** — drag the `site/` folder onto https://app.netlify.com/drop.
   Live in 30 seconds with a free `*.netlify.app` URL.
2. **GitHub Pages** — push to a public repo, Settings → Pages → "Deploy from
   branch" → `/site`.
3. **Client's existing host** — the Google Business auto-page
   (`pacheco-carpentry-services.business.site`) can be replaced with this site
   on any cheap shared host. Just upload the three files.

## Browser support

Modern Chrome / Safari / Firefox / Edge (last 2 versions). Uses
`backdrop-filter`, IntersectionObserver, CSS Grid — all widely supported in
2026. Respects `prefers-reduced-motion`.

## Pre-pitch checklist

- [ ] Open the site in Chrome and Safari at desktop + mobile widths.
- [ ] Confirm every photo loads (none silently fall back to Picsum).
- [ ] Click "Request an estimate" — confirm the email pre-fill looks right.
- [ ] Click "Call 832-639-2649" on a phone — confirm it dials.
- [ ] Run Chrome Lighthouse (DevTools → Lighthouse). Target 90+ on
      Performance, Accessibility, Best Practices, SEO.
