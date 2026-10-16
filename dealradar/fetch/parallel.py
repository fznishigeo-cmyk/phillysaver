"""Bounded source slots. SQLite stays on its creating (pipeline) thread."""

from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue
import threading
import time
from urllib.parse import urlsplit

from ..errors import DeadlineExceeded
from .newsletter import registrable_domain


def source_hosts(source):
    """Only audited stateless web readers may enter the pool.

    Both the feed/API URL and the partner site are held for the whole source.
    Other adapters (including every paid/quota API) are exclusive barriers.
    """
    method, adapter = (source.method or "").lower(), (source.adapter or "").lower()
    independent = ((method in {"rss", "html"} and not adapter)
                   or (method == "html" and adapter in {
                       "bradsdeals", "dealsofamerica", "dealmoon_us", "morningsave"})
                   or (method == "api" and adapter in {"jungle_deals", "kcl_wall"}))
    if not independent:
        return None
    hosts = set()
    for url in (source.url, source.site):
        if not url:
            continue
        try:
            parts = urlsplit(url)
            host = registrable_domain(parts.hostname or "")
        except ValueError:
            return None
        if parts.scheme not in {"http", "https"} or not host:
            return None  # an unclassifiable host cannot be proved independent
        hosts.add(host)
    return hosts or None


class _MainThreadDB:
    """Marshal whole DB methods, including their commits, to the owner thread.

    No second connection, cross-thread SQLite flag, cursor or partially
    consumed DB iterator crosses into a worker. The allow-listed readers use
    only methods returning materialized values.
    """

    def __init__(self, db, events):
        self._db, self._events = db, events
        self._owner = threading.get_ident()

    def __getattr__(self, name):
        if threading.get_ident() == self._owner:
            return getattr(self._db, name)
        if name.startswith("_") or name in {"conn", "close"}:
            raise AttributeError(f"worker DB access is method-only: {name}")

        def call(*args, **kwargs):
            result = Future()
            self._events.put(("db", (name, args, kwargs, result)))
            return result.result()
        return call


def run_sources(sources, run, fetcher, db, limit, deadline):
    """Return results in input order; None means never started in the window.

    Unknown redirect/enrichment hosts are claimed before their first request
    and held until the source finishes too. A contested claim waits within the
    source deadline; even cyclic cross-site redirects cannot wait forever.
    """
    events = Queue()
    proxy = _MainThreadDB(db, events)
    groups = [source_hosts(s) for s in sources]
    pending = list(range(len(sources)))
    results = [None] * len(sources)
    active, held, waiting = {}, {}, []
    failures = []
    closing = False
    previous_db = fetcher.db if fetcher is not None else None

    def claim(index, host, stop):
        # netloc includes ports; the registrable slot deliberately does not.
        domain = registrable_domain(urlsplit("//" + host).hostname or "") or host
        if domain in held[index]:
            return
        answer = Future()
        events.put(("host", (index, domain, stop, answer)))
        answer.result()

    def worker(index):
        if fetcher is not None:
            fetcher._source_slot.claim = lambda host, stop: claim(index, host, stop)
        try:
            return run(sources[index], proxy)
        finally:
            if fetcher is not None:
                del fetcher._source_slot.claim

    def service():
        # Completion and DB requests share a wakeup queue, so waiting for a
        # network worker never strands that worker's SQLite call.
        try:
            kind, value = events.get(timeout=0.05)
        except Empty:
            return
        if kind == "db":
            name, args, kwargs, result = value
            try:
                result.set_result(getattr(db, name)(*args, **kwargs))
            except BaseException as exc:
                result.set_exception(exc)
        elif kind == "host":
            if closing:
                value[-1].set_exception(DeadlineExceeded("fetch stopped"))
            else:
                waiting.append(value)
        else:
            index = value
            future = active.pop(index)
            del held[index]
            try:
                results[index] = future.result()
            except BaseException as exc:
                failures.append(exc)
                pending.clear()

    pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="fetch")
    if fetcher is not None:
        fetcher.db = proxy
    try:
        while pending or active:
            for request in waiting[:]:
                index, domain, stop, answer = request
                if stop is not None and time.monotonic() >= stop:
                    answer.set_exception(DeadlineExceeded("deadline waiting for source host"))
                elif any(domain in hosts for owner, hosts in held.items() if owner != index):
                    continue
                else:
                    held[index].add(domain)
                    answer.set_result(None)
                waiting.remove(request)

            if time.monotonic() > deadline:
                pending.clear()
            # Earlier waiters own their place on shared hosts. An unrelated
            # host may pass them, but never pass a main-thread-only adapter.
            occupied = set().union(*held.values()) if held else set()
            for index in pending[:]:
                hosts = groups[index]
                if hosts is None:
                    if not active and index == pending[0]:
                        results[index] = run(sources[index], db)
                        pending.remove(index)
                    break
                if len(active) >= limit:
                    break
                if hosts & occupied:
                    occupied.update(hosts)
                    continue
                occupied.update(hosts)
                held[index] = set(hosts)
                active[index] = pool.submit(worker, index)
                active[index].add_done_callback(lambda _future, i=index: events.put(("done", i)))
                pending.remove(index)
            if active:
                service()
    finally:
        # On an unexpected orchestration error, finish servicing in-flight DB
        # calls before joining workers or restoring the fetcher's DB reference.
        try:
            closing = True
            for *_, answer in waiting:
                answer.set_exception(DeadlineExceeded("fetch stopped"))
            waiting.clear()
            while active:
                service()
        finally:
            pool.shutdown(wait=True)
            if fetcher is not None:
                fetcher.db = previous_db
    if failures:
        raise failures[0]
    return results
