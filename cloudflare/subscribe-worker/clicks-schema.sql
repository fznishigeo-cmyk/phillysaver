-- Dedicated free D1 database. No raw events, secondary indexes or visitor data.
CREATE TABLE IF NOT EXISTS click_hours (
    hour INTEGER PRIMARY KEY,
    total INTEGER NOT NULL CHECK(total BETWEEN 0 AND 3000),
    counts TEXT NOT NULL CHECK(json_valid(counts)),
    quota INTEGER NOT NULL CHECK(quota BETWEEN 1 AND 3000)
);
