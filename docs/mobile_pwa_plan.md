# Mobile & PWA Readiness Plan

## Goal

Make the Growout + Pregrow Management System usable on mobile phones and tablets
during daily farm rounds, without building a separate native app.

---

## Phase 1 — Responsive Streamlit Layout (Now)

Streamlit renders on mobile but some patterns hurt usability on small screens.

### Layout guidelines

| Pattern | Desktop | Mobile fix |
|---|---|---|
| `st.columns([3,2,2,1])` | fine | use 2 cols max on data-entry screens |
| Wide DataFrames | scrollable | limit columns, use `use_container_width=True` |
| Sidebar always visible | fine | `initial_sidebar_state="collapsed"` on mobile |
| Long forms | fine | group into `st.expander` sections |
| `st.metric` grids | fine | reduce to 2-across on small screens |

### Immediate improvements (no native app required)

1. **Date inputs and selects** — already work well on mobile browsers.
2. **Collapse sidebar by default** — set `initial_sidebar_state="auto"` in
   `st.set_page_config` so Streamlit collapses it on narrow viewports.
3. **Simplify Daily Log tank table** — replace the side-by-side O2/Mortality/Feed
   columns with a stacked layout on screens < 600 px wide (CSS media query or
   detecting viewport via JS).
4. **Reduce DataFrame columns for summary views** — show only the most important
   columns (Date, Unit, Mortality, Feed) in the overview table; put the rest
   behind an expander.
5. **Larger tap targets** — `use_container_width=True` on buttons makes them
   easier to tap on touchscreens.

### Streamlit config tip

In `.streamlit/config.toml`:
```toml
[server]
headless = true

[browser]
gatherUsageStats = false
```

---

## Phase 2 — HTTPS Deployment (Required for PWA)

A Progressive Web App (PWA) requires HTTPS. Options:

### Option A — Streamlit Cloud (Easiest)
- Free tier available at share.streamlit.io
- HTTPS out of the box
- Connect a GitHub repo → auto-deploy on push
- Secrets via Streamlit Cloud secrets UI

### Option B — VPS with nginx + Let's Encrypt
- Any cloud VM (DigitalOcean, Hetzner, Linode)
- nginx reverse proxy to `localhost:8501`
- Certbot for free TLS certificate
- Suitable for on-premise / farm-local deployment

### Option C — Fly.io / Railway / Render
- Container-based; push Docker image
- HTTPS + custom domain included
- Simple `Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app
  COPY . .
  RUN pip install -r requirements.txt
  CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.headless=true"]
  ```

---

## Phase 3 — PWA Wrapper

Once deployed over HTTPS, the app can be "installed" as a PWA on Android and iOS:

1. **Add a Web App Manifest** — a `manifest.json` file declaring app name, icons,
   theme color, and `display: standalone`.  Streamlit doesn't serve this natively,
   but it can be injected via `st.markdown(<link rel="manifest" ...>, unsafe_allow_html=True)`.

2. **Service Worker** — caches static assets for faster load on slow farm Wi-Fi.
   A minimal service worker can be injected similarly.

3. **iOS "Add to Home Screen"** — works without a service worker; just requires
   HTTPS and the correct `<meta name="apple-mobile-web-app-capable">` tags.

4. **Alternatively: Capacitor wrapper** — wraps any web app in a native shell.
   Low effort if you already have a HTTPS URL; produces an APK / IPA that can be
   sideloaded or published to app stores.

### PWA manifest template
```json
{
  "name": "Growout Manager",
  "short_name": "Growout",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#0068c9",
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

---

## Phase 4 — Offline Support (Advanced)

Farm networks can be unreliable. Options for offline resilience:

- **Local SQLite + sync** — keep SQLite on the device; sync to cloud when online.
  Requires a proper client-server architecture (FastAPI backend + React / React Native
  frontend). High effort.
- **IndexedDB cache** — JavaScript-side caching in the browser. Works with a PWA
  service worker; read-only offline access to the last loaded data.
- **"Store and forward"** — daily log entries queued locally; uploaded when
  connectivity resumes. Requires backend API (see database_migration_plan.md).

For most farms, reliable Wi-Fi + HTTPS deployment is sufficient without offline
support.

---

## Checklist

- [ ] Deploy over HTTPS (Streamlit Cloud or VPS)
- [ ] Set `initial_sidebar_state="auto"` in app.py
- [ ] Test Daily Log entry form on a phone (Android Chrome + iOS Safari)
- [ ] Simplify DataFrame summary columns for narrow viewports
- [ ] Add "Add to Home Screen" meta tags
- [ ] (Optional) Write manifest.json and inject it via st.markdown
- [ ] (Optional) Capacitor wrapper for app store distribution
- [ ] (Optional) Offline queue for daily log entries
