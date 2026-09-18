import { setParseStatus } from "@/lib/db";
import { useParseStore, type ParseJob } from "@/stores/parseStore";
import { usePipelineStore } from "@/stores/pipelineStore";
import type { SyncWriteQueue } from "@/lib/syncWrites";

/** Stage-only signals must not be persisted as files.parse_status. */
const PARSE_STATUSES = new Set(["fast", "quality", "queued", "running", "error"]);

/** Keep the stale-heartbeat check, database commit and visible stage change in
 *  one queued operation. Checking before the queue lets a trailing heartbeat
 *  overtake a quality completion that is still waiting behind file writes. */
export function queueParseEvent(
  writes: SyncWriteQueue,
  runId: number | null,
  ev: ParseJob,
  onParsed: (subjectId: number, relativePath: string) => void,
  onError: (message: string) => void,
): Promise<boolean> {
  const path = ev.relative_path;
  if (!path) return Promise.resolve(false);
  return writes.enqueue(runId, `Saving parse status for ${path}`, async () => {
    if (ev.status === "running" && usePipelineStore.getState().items[path]?.quality === "done") return;

    if (PARSE_STATUSES.has(ev.status)) {
      await setParseStatus(ev.subject_id, path, ev.status);
      useParseStore.getState().update(ev);
    }

    const touch = usePipelineStore.getState().touch;
    switch (ev.status) {
      case "parsing":
        touch(path, ev.subject_id, { download: "done", fast: "active" });
        break;
      case "fast":
        touch(path, ev.subject_id, { download: "done", fast: "done", fastParsedAt: Date.now() });
        break;
      case "queued":
        touch(path, ev.subject_id, {
          download: "done", fast: "done", quality: "queued", qualityQueuePos: ev.position,
        });
        break;
      case "running":
        touch(path, ev.subject_id, {
          download: "done", fast: "done", quality: "active",
          pagesDone: ev.pages_done ?? 0, totalPages: ev.total_pages ?? 0,
          qualityQueuePos: undefined,
        });
        break;
      case "quality":
        touch(path, ev.subject_id, {
          download: "done", fast: "done", quality: "done", parsedAt: Date.now(),
        });
        break;
      case "error":
        touch(path, ev.subject_id, { quality: "error", error: ev.error ?? "Parse failed" });
        break;
      case "embedding":
        touch(path, ev.subject_id, {
          embed: "active", embedPagesDone: ev.pages_done ?? 0, embedTotalPages: ev.total_pages ?? 0,
        });
        break;
      case "embedded": {
        // The fast-pass embed is provisional; quality's embed is final.
        const final = usePipelineStore.getState().items[path]?.quality === "done";
        touch(path, ev.subject_id, {
          embed: final ? "done" : "pending", ...(final ? { embeddedAt: Date.now() } : {}),
        });
        break;
      }
      case "embed_error": {
        const final = usePipelineStore.getState().items[path]?.quality === "done";
        touch(path, ev.subject_id, final
          ? { embed: "error", error: ev.error ?? "Embed failed" }
          : { embed: "pending" });
        break;
      }
    }

    // Index after both fast and quality commits. The latter refreshes page
    // text even when its image vectors are unchanged.
    if (ev.status === "fast" || ev.status === "quality") onParsed(ev.subject_id, path);
  }, (message) => {
    useParseStore.getState().update({ ...ev, status: "error", error: message });
    usePipelineStore.getState().touch(path, ev.subject_id, { quality: "error", error: message });
    onError(message);
  });
}
