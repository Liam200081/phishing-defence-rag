# Phishing Defence Agent Demo

Vue 3 single-page frontend for the existing FastAPI adapter.

## Run locally

```bash
npm install
npm run dev
```

The frontend calls `http://localhost:8000` by default. To use another API origin,
copy `.env.example` to `.env.local` and change `VITE_API_BASE_URL`.

## Checks

```bash
npm test
npm run build
```
