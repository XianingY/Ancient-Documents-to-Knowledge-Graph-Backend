import type {
  ApiEnvelope,
  ImageItem,
  LoginResponse,
  MultiRelationGraph,
  OcrResult,
  PagedIds,
  RelationGraph,
  StatisticsData,
  StructuredResult,
  UserInfo,
} from "./types";

export const DEMO_USERNAME = "demo_web";
export const DEMO_PASSWORD = "DemoWeb2026!";

const DEFAULT_API_BASE = "/api";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ApiClient {
  private baseUrl: string;
  private token: string | null;

  constructor(baseUrl = DEFAULT_API_BASE, token: string | null = null) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token = token;
  }

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  private url(path: string) {
    const clean = path.startsWith("/") ? path : `/${path}`;
    return `${this.baseUrl}/v1${clean}`;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (this.token) {
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(this.url(path), { ...init, headers });
    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }

    if (!response.ok) {
      const detail =
        typeof payload === "object" && payload && "detail" in payload
          ? String((payload as { detail: unknown }).detail)
          : response.statusText;
      throw new ApiError(detail || "请求失败", response.status);
    }
    return payload as T;
  }

  async login(username: string, password: string) {
    const payload = await this.request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    this.setToken(payload.access_token);
    return payload;
  }

  async currentUser() {
    const payload = await this.request<{ success: boolean; user: UserInfo }>("/users/me");
    return payload.user;
  }

  async listImages(limit = 80) {
    const payload = await this.request<ApiEnvelope<PagedIds<ImageItem>>>(
      `/users/images?limit=${limit}`,
    );
    return payload.data;
  }

  async uploadImage(file: File) {
    const form = new FormData();
    form.set("image", file);
    return this.request<{ success: boolean; imageId: number; filename: string }>(
      "/images/upload",
      {
        method: "POST",
        body: form,
      },
    );
  }

  async imageBlobUrl(imageId: number) {
    const headers = new Headers();
    if (this.token) {
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const response = await fetch(this.url(`/images/${imageId}`), { headers });
    if (!response.ok) {
      throw new ApiError("图片加载失败", response.status);
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  }

  async listOcrResults(imageId: number) {
    const payload = await this.request<ApiEnvelope<PagedIds>>(
      `/images/${imageId}/ocr-results?limit=20`,
    );
    return payload.data;
  }

  async triggerOcr(imageId: number) {
    return this.request<{ success: boolean; message: string }>(`/images/${imageId}/ocr`, {
      method: "POST",
    });
  }

  async getOcrResult(ocrId: number) {
    const payload = await this.request<ApiEnvelope<OcrResult>>(`/ocr-results/${ocrId}`);
    return payload.data;
  }

  async updateOcrResult(
    ocrId: number,
    rawText: string,
    segmentEdits?: Array<{ segment_id: string; text: string }>,
  ) {
    const payload = await this.request<ApiEnvelope<OcrResult>>(`/ocr-results/${ocrId}`, {
      method: "PATCH",
      body: JSON.stringify({
        raw_text: rawText,
        ...(segmentEdits ? { segment_edits: segmentEdits } : {}),
      }),
    });
    return payload.data;
  }

  async reanalyzeOcrResult(ocrId: number) {
    return this.request<{ success: boolean; message: string }>(
      `/ocr-results/${ocrId}/reanalyze`,
      {
        method: "POST",
      },
    );
  }

  async createStructuredResult(ocrResultId: number) {
    return this.request<{ success: boolean; message: string }>("/structured-results", {
      method: "POST",
      body: JSON.stringify({ ocr_result_id: ocrResultId }),
    });
  }

  async listStructuredResults(ocrResultId: number) {
    const payload = await this.request<ApiEnvelope<PagedIds>>(
      `/ocr-results/${ocrResultId}/structured-results?limit=20`,
    );
    return payload.data;
  }

  async getStructuredResult(structuredResultId: number) {
    const payload = await this.request<ApiEnvelope<StructuredResult>>(
      `/structured-results/${structuredResultId}`,
    );
    return payload.data;
  }

  async createRelationGraph(structuredResultId: number) {
    return this.request<{ success: boolean; message: string }>("/relation-graphs", {
      method: "POST",
      body: JSON.stringify({ structured_result_id: structuredResultId }),
    });
  }

  async listRelationGraphs(structuredResultId: number) {
    const payload = await this.request<ApiEnvelope<PagedIds>>(
      `/structured-results/${structuredResultId}/relation-graphs?limit=20`,
    );
    return payload.data;
  }

  async getRelationGraph(relationGraphId: number) {
    const payload = await this.request<ApiEnvelope<RelationGraph>>(
      `/relation-graphs/${relationGraphId}`,
    );
    return payload.data;
  }

  async createMultiTaskFromImages(imageIds: number[]) {
    return this.request<{
      success: boolean;
      multi_task_id: number;
      structured_result_ids: number[];
    }>("/multi-tasks/from-images", {
      method: "POST",
      body: JSON.stringify({ image_ids: imageIds }),
    });
  }

  async listMultiRelationGraphs(multiTaskId: number) {
    const payload = await this.request<ApiEnvelope<PagedIds>>(
      `/multi-tasks/${multiTaskId}/multi-relation-graphs?limit=20`,
    );
    return payload.data;
  }

  async getMultiRelationGraph(multiRelationGraphId: number) {
    const payload = await this.request<ApiEnvelope<MultiRelationGraph>>(
      `/multi-relation-graphs/${multiRelationGraphId}`,
    );
    return payload.data;
  }

  async statistics() {
    const payload = await this.request<ApiEnvelope<StatisticsData>>("/statistics");
    return payload.data;
  }
}

export function createApiClient(token: string | null) {
  const envBase = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE;
  return new ApiClient(envBase, token);
}
