# dealradar sources report

> **Context:** this snapshot was generated from a network-restricted build
> container where all outbound fetches to deal sites were blocked, so feeds
> read `dead`/`blocked`. Re-run `python -m dealradar validate-sources` from a
> machine with the allowlisted domains (README §network access) for the real
> report; the hourly workflow also regenerates it on first run.

Generated: 2026-10-09T23:44:16+00:00 (UTC)

## Summary

- Active sources: 0
- dead: 44
- disabled: 2
- needs_key: 10

**Note:** only 0 active sources; target is 30 and the backup pool is exhausted.

## Sources

| name | method | status | detail/url |
| --- | --- | --- | --- |
| hip2save | rss | dead | blocked_by_robots; autodiscovery found no feed |
| krazy_coupon_lady | rss | dead | blocked_by_robots; autodiscovery found no feed |
| slickdeals_frontpage | rss | dead | blocked_by_robots; official-feed-only host, autodiscovery forbidden |
| slickdeals_popular | rss | dead | blocked_by_robots; official-feed-only host, autodiscovery forbidden |
| slickdeals_amazon | rss | dead | blocked_by_robots; official-feed-only host, autodiscovery forbidden |
| hip2save_amazon | rss | dead | blocked_by_robots; autodiscovery found no feed |
| kcl_amazon | rss | dead | blocked_by_robots; autodiscovery found no feed |
| dealnews | rss | dead | blocked_by_robots; autodiscovery found no feed |
| bradsdeals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| bensbargains | rss | dead | blocked_by_robots; autodiscovery found no feed |
| woot | api | needs_key | missing WOOT_API_KEY |
| meh | api | needs_key | missing MEH_API_KEY |
| techbargains | rss | dead | blocked_by_robots; autodiscovery found no feed |
| dealmoon_us | rss | dead | blocked_by_robots; autodiscovery found no feed |
| moneysavingmom | rss | dead | blocked_by_robots; autodiscovery found no feed |
| passionate_penny_pincher | rss | dead | blocked_by_robots; autodiscovery found no feed |
| freebies2deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| southernsavers | rss | dead | blocked_by_robots; autodiscovery found no feed |
| coupon_pro | rss | dead | blocked_by_robots; autodiscovery found no feed |
| the_freebie_guy | rss | dead | blocked_by_robots; autodiscovery found no feed |
| yes_we_coupon | rss | dead | blocked_by_robots; autodiscovery found no feed |
| jungle_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| clark_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| hunt4freebies | rss | dead | blocked_by_robots; autodiscovery found no feed |
| living_rich_with_coupons | rss | dead | blocked_by_robots; autodiscovery found no feed |
| 9to5toys | rss | dead | blocked_by_robots; autodiscovery found no feed |
| cnet_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| tomsguide_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| dansdeals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| doctor_of_credit | rss | dead | blocked_by_robots; autodiscovery found no feed |
| kinja_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| couponcabin_blog | rss | dead | blocked_by_robots; autodiscovery found no feed |
| retailmenot_blog | rss | dead | blocked_by_robots; autodiscovery found no feed |
| gottadeal | rss | dead | blocked_by_robots; autodiscovery found no feed |
| offers_blog | rss | dead | blocked_by_robots; autodiscovery found no feed |
| wirecutter_deals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| slickdeals_blog | rss | dead | blocked_by_robots; official-feed-only host, autodiscovery forbidden |
| reddit_deals | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| reddit_coupons | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| reddit_frugal | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| reddit_buildapcsales | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| reddit_gamedeals | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| reddit_frugalfemalefashion | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |
| telegram_channels | api | needs_key | missing TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION |
| threads_search | api | disabled | disabled in sources.yaml |
| instagram_hashtags | api | disabled | disabled in sources.yaml |
| dealspotr | rss | dead | blocked_by_robots; autodiscovery found no feed |
| daily_steals | rss | dead | blocked_by_robots; autodiscovery found no feed |
| tanga | rss | dead | blocked_by_robots; autodiscovery found no feed |
| 1sale | rss | dead | blocked_by_robots; autodiscovery found no feed |
| mojo_savings | rss | dead | blocked_by_robots; autodiscovery found no feed |
| common_sense_with_money | rss | dead | blocked_by_robots; autodiscovery found no feed |
| frugal_living_nw | rss | dead | blocked_by_robots; autodiscovery found no feed |
| saving_dollars_and_sense | rss | dead | blocked_by_robots; autodiscovery found no feed |
| consumer_queen | rss | dead | blocked_by_robots; autodiscovery found no feed |
| reddit_flipping | api | needs_key | missing REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT |

