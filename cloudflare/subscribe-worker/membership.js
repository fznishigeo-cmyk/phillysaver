/**
 * Membership for the paywall (docs/paywall.md): a member is anyone with an
 * active or trialing Stripe subscription on their email, or still inside the
 * free trial that started at sign-up.
 *
 *   member:<email>   {member, checked_at}  Stripe's answer, good for 5 minutes
 *   rl:fresh:<email> when ?fresh=1 last bypassed that cache (once a minute)
 *
 * A Stripe failure counts as "not subscribed" for that one request (logged,
 * never cached), so an outage can never lock a trial user out or persist.
 */

const STRIPE_API = "https://api.stripe.com/v1";
const MEMBER_TTL_MS = 5 * 60 * 1000;
const FRESH_EVERY_MS = 60 * 1000;

// true/false from Stripe, null when it could not be asked.
async function stripeSubscribed(env, email) {
  if (!env.STRIPE_SECRET_KEY) {
    console.warn("stripe check skipped: STRIPE_SECRET_KEY unset");
    return null;
  }
  const headers = { Authorization: `Basic ${btoa(`${env.STRIPE_SECRET_KEY}:`)}` };
  const get = async (path) => {
    const res = await fetch(`${STRIPE_API}${path}`, { headers });
    if (!res.ok) throw new Error(`stripe ${res.status} on ${path.split("?")[0]}`);
    return res.json();
  };
  try {
    const customers = (await get(`/customers?email=${encodeURIComponent(email)}&limit=5`)).data || [];
    for (const c of customers) {
      const subs = (await get(`/subscriptions?customer=${encodeURIComponent(c.id)}&status=all&limit=100`)).data || [];
      if (subs.some((s) => s.status === "active" || s.status === "trialing")) return true;
    }
    return false;
  } catch (e) {
    console.warn("stripe check failed:", e && e.message);
    return null;
  }
}

export async function isSubscribed(env, email, fresh = false) {
  const now = Date.now();
  if (fresh) { // honored once a minute per account; otherwise the cache answers
    const last = Date.parse(await env.SUBS.get(`rl:fresh:${email}`) || "") || 0;
    if (now - last < FRESH_EVERY_MS) fresh = false;
    else await env.SUBS.put(`rl:fresh:${email}`, new Date(now).toISOString(), { expirationTtl: 120 });
  }
  if (!fresh) {
    try {
      const cached = JSON.parse(await env.SUBS.get(`member:${email}`));
      if (cached && now - Date.parse(cached.checked_at) < MEMBER_TTL_MS) return !!cached.member;
    } catch (e) { /* no usable cache: ask Stripe */ }
  }
  const member = await stripeSubscribed(env, email);
  if (member === null) return false;
  await env.SUBS.put(`member:${email}`, JSON.stringify({ member, checked_at: new Date(now).toISOString() }),
                     { expirationTtl: 600 });
  return member;
}

// The `me` object every signed-in response carries.
export async function meFor(env, acct, fresh = false) {
  const subscribed = await isSubscribed(env, acct.email, fresh);
  const trial = Date.now() < (Date.parse(acct.trial_ends_at || "") || 0);
  const link = env.STRIPE_PAYMENT_LINK || "";
  return {
    email: acct.email,
    member: subscribed || trial,
    reason: subscribed ? "subscribed" : trial ? "trial" : "expired",
    trial_ends_at: acct.trial_ends_at || null,
    subscribe_url: link ? `${link}${link.includes("?") ? "&" : "?"}prefilled_email=${encodeURIComponent(acct.email)}` : "",
    portal_url: env.STRIPE_PORTAL_LINK || "",
  };
}
