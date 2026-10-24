# Tracker & Mail Alert - Membership Barometer

This project is an automated Python pipeline designed to track the membership count of the Nouvelle Énergie movement. Published via the dedicated account **[@CompteurNE](https://x.com/CompteurNE)**, it runs on GitHub Actions to fetch data, detect changes, generate an updated visual card using a headless browser, and publish regular milestone updates to both X (Twitter) and email.

## Features

* **Real-time Monitoring:** Queries remote API endpoints to fetch live counts and historical trend points.
* **Cache & Anomaly Protection:** State-checking mechanisms prevent recording duplicate or outdated snapshots caused by stale CDN caches.
* **Momentum & Velocity Analytics:** Dynamically computes recent registration velocity (adhesions/hr, min/adhesion), 24h & 7d net gains, and linear projection toward the 100 000 milestone.
* **Visual Card Rendering:** Builds a tailored data-visualization card using HTML5 and Chart.js, captured at high resolution with headless Playwright Chromium.
* **Automated Publishing:**
  * Publishes status updates with attached media directly to X via Tweepy (v1.1 media upload + v2 tweet creation).
  * Dispatches snapshot alerts with high-resolution image attachments over Gmail SMTP.
* **GitOps Storage:** Automatically commits and pushes updated snapshots (`.csv` and state `.json`) directly to the GitHub repository.

## Tweet Example

Every scheduled or triggered run posts an update following this format directly to **@CompteurNE**:

```text
📊 
@nouv_energie · Baromètre

👥 80 259 adhérents

⚡ ~135 adh. / h (~2.2 adh. / min)
📈 +2 174 (24h) · +16 573 (7j)

Cap 60k ➔ 100k (▓▓▓▓░░░░ 50 %) :
▫️ Reste : 19 741 adhésions
▫️ Projection : ~31 oct. 2026
```

<img width="2160" height="2700" alt="HVURcJtWQAARd4N" src="https://github.com/user-attachments/assets/c9caed61-757e-4561-977a-237543844f04" />

