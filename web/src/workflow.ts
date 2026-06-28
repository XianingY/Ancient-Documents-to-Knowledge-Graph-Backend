import type { ApiClient } from "./api";
import type { RelationGraph, StructuredResult, WorkflowProgress } from "./types";
import type { SegmentEditPayload } from "./proofreading";

type Sleep = (ms: number) => Promise<void>;

export type ReanalysisResult = {
  structured: StructuredResult;
  graph: RelationGraph;
};

const defaultSleep: Sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function newestId(ids: number[]) {
  return ids.length ? Math.max(...ids) : null;
}

async function pollForCompletedRefresh<T extends { id: number; created_at: string; status: string }>(
  fetchIds: () => Promise<number[]>,
  fetchItem: (id: number) => Promise<T>,
  previous: T | null,
  sleep: Sleep,
  maxAttempts: number,
) {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const ids = await fetchIds();
    const nextId = newestId(ids);
    if (nextId !== null) {
      const item = await fetchItem(nextId);
      const refreshed =
        previous === null ||
        item.id !== previous.id ||
        item.created_at !== previous.created_at;
      if (item.status === "failed") {
        return item;
      }
      if (refreshed && item.status === "done") {
        return item;
      }
    }
    await sleep(1500);
  }
  throw new Error("分析任务超时，请稍后刷新结果");
}

export async function saveOcrAndReanalyze(
  api: Pick<
    ApiClient,
    | "updateOcrResult"
    | "reanalyzeOcrResult"
    | "listStructuredResults"
    | "getStructuredResult"
    | "listRelationGraphs"
    | "getRelationGraph"
  >,
  ocrId: number,
  rawText: string,
  onProgress: (progress: WorkflowProgress) => void,
  options: { sleep?: Sleep; maxAttempts?: number; segmentEdits?: SegmentEditPayload[] } = {},
): Promise<ReanalysisResult> {
  const sleep = options.sleep ?? defaultSleep;
  const maxAttempts = options.maxAttempts ?? 40;

  const beforeStructuredId = newestId((await api.listStructuredResults(ocrId)).ids);
  const beforeStructured = beforeStructuredId
    ? await api.getStructuredResult(beforeStructuredId)
    : null;
  onProgress({ stage: "saving", message: "正在保存 OCR 修订" });
  await api.updateOcrResult(ocrId, rawText, options.segmentEdits);

  onProgress({ stage: "structured", message: "正在重新提取结构化字段" });
  await api.reanalyzeOcrResult(ocrId);
  const structured = await pollForCompletedRefresh(
    async () => (await api.listStructuredResults(ocrId)).ids,
    (id) => api.getStructuredResult(id),
    beforeStructured,
    sleep,
    maxAttempts,
  );
  if (structured.status === "failed") {
    throw new Error("结构化分析失败");
  }

  const beforeGraphId = newestId((await api.listRelationGraphs(structured.id)).ids);
  const beforeGraph = beforeGraphId ? await api.getRelationGraph(beforeGraphId) : null;
  onProgress({ stage: "graph", message: "正在生成知识图谱" });
  const graph = await pollForCompletedRefresh(
    async () => (await api.listRelationGraphs(structured.id)).ids,
    (id) => api.getRelationGraph(id),
    beforeGraph,
    sleep,
    maxAttempts,
  );
  if (graph.status === "failed") {
    throw new Error("知识图谱生成失败");
  }

  onProgress({ stage: "done", message: "OCR 修订已同步到结构化结果和知识图谱" });
  return { structured, graph };
}
