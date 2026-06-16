import type { CaptureEvent } from "@/lib/api";

/** 合并后的时间线条目。
 * - transcription 段:把连续同源的多条转写合成一段(段落式),保留首/末时间戳与所有原始事件 id。
 * - 其它(note/clipboard/screenshot/pause/resume…):原样透传成单条。
 */
export type TimelineItem =
  | {
      kind: "transcription";
      /** 转写来源:"device"(系统声音)否则视作麦克风。 */
      source: string;
      /** 合并后的整段文本。 */
      text: string;
      /** 段内首/末事件的 ISO 时间戳(同一条时两者相等)。 */
      start_ts: string;
      end_ts: string;
      /** 被合并的原始事件 id(供 key / 调试 / 将来回跳)。 */
      ids: number[];
    }
  | {
      kind: "passthrough";
      /** 透传的原始事件(note/screenshot/pause/…)。 */
      event: CaptureEvent;
    };

/** 同一段落内相邻转写的最大时间间隔(秒);超过则断段(不同话题/长停顿单独成段)。 */
const PARAGRAPH_GAP_SECS = 30;

/** 取事件来源标识;transcription 用 meta.source,缺省视作 mic。 */
function eventSource(ev: CaptureEvent): string {
  const s = ev.meta?.source;
  return typeof s === "string" ? s : "mic";
}

/** 两个 ISO 时间戳相差秒数(绝对值)。无法解析时返回 Infinity(强制断段)。 */
function gapSeconds(aTs: string, bTs: string): number {
  const a = new Date(aTs).getTime();
  const b = new Date(bTs).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return Infinity;
  return Math.abs(b - a) / 1000;
}

/**
 * 把按时间排好的事件列表合并成时间线条目(FIX 2):
 * 连续、同 meta.source、且相邻间隔 ≤30s 的 transcription 事件合成一段段落;
 * 来源切换 / 出现非转写事件 / 间隔过大都断段。非转写事件原样透传。
 *
 * 纯函数,不改入参;中文转写用空格连接(可读、不黏连;空 payload 跳过)。
 */
export function groupTimelineItems(events: CaptureEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  // 当前正在累积的转写段;null 表示没有进行中的段。
  let cur:
    | { source: string; texts: string[]; start_ts: string; end_ts: string; ids: number[] }
    | null = null;

  const flush = () => {
    if (cur) {
      items.push({
        kind: "transcription",
        source: cur.source,
        text: cur.texts.join(" "),
        start_ts: cur.start_ts,
        end_ts: cur.end_ts,
        ids: cur.ids,
      });
      cur = null;
    }
  };

  for (const ev of events) {
    if (ev.kind !== "transcription") {
      flush();
      items.push({ kind: "passthrough", event: ev });
      continue;
    }
    const src = eventSource(ev);
    const text = (ev.payload || "").trim();
    // 断段条件:来源变了 / 与上一条间隔 >30s。
    if (cur && (cur.source !== src || gapSeconds(cur.end_ts, ev.ts) > PARAGRAPH_GAP_SECS)) {
      flush();
    }
    if (!cur) {
      cur = { source: src, texts: [], start_ts: ev.ts, end_ts: ev.ts, ids: [] };
    }
    if (text) cur.texts.push(text);
    cur.end_ts = ev.ts;
    cur.ids.push(ev.id);
  }
  flush();
  return items;
}
