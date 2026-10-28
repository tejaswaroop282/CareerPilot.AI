# System design notes

## URL shortener

A short code of 7 characters in base62 gives about 3.5 trillion unique values, which is plenty for billions of links. Codes can be generated from a counter: each application server reserves a range of ids from ZooKeeper so no coordination is needed per request, then base62-encodes the id. The service is read heavy, roughly 100 reads for every write, so hot keys are cached in Redis in front of the database. A 301 redirect is cacheable by browsers and reduces load, while a 302 lets the service keep collecting click analytics.

## Rate limiter

The token bucket algorithm refills tokens at a fixed rate and allows short bursts up to the bucket size. A sliding window log is more accurate but stores every request timestamp, which costs memory. To make checks atomic across servers the logic runs as a Lua script inside Redis. When a client exceeds the limit the API returns HTTP 429 with a Retry-After header.

## Sharding with consistent hashing

Consistent hashing places servers on a ring and assigns each key to the next server clockwise, so adding or removing a node only moves a small share of keys. Virtual nodes spread each server over many points on the ring to even out load and to give stronger machines more points.

## Message queues

A queue decouples producers from consumers and absorbs traffic spikes. Dead letter queues collect messages that fail repeatedly so they can be inspected without blocking the main flow. Idempotent consumers make retries safe.