## Promoted backups

None.

## Dropped sources

- hip2save: blocked_by_robots; autodiscovery found no feed
- krazy_coupon_lady: blocked_by_robots; autodiscovery found no feed
- slickdeals_frontpage: blocked_by_robots; official-feed-only host, autodiscovery forbidden
- slickdeals_popular: blocked_by_robots; official-feed-only host, autodiscovery forbidden
- slickdeals_amazon: blocked_by_robots; official-feed-only host, autodiscovery forbidden
- hip2save_amazon: blocked_by_robots; autodiscovery found no feed
- kcl_amazon: blocked_by_robots; autodiscovery found no feed
- dealnews: blocked_by_robots; autodiscovery found no feed
- bradsdeals: blocked_by_robots; autodiscovery found no feed
- bensbargains: blocked_by_robots; autodiscovery found no feed
- techbargains: blocked_by_robots; autodiscovery found no feed
- dealmoon_us: blocked_by_robots; autodiscovery found no feed
- moneysavingmom: blocked_by_robots; autodiscovery found no feed
- passionate_penny_pincher: blocked_by_robots; autodiscovery found no feed
- freebies2deals: blocked_by_robots; autodiscovery found no feed
- southernsavers: blocked_by_robots; autodiscovery found no feed
- coupon_pro: blocked_by_robots; autodiscovery found no feed
- the_freebie_guy: blocked_by_robots; autodiscovery found no feed
- yes_we_coupon: blocked_by_robots; autodiscovery found no feed
- jungle_deals: blocked_by_robots; autodiscovery found no feed
- clark_deals: blocked_by_robots; autodiscovery found no feed
- hunt4freebies: blocked_by_robots; autodiscovery found no feed
- living_rich_with_coupons: blocked_by_robots; autodiscovery found no feed
- 9to5toys: blocked_by_robots; autodiscovery found no feed
- cnet_deals: blocked_by_robots; autodiscovery found no feed
- tomsguide_deals: blocked_by_robots; autodiscovery found no feed
- dansdeals: blocked_by_robots; autodiscovery found no feed
- doctor_of_credit: blocked_by_robots; autodiscovery found no feed
- kinja_deals: blocked_by_robots; autodiscovery found no feed
- couponcabin_blog: blocked_by_robots; autodiscovery found no feed
- retailmenot_blog: blocked_by_robots; autodiscovery found no feed
- gottadeal: blocked_by_robots; autodiscovery found no feed
- offers_blog: blocked_by_robots; autodiscovery found no feed
- wirecutter_deals: blocked_by_robots; autodiscovery found no feed
- slickdeals_blog: blocked_by_robots; official-feed-only host, autodiscovery forbidden
- dealspotr: blocked_by_robots; autodiscovery found no feed
- daily_steals: blocked_by_robots; autodiscovery found no feed
- tanga: blocked_by_robots; autodiscovery found no feed
- 1sale: blocked_by_robots; autodiscovery found no feed
- mojo_savings: blocked_by_robots; autodiscovery found no feed
- common_sense_with_money: blocked_by_robots; autodiscovery found no feed
- frugal_living_nw: blocked_by_robots; autodiscovery found no feed
- saving_dollars_and_sense: blocked_by_robots; autodiscovery found no feed
- consumer_queen: blocked_by_robots; autodiscovery found no feed
