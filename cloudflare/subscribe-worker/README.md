# subscribe worker — the free daily email list

The only moving part of the newsletter that lives outside GitHub: a tiny
Cloudflare Worker + KV namespace that holds the subscriber list. The site's
forms POST to it, people unsubscribe through it, and the pipeline reads the
list through it. It never sends mail; `dealradar/publish/newsletter.py` does
that over the SMTP secrets, once per subscriber per day.

```
browser ──POST /subscribe──▶ Worker ──▶ KV  sub:<email> = {ts, token}
GitHub Actions ──GET /list (Bearer NEWSLETTER_SECRET)──▶ Worker ──▶ emails + tokens
subscriber ──GET /unsubscribe?e=&t=──▶ Worker ──▶ KV delete
```

## Endpoints

| Route | What it does |
|---|---|
| `POST /subscribe` | `{"email": ...}` JSON or a form field `email`. Validates, lowercases, stores `sub:<email>` with a random token (idempotent: signing up twice keeps the first token). JSON callers get `200 {"ok": true}`; form posts get a `303` back to `<site>/subscribe.html?ok=1` (or `?err=email`). Abuse control: 20 posts per client IP per hour. |
| `GET` or `POST /unsubscribe?e=<email>&t=<token>` | Deletes the record when the token matches, 200 HTML "You're unsubscribed". POST is the RFC 8058 one-click form mail apps send. |
| `GET /list` | `Authorization: Bearer <NEWSLETTER_SECRET>` -> `{"emails": [{"email", "token"}]}`, at most 10,000 (KV list pagination; tokens ride in key metadata so a page of 1,000 costs one KV operation). |
| `OPTIONS *` | CORS preflight for `ALLOWED_ORIGINS` only. |
| anything else | 404. |

Stored per subscriber: the address, the sign-up time and the unsubscribe
token. Rate-limit counters (`rl:<ip>:<hour>`) expire after two hours.

## Deploy (once, then whenever worker.js changes)

Run the **deploy subscribe worker** workflow from the repository's Actions tab
(`.github/workflows/deploy-subscribe-worker.yml`). It needs three repository
secrets:

| Secret | Where it comes from |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> template "Edit Cloudflare Workers". |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard -> Workers & Pages -> Overview, right-hand column "Account ID". |
| `NEWSLETTER_SECRET` | Any long random string (e.g. `openssl rand -hex 32`). The same secret is used by the pipeline workflows to call `/list`. |

The workflow creates the KV namespace `wowpriced_subs` if it is missing,
substitutes its id into a copy of `wrangler.toml`, runs `wrangler deploy`,
puts `NEWSLETTER_SECRET` on the Worker and prints the Worker URL
(`https://wowpriced-subscribe.<your-subdomain>.workers.dev`) in the job
summary. Paste that URL into `config.yaml`:

```yaml
newsletter:
  worker_url: "https://wowpriced-subscribe.<your-subdomain>.workers.dev"
```

The next site deploy carries it in `deals.json` meta (`newsletter_url`), the
forms on the site start posting to it, and the pipeline starts sending the
daily email at `newsletter.hour_local`.

Manual deploy from a laptop (same steps the workflow runs):

```sh
cd cloudflare/subscribe-worker
npx wrangler kv namespace create wowpriced_subs      # once; note the id
sed 's/REPLACED_AT_DEPLOY/<that id>/' wrangler.toml > wrangler.deploy.toml
npx wrangler deploy --config wrangler.deploy.toml
printf '%s' "$NEWSLETTER_SECRET" | npx wrangler secret put NEWSLETTER_SECRET --config wrangler.deploy.toml
```

## Smoke test

```sh
W=https://wowpriced-subscribe.<sub>.workers.dev
curl -s -X POST -H 'Content-Type: application/json' -d '{"email":"you@example.com"}' $W/subscribe
curl -s -H "Authorization: Bearer $NEWSLETTER_SECRET" $W/list
curl -s "$W/unsubscribe?e=you@example.com&t=<token from the list>"
```

## Costs and limits

Fits the Workers free plan: a few hundred requests a day (one `/list` call
per pipeline run plus sign-ups), well under the 100k/day request and the KV
read/list allowances. `/list` returns at most 10,000 subscribers.
