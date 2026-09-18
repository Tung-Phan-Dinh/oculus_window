import { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  upsertFile, finishSyncRun, addLog,
  addSyncRunFile, getSyncOptions, markFileContentChanged, resetFilePipeline,
  upsertLectures, replaceCalendarEvents,
  type CalendarEventData, type LectureData, type SyncFileAction,
} from "@/lib/db";
import { useSyncStore } from "@/stores/syncStore";
import { usePipelineStore } from "@/stores/pipelineStore";
import type { SyncProgress } from "@/stores/syncStore";
import type { ParseJob } from "@/stores/parseStore";
import { embedFile } from "@/lib/retrieval";
import { isPdfBacked } from "@/lib/fileTypes";
import { CALENDAR_UPDATED_EVENT } from "@/lib/calendar";
import { notifyProjectsUpdated } from "@/lib/projects";
import { getDb } from "@/lib/db";
import { useHarnessStore } from "@/stores/harnessStore";
import type { HarnessEnvelope } from "@/lib/harness";
import { SCRAPED_FILE_FAILED_EVENT, SCRAPED_FILE_SAVED_EVENT, SyncWriteQueue } from "@/lib/syncWrites";

import { queueParseEvent } from "@/lib/parseEvents";

/**
 * Index a PDF right after it parses.
 *
 * Fire-and-forget: a failed embed must never block or fail the parse flow, and
 * `embedPending()` will retry it later. Serialised through one promise chain
 * because the sidecar holds a single model — firing these in parallel would
 * queue on the GPU anyway.
 */
let embedChain: Promise<unknown> = Promise.resolve();

async function embedAfterParse(subjectId: number, relativePath: string) {
  if (!isPdfBacked(relativePath)) return;
  embedChain = embedChain.then(async () => {
    try {
      const db = await getDb();
      const rows = await db.select<{ id: number }[]>(
        `SELECT id FROM files WHERE subject_id = $1 AND relative_path = $2`,
        [subjectId, relativePath],
      );
      const fileId = rows[0]?.id;
      if (fileId == null) return;
      await embedFile(fileId, relativePath);
    } catch (e) {
      console.error("embed after parse failed", relativePath, e);
    }
  });
}

const isPipelinePdf = (path: string) => isPdfBacked(path);

/**
 * Single app-level bridge: subscribes to all backend Tauri events and writes
 * them into the global stores (and the DB). Mount ONCE near the app root so
 * progress survives page navigation. UI reads from the stores, never listens
 * directly.
 */
