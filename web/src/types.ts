export type ApiEnvelope<T> = {
  success: boolean;
  data: T;
  message?: string;
  detail?: string;
};

export type LoginResponse = {
  success: boolean;
  access_token: string;
  token_type: string;
  user_id: number;
  username: string;
};

export type UserInfo = {
  id: number;
  username: string;
  email?: string | null;
  created_at: string;
};

export type ImageItem = {
  id: number;
  filename: string;
  upload_time: string;
  title: string;
};

export type PagedIds<T = unknown> = {
  total: number;
  skip: number;
  limit: number;
  ids: number[];
  items?: T[];
};

export type OcrSegment = {
  bbox?: number[];
  text?: string;
  status?: string;
  confidence?: number;
  rejection_reasons?: string[];
  medium_text?: string;
  small_text?: string;
};

export type OcrResult = {
  id: number;
  image_id: number;
  raw_text: string;
  status: string;
  confidence: number;
  coverage: number;
  engine?: string | null;
  model_versions?: string | null;
  segments: OcrSegment[];
  rejection_reasons: string[];
  human_corrected: boolean;
  created_at: string;
};

export type StructuredResult = {
  id: number;
  ocr_result_id: number;
  content: Record<string, unknown> | string;
  status: string;
  created_at: string;
};

export type RelationGraph = {
  id: number;
  structured_result_id: number;
  content: Record<string, unknown> | string;
  status: string;
  created_at: string;
};

export type MultiRelationGraph = {
  id: number;
  multi_task_id: number;
  content: Record<string, unknown> | string;
  status: string;
  created_at: string;
};

export type StatisticsData = {
  total_images: number;
  total_analyzed: number;
  time_range: { start?: number; end?: number; span?: number };
  time_distribution: Array<{ year: number; count: number }>;
  location_distribution: Array<{ name: string; count: number }>;
  top_people: Array<{ name: string; count: number }>;
  price_trend: Array<{ year: number; avg_price: number; count: number }>;
};

export type WorkflowProgress = {
  stage: "saving" | "structured" | "graph" | "done";
  message: string;
};
