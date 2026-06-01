/** Shared polling: one interval per URL, skip if in-flight, AbortController on unmount. */

type Subscriber<T> = (data: T | null, error: string | null, loading: boolean) => void;

type PollEntry = {
  intervalMs: number;
  subscribers: Set<Subscriber<unknown>>;
  timer: ReturnType<typeof setInterval> | null;
  inFlight: boolean;
  abort: AbortController | null;
  lastData: unknown;
  lastError: string | null;
};

const entries = new Map<string, PollEntry>();

function fetchOnce(url: string, entry: PollEntry) {
  if (entry.inFlight) return;
  if (typeof document !== 'undefined' && document.hidden) return;

  entry.inFlight = true;
  entry.abort?.abort();
  entry.abort = new AbortController();

  const notify = (loading: boolean) => {
    entry.subscribers.forEach((cb) =>
      cb(entry.lastData, entry.lastError, loading)
    );
  };

  notify(true);

  fetch(url, { signal: entry.abort.signal })
    .then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((data) => {
      entry.lastData = data;
      entry.lastError = null;
      entry.inFlight = false;
      notify(false);
    })
    .catch((err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') {
        entry.inFlight = false;
        return;
      }
      entry.lastError = err instanceof Error ? err.message : 'Unknown error';
      entry.inFlight = false;
      notify(false);
    });
}

function ensureTimer(url: string, entry: PollEntry) {
  if (entry.timer != null) return;
  fetchOnce(url, entry);
  entry.timer = setInterval(() => fetchOnce(url, entry), entry.intervalMs);
}

function stopTimerIfIdle(url: string, entry: PollEntry) {
  if (entry.subscribers.size > 0) return;
  if (entry.timer != null) {
    clearInterval(entry.timer);
    entry.timer = null;
  }
  entry.abort?.abort();
  entry.abort = null;
  entries.delete(url);
}

export function subscribePoll<T>(
  url: string,
  intervalMs: number,
  callback: Subscriber<T>
): () => void {
  let entry = entries.get(url);
  if (!entry) {
    entry = {
      intervalMs,
      subscribers: new Set(),
      timer: null,
      inFlight: false,
      abort: null,
      lastData: null,
      lastError: null,
    };
    entries.set(url, entry);
  } else {
    entry.intervalMs = intervalMs;
  }

  const wrapped = callback as Subscriber<unknown>;
  entry.subscribers.add(wrapped);

  if (entry.lastData != null || entry.lastError != null) {
    callback(entry.lastData as T | null, entry.lastError, false);
  }

  ensureTimer(url, entry);

  return () => {
    entry?.subscribers.delete(wrapped);
    if (entry) stopTimerIfIdle(url, entry);
  };
}

export function refetchPoll(url: string) {
  const entry = entries.get(url);
  if (entry) fetchOnce(url, entry);
}