export function useBackendEvents() {
  useEffect(() => {
    const unsubs: Array<Promise<() => void>> = [];
    const pipeline = () => usePipelineStore.getState();
    const writes = new SyncWriteQueue();
    const reportWriteFailure = (message: string) => {
      console.error(message);
      useSyncStore.setState({ error: message });
    };
    const failRun = async (runId: number | null, message: string) => {
      try {
        if (runId != null) await finishSyncRun(runId, "failed", 0, 0, message);
      } catch (reason) {
        message += ` Could not save the failed run either: ${String(reason)}`;
      }
      useSyncStore.getState().fail(message);
      pipeline().failStalledDownloads();
    };

    // ── CLI agents ──────────────────────────────────────────────────────────
    // App-level, not page-level: a thread keeps running while you are on
    // another page, and the sidebar's spinner needs to know.
    //
    // The second half of this listener is the one hop that keeps an open board
    // honest. `oculus project` / `oculus task` write the same tables the board
    // reads, but from a *separate process* with its own connection — nothing
    // in this webview's pool notices a row the agent wrote. So when a tool call
    // whose command names a project or a task finishes, the board is told to
    // re-read. `tool_finished` carries only the call's id, not its command, so
    // the ids worth watching are remembered from `tool_started`.
    //
    // The test is the command text alone, not `kind === "oculus_cli"`:
    // `is_oculus_cli` in app/src-tauri/src/harness/event.rs only word-matches
    // the first few words, so `cd … && oculus task add` classifies as plain
    // Bash and a kind gate would drop exactly the write this hop exists for.
    // The trade is deliberate and one-sided — a false positive (an agent
    // grepping for the words "oculus task") costs one re-read of a handful of
    // rows, a false negative costs a board that is silently wrong.
    const planningCalls = new Set<string>();
    const WRITES_PLANNING = /\boculus\s+(project|task)\b/;
    unsubs.push(
      listen<HarnessEnvelope>("harness-event", (e) => {
        useHarnessStore.getState().apply(e.payload);
        const ev = e.payload.event;
        if (ev.type === "tool_started") {
          // `title` is the whole command for a Bash-shaped tool, which is what
          // makes matching on it possible at all.
          if (WRITES_PLANNING.test(ev.title)) {
            planningCalls.add(`${e.payload.threadId}:${ev.id}`);
          }
        } else if (ev.type === "tool_finished") {
          // Regardless of `ok`, for the same reason: a failed write is atomic
          // and changes nothing, but a command that timed out may still have
          // landed.
          if (planningCalls.delete(`${e.payload.threadId}:${ev.id}`)) {
            notifyProjectsUpdated();
          }
        }
      }),
    );

    // ── Sync (scrape) events ────────────────────────────────────────────────
    unsubs.push(
      listen<SyncProgress>("scrape-progress", (e) => {
        if (e.payload.phase === "complete") return;
        useSyncStore.getState().setProgress(e.payload);
      }),
    );
    unsubs.push(
      listen<{ subject_id: number; relative_path: string; filename: string }>(
        "scrape-file-start",
        (e) => {
          const { subject_id, relative_path } = e.payload;
          if (!isPipelinePdf(relative_path)) return;
          pipeline().touch(relative_path, subject_id, { download: "active" });
        },
      ),
    );
    unsubs.push(
      listen<{ subject_id: number; relative_path: string; size_bytes: number; category: string | null; canvas_id: number | null; source_url: string | null; action: SyncFileAction }>(
        "scrape-file",
        (e) => {
          const { subject_id, relative_path, size_bytes, category, canvas_id, source_url, action } = e.payload;
          const runId = useSyncStore.getState().runId;
          const filename = relative_path.split("/").pop() ?? relative_path;
          const ext = filename.includes(".") ? filename.split(".").pop()! : "md";
          void writes.enqueue(runId, `Saving ${relative_path}`, async () => {
            await upsertFile(subject_id, filename, relative_path, ext, size_bytes, category ?? undefined, canvas_id ?? undefined, source_url ?? undefined);
            if (action === "new" || action === "updated") {
              await markFileContentChanged(subject_id, relative_path);
            }
            // Changed bytes: the Rust side purged the on-disk parse artifacts;
            // clear the DB's stale statuses and stored pages so the pipeline
            // re-runs and search never serves the old text.
            if (action === "updated" && isPdfBacked(relative_path)) {
              await resetFilePipeline(subject_id, relative_path);
            }
            // The ledger is committed only after the file it claims exists.
            if (runId != null) {
              await addSyncRunFile(runId, subject_id, relative_path, action ?? "new", size_bytes);
            }
            if (isPipelinePdf(relative_path)) {
              pipeline().touch(relative_path, subject_id, { download: "done", downloadedAt: Date.now() });
            }
            window.dispatchEvent(new CustomEvent(SCRAPED_FILE_SAVED_EVENT, { detail: e.payload }));
          }, (message) => {
            reportWriteFailure(message);
            window.dispatchEvent(new CustomEvent(SCRAPED_FILE_FAILED_EVENT, { detail: { ...e.payload, error: message } }));
            if (isPipelinePdf(relative_path)) {
              pipeline().touch(relative_path, subject_id, { download: "error", error: message });
            }
          });
        },
      ),
    );
    unsubs.push(
      listen<{ level: string; course: string; message: string }>("scrape-log", (e) => {
        const { level, message } = e.payload;
        const mapped = level === "error" ? "error" : level === "warning" ? "warning" : "info";
        addLog(message, mapped).catch(() => {});
      }),
    );
    unsubs.push(
      listen<{ count: number; cancelled?: boolean }>("scrape-complete", async (e) => {
        const { runId, subjects } = useSyncStore.getState();
        await writes.drain();
        const failure = writes.takeFailure(runId);
        if (failure) {
          await failRun(runId, failure);
          return;
        }
        try {
          if (runId != null) {
            await finishSyncRun(runId, e.payload.cancelled ? "failed" : "completed", e.payload.count, e.payload.count,
              e.payload.cancelled ? "Sync cancelled" : undefined);
            await addLog(e.payload.cancelled ? "Sync cancelled" : `Synced ${e.payload.count} subject(s)`);
          }
        } catch (reason) {
          await failRun(runId, `Could not save sync completion: ${String(reason)}`);
          return;
        }
        useSyncStore.getState().complete(e.payload.count, !!e.payload.cancelled);
        // Anything still "downloading" now will never finish — the run is over.
        pipeline().failStalledDownloads();
        // Refresh each subject's Echo360 lecture *list* (metadata only, no
        // video downloads) — same call the Lectures tab's button makes. A
        // course without Echo, or a failed LTI launch, just logs and moves on.
        if (!e.payload.cancelled && subjects.length > 0) {
          const { lectures, calendar } = await getSyncOptions();
          if (lectures) {
            for (const s of subjects) {
              try {
                const data = await invoke<LectureData[]>("echo360_sync_lectures", {
                  canvasCourseId: s.id,
                });
                await upsertLectures(s.id, data);
              } catch (err) {
                addLog(`lectures ${s.code}: ${err}`, "warning").catch(() => {});
              }
            }
          }
          // Class times and due dates, from Canvas's calendar API. Same shape
          // as the lecture refresh above: a post-scrape pass the frontend
          // drives, since nothing about it lands on disk.
          if (calendar) {
            for (const s of subjects) {
              try {
                const rows = await invoke<CalendarEventData[]>("calendar_sync_events", {
                  canvasCourseId: s.id,
                });
                await replaceCalendarEvents(s.id, rows);
              } catch (err) {
                addLog(`calendar ${s.code}: ${err}`, "warning").catch(() => {});
              }
            }
            window.dispatchEvent(new CustomEvent(CALENDAR_UPDATED_EVENT));
          }
        }
      }),
    );
    unsubs.push(
      listen<string>("scrape-error", async (e) => {
        const runId = useSyncStore.getState().runId;
        await writes.drain();
        const failure = writes.takeFailure(runId);
        await failRun(runId, [typeof e.payload === "string" ? e.payload : "Sync error", failure].filter(Boolean).join(" "));
      }),
    );

    // ── PDF parse + embed stage events ──────────────────────────────────────
    unsubs.push(
      listen<ParseJob>("parse-status", (e) => {
        void queueParseEvent(writes, useSyncStore.getState().runId, e.payload,
          embedAfterParse, reportWriteFailure);
      }),
    );
    return () => {
      unsubs.forEach((u) => u.then((f) => f()).catch(() => {}));
    };
  }, []);
}
