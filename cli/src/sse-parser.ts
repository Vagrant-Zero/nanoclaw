/**
 * SSE protocol parser with proper event buffering.
 *
 * SSE events are separated by double newlines (\n\n). A single chunk
 * from the network may contain zero, one, or multiple complete events,
 * plus a partial trailing event. This parser buffers partial data
 * across `feed()` calls and emits complete events only.
 */

export interface SseEvent {
  event: string;
  data: unknown;
}

export class SseParser {
  private buffer = "";
  private currentEvent = "";

  /**
   * Feed a raw text chunk. Returns zero or more complete events.
   */
  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];

    // SSE protocol: events terminated by double newline
    while (true) {
      const delimIdx = this.buffer.indexOf("\n\n");
      if (delimIdx === -1) break;

      const rawEvent = this.buffer.slice(0, delimIdx);
      this.buffer = this.buffer.slice(delimIdx + 2);

      const parsed = this._parseRawEvent(rawEvent);
      if (parsed) {
        events.push(parsed);
      }
    }

    return events;
  }

  /**
   * Flush any remaining buffered data as a final event.
   * Call this when the stream ends.
   */
  flush(): SseEvent[] {
    if (!this.buffer.trim()) return [];
    const event = this._parseRawEvent(this.buffer.trim());
    this.buffer = "";
    return event ? [event] : [];
  }

  /**
   * Reset internal state (e.g. on reconnection).
   */
  reset(): void {
    this.buffer = "";
    this.currentEvent = "";
  }

  private _parseRawEvent(raw: string): SseEvent | null {
    const lines = raw.split(/\r?\n/);
    let event = this.currentEvent;
    let data: unknown = null;

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        event = line.slice(7);
      } else if (line.startsWith("data: ")) {
        const rawData = line.slice(6);
        try {
          data = JSON.parse(rawData);
        } catch {
          data = rawData;
        }
      }
    }

    if (data === null) return null;

    this.currentEvent = "";
    return { event, data };
  }
}
