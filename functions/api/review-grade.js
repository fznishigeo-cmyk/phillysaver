// Task 367 — pure review-signal grading. No raw signals leave this module.
// stats=30 makes atIntervalStart the actual count at the start of the month;
// avg30 is a weighted mean and must never stand in for that count.
export function validReviewSettings(cfg) {
  if (!cfg || cfg.enabled !== true) return false;
  const integer = (key, low, high) => Number.isSafeInteger(cfg[key]) && cfg[key] >= low && cfg[key] <= high;
  const number = (key, low, high) => typeof cfg[key] === "number" && Number.isFinite(cfg[key]) && cfg[key] >= low && cfg[key] <= high;
  return ["min_reviews", "pace_min_reviews", "popular_monthly_sold"].every(k => integer(k, 1, 1000000000)) &&
    integer("grade_a_checks", 1, 5) && integer("grade_b_checks", 1, cfg.grade_a_checks) &&
    number("min_rating", 1, 5) && number("steady_rating_delta", 0, 4) && number("pace_max_fraction", 0, 1);
}

export function gradeReviews(product, cfg) {
  const checks = [];
  const add = (id, title, result, detail) => checks.push({id, title, result, detail});
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const rating = value => Number.isSafeInteger(value) && value >= 10 && value <= 50 ? value : null;
  if (validReviewSettings(cfg)) {
    const stats = product?.stats;
    const total = count(stats?.current?.[17]), current = rating(stats?.current?.[16]);
    const average = rating(stats?.avg180?.[16]), before = count(stats?.atIntervalStart?.[17]);
    const sold = count(product?.monthlySold);
    if (total !== null && total >= cfg.min_reviews)
      add("plenty_of_reviews", "Plenty of reviews", "An established collection of reviews to explore.",
        "The available review count meets our benchmark. Reading a few reviews can help you decide what matters to you.");
    if (current !== null && current >= cfg.min_rating * 10)
      add("highly_rated", "Highly rated", "Shoppers have given this product a warm reception.",
        "The available rating meets our benchmark for this check. This is a review signal, not a guarantee about the product.");
    // Compare tenths directly, avoiding floating point subtraction at the boundary.
    if (current !== null && average !== null && Math.abs(current - average) <= cfg.steady_rating_delta * 10)
      add("steady_rating", "Steady rating over time", "The rating is close to its longer-term average.",
        "The available current rating is close to its longer-term average. We do not change Amazon's rating.");
    if (total !== null && before !== null && total >= cfg.pace_min_reviews && total >= before &&
        total - before <= total * cfg.pace_max_fraction)
      add("natural_pace", "Natural review pace", "Recent reviews are a modest share of the total.",
        "The recorded count at the start of the past month supports a measured pace of review growth. This does not assess individual reviews or prove authenticity.");
    if (sold !== null && sold >= cfg.popular_monthly_sold)
      add("popular_now", "Popular right now", "Recent shopping activity meets our popularity benchmark.",
        "The available recent purchase signal meets our benchmark. Popularity is one useful piece of context for your decision.");
  }
  const grade = checks.length >= cfg?.grade_a_checks && checks.some(c => c.id === "highly_rated") ? "A" :
    checks.length >= cfg?.grade_b_checks && checks.length ? "B" : "C";
  const label = {A:"Great — a strong set of review signals", B:"Good — helpful review signals to explore",
    C:"Okay — read a few reviews before you buy"}[grade];
  return {grade, label, checks};
}

export function reviewResponse(products, items, asin, cfg) {
  const product = products.find(p => p?.asin === asin);
  if (!product || typeof product.title !== "string" || !product.title.trim()) return {items:[], reason:"no_product"};
  const item = items[0];
  // Exact allowlist: no list/past prices, rating, count or sales numbers. The price keeps its
  // shipping fact (A34/C16), shown as on Deal Spotter: "+ $X shipping" or "incl. shipping".
  return {items:[item ? {asin:item.asin, title:item.title, image:item.image,
    price_cents:item.price_cents, as_of:item.as_of,
    ...(Number.isSafeInteger(item.shipping_cents) && item.shipping_cents > 0 ? {shipping_cents:item.shipping_cents} : {}),
    ...(item.price_includes_shipping === true ? {price_includes_shipping:true} : {})} :
    {asin, title:product.title.trim().slice(0, 500), image:""}], review:gradeReviews(product, cfg)};
}
