import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import {
  BarChart3,
  BookOpen,
  Check,
  Database,
  FileText,
  GitBranch,
  Loader2,
  LogOut,
  Network,
  RefreshCw,
  Save,
  Search,
  Upload,
} from "lucide-react";
import { ApiError, DEMO_PASSWORD, DEMO_USERNAME, createApiClient } from "./api";
import type {
  ImageItem,
  MultiRelationGraph,
  OcrResult,
  RelationGraph,
  StatisticsData,
  StructuredResult,
  UserInfo,
  WorkflowProgress,
} from "./types";
import { saveOcrAndReanalyze } from "./workflow";
import { DocumentViewer } from "./OcrProofreader";

type Tab = "document" | "proofread" | "graph" | "multi" | "statistics";

const TOKEN_KEY = "wenzhi_web_token";
const CORE_FIELDS = [
  ["Time", "时间"],
  ["Time_AD", "公元年份"],
  ["Location", "地点"],
  ["Seller", "卖方"],
  ["Buyer", "买方"],
  ["Middleman", "中人"],
  ["Price", "价格"],
  ["Subject", "标的"],
  ["Translation", "译文"],
] as const;

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "操作失败";
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function displayValue(value: unknown) {
  if (value && typeof value === "object" && "value" in value) {
    return String((value as { value?: unknown }).value ?? "");
  }
  if (Array.isArray(value)) {
    return value.join("、");
  }
  if (value === null || value === undefined || value === "") {
    return "未识别";
  }
  return String(value);
}

function displayTitle(title: string) {
  return title.replace(/demo_web_src_\d+_/g, "");
}

function newest(ids: number[]) {
  return ids.length ? Math.max(...ids) : null;
}

function normalizeChartOption(option: unknown) {
  const content = typeof option === "string" ? {} : asObject(option);
  const series = content.series;
  if (!Array.isArray(series)) {
    return content;
  }
  return {
    ...content,
    series: series.map((item) => {
      const seriesItem = asObject(item);
      if (seriesItem.type !== "graph") {
        return item;
      }
      return {
        ...seriesItem,
        roam: true,
        draggable: true,
        top: 40,
        bottom: 70,
        left: 60,
        right: 60,
        scaleLimit: { min: 0.35, max: 4 },
        force: {
          repulsion: 220,
          edgeLength: [90, 170],
          gravity: 0.04,
          ...asObject(seriesItem.force),
        },
        emphasis: {
          focus: "adjacency",
          ...asObject(seriesItem.emphasis),
        },
      };
    }),
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function GraphView({ option }: { option: unknown }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!hostRef.current) {
      return;
    }
    const chart = echarts.init(hostRef.current);
    chartRef.current = chart;
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) {
      return;
    }
    const content = normalizeChartOption(option);
    chartRef.current.setOption(content, true);
  }, [option]);

  return <div ref={hostRef} className="graph-canvas" />;
}

function LoginView({
  onLogin,
  busy,
  error,
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  busy: boolean;
  error: string;
}) {
  const [username, setUsername] = useState(DEMO_USERNAME);
  const [password, setPassword] = useState(DEMO_PASSWORD);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onLogin(username, password);
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="brand-mark">
          <BookOpen size={34} />
        </div>
        <h1>文渊智图</h1>
        <p>古代地契 OCR 修订与知识图谱展示端</p>
        <form onSubmit={submit} className="login-form">
          <label>
            用户名
            <input value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && <div className="error-box">{error}</div>}
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? <Loader2 className="spin" size={18} /> : <Check size={18} />}
            进入演示账号
          </button>
        </form>
        <code className="seed-hint">python scripts/seed_demo_web.py</code>
      </section>
    </main>
  );
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const api = useMemo(() => createApiClient(token), [token]);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [selectedImageId, setSelectedImageId] = useState<number | null>(null);
  const [selectedForMulti, setSelectedForMulti] = useState<number[]>([]);
  const [imageUrl, setImageUrl] = useState<string>("");
  const imageUrlRef = useRef<string>("");
  const [ocrResult, setOcrResult] = useState<OcrResult | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [structured, setStructured] = useState<StructuredResult | null>(null);
  const [graph, setGraph] = useState<RelationGraph | null>(null);
  const [multiGraph, setMultiGraph] = useState<MultiRelationGraph | null>(null);
  const [statistics, setStatistics] = useState<StatisticsData | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("document");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");

  const selectedImage = images.find((item) => item.id === selectedImageId) ?? null;
  const structuredContent = asObject(structured?.content);

  const loadImages = useCallback(async () => {
    const data = await api.listImages(120);
    const items = data.items ?? [];
    setImages(items);
    setSelectedImageId((current) => current ?? items[0]?.id ?? null);
  }, [api]);

  const loadStatistics = useCallback(async () => {
    setStatistics(await api.statistics());
  }, [api]);

  const loadDocument = useCallback(
    async (imageId: number) => {
      setBusy(true);
      setError("");
      setStatus("正在加载文书");
      setOcrResult(null);
      setStructured(null);
      setGraph(null);
      setCorrectedText("");
      try {
        if (imageUrlRef.current) {
          URL.revokeObjectURL(imageUrlRef.current);
        }
        const nextUrl = await api.imageBlobUrl(imageId);
        imageUrlRef.current = nextUrl;
        setImageUrl(nextUrl);

        const ocrIds = (await api.listOcrResults(imageId)).ids;
        const ocrId = newest(ocrIds);
        if (!ocrId) {
          setStatus("暂无 OCR 结果，已提交识别任务");
          await api.triggerOcr(imageId);
          return;
        }

        const ocr = await api.getOcrResult(ocrId);
        setOcrResult(ocr);
        setCorrectedText(ocr.corrected_text || ocr.raw_text || "");

        const structuredId = newest((await api.listStructuredResults(ocr.id)).ids);
        if (structuredId) {
          const nextStructured = await api.getStructuredResult(structuredId);
          setStructured(nextStructured);
          const graphId = newest((await api.listRelationGraphs(nextStructured.id)).ids);
          if (graphId) {
            setGraph(await api.getRelationGraph(graphId));
          }
        }
        setStatus("");
      } catch (caught) {
        setError(errorMessage(caught));
      } finally {
        setBusy(false);
      }
    },
    [api],
  );

  async function login(username: string, password: string) {
    setLoginBusy(true);
    setLoginError("");
    try {
      const result = await api.login(username, password);
      localStorage.setItem(TOKEN_KEY, result.access_token);
      setToken(result.access_token);
    } catch (caught) {
      setLoginError(`${errorMessage(caught)}。请先运行 python scripts/seed_demo_web.py 初始化演示账号。`);
    } finally {
      setLoginBusy(false);
    }
  }

  async function logout() {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setImages([]);
    setSelectedImageId(null);
    setCorrectedText("");
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("image") as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    setBusy(true);
    setError("");
    setStatus("正在上传图片");
    try {
      const result = await api.uploadImage(file);
      await loadImages();
      setSelectedImageId(result.imageId);
      input.value = "";
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function saveOcr() {
    if (!ocrResult) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await saveOcrAndReanalyze(
        api,
        ocrResult.id,
        correctedText,
        (progress: WorkflowProgress) => setStatus(progress.message),
      );
      setStructured(result.structured);
      setGraph(result.graph);
      const refreshed = await api.getOcrResult(ocrResult.id);
      setOcrResult(refreshed);
      setCorrectedText(refreshed.corrected_text || refreshed.raw_text || "");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function runMultiDocumentGraph() {
    if (selectedForMulti.length < 2) {
      setError("请至少选择两篇文书");
      return;
    }
    setBusy(true);
    setError("");
    setStatus("正在创建跨文档分析");
    try {
      const task = await api.createMultiTaskFromImages(selectedForMulti);
      let graphId: number | null = null;
      for (let index = 0; index < 40; index += 1) {
        const ids = (await api.listMultiRelationGraphs(task.multi_task_id)).ids;
        graphId = newest(ids);
        if (graphId) {
          break;
        }
        setStatus("正在合并实体与关系");
        await sleep(1500);
      }
      if (!graphId) {
        throw new Error("跨文档图谱生成超时");
      }
      setMultiGraph(await api.getMultiRelationGraph(graphId));
      setActiveTab("multi");
      setStatus("跨文档图谱已生成");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function toggleMulti(imageId: number) {
    setSelectedForMulti((current) =>
      current.includes(imageId)
        ? current.filter((id) => id !== imageId)
        : [...current, imageId],
    );
  }

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    async function bootstrap() {
      try {
        const currentUser = await api.currentUser();
        if (cancelled) {
          return;
        }
        setUser(currentUser);
        await loadImages();
        await loadStatistics();
      } catch (caught) {
        if (cancelled) {
          return;
        }
        setError(errorMessage(caught));
        localStorage.removeItem(TOKEN_KEY);
        setToken(null);
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [api, loadImages, loadStatistics, token]);

  useEffect(() => {
    if (selectedImageId) {
      loadDocument(selectedImageId);
    }
  }, [loadDocument, selectedImageId]);

  useEffect(() => {
    return () => {
      if (imageUrlRef.current) {
        URL.revokeObjectURL(imageUrlRef.current);
      }
    };
  }, []);

  if (!token || !user) {
    return <LoginView onLogin={login} busy={loginBusy} error={loginError} />;
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="brand-line">
            <BookOpen size={24} />
            <span>文渊智图 Web</span>
          </div>
          <p>{user.username} · OCR 修订 · 知识图谱</p>
        </div>
        <nav className="tabbar" aria-label="主视图">
          <button className={activeTab === "document" ? "active" : ""} onClick={() => setActiveTab("document")}>
            <FileText size={17} /> 文书
          </button>
          <button className={activeTab === "proofread" ? "active" : ""} onClick={() => setActiveTab("proofread")}>
            <Save size={17} /> 人工修订
          </button>
          <button className={activeTab === "graph" ? "active" : ""} onClick={() => setActiveTab("graph")}>
            <GitBranch size={17} /> 单图谱
          </button>
          <button className={activeTab === "multi" ? "active" : ""} onClick={() => setActiveTab("multi")}>
            <Network size={17} /> 跨文档
          </button>
          <button className={activeTab === "statistics" ? "active" : ""} onClick={() => setActiveTab("statistics")}>
            <BarChart3 size={17} /> 统计
          </button>
        </nav>
        <button className="icon-button" onClick={logout} title="退出登录">
          <LogOut size={18} />
        </button>
      </header>

      <section className="workspace">
        <aside className="sidebar">
          <form onSubmit={upload} className="upload-row">
            <label className="upload-button">
              <Upload size={16} />
              上传
              <input name="image" type="file" accept="image/*" onChange={(event) => event.currentTarget.form?.requestSubmit()} />
            </label>
            <button type="button" className="icon-button" onClick={loadImages} title="刷新列表">
              <RefreshCw size={16} />
            </button>
          </form>
          <div className="search-box">
            <Search size={16} />
            <span>{images.length} 篇文书</span>
          </div>
          <div className="document-list">
            {images.map((item) => (
              <button
                key={item.id}
                className={`document-item ${selectedImageId === item.id ? "selected" : ""}`}
                onClick={() => setSelectedImageId(item.id)}
              >
                <input
                  type="checkbox"
                  checked={selectedForMulti.includes(item.id)}
                  onChange={() => toggleMulti(item.id)}
                  onClick={(event) => event.stopPropagation()}
                  aria-label={`选择 ${displayTitle(item.title)}`}
                />
                <span>
                  <strong>{displayTitle(item.title)}</strong>
                  <small>{new Date(item.upload_time).toLocaleString()}</small>
                </span>
              </button>
            ))}
          </div>
          <button className="secondary-button" onClick={runMultiDocumentGraph} disabled={busy}>
            <Network size={17} />
            生成跨文档图谱
          </button>
        </aside>

        {activeTab === "statistics" ? (
          <section className="content-wide statistics-content">
            <StatisticsView statistics={statistics} onRefresh={loadStatistics} />
          </section>
        ) : activeTab === "proofread" ? (
          <section className="proofread-grid">
            <div className="image-panel">
              <div className="section-head">
                <div>
                  <h2>{selectedImage ? displayTitle(selectedImage.title) : "文书原图"}</h2>
                  <p>{selectedImage?.filename ?? ""}</p>
                </div>
              </div>
              {imageUrl ? (
                <DocumentViewer
                  imageUrl={imageUrl}
                  imageTitle={selectedImage ? displayTitle(selectedImage.title) : "文书图片"}
                />
              ) : (
                <EmptyState title="未加载图片" text="从左侧选择一篇文书。" />
              )}
            </div>

            <div className="revision-panel">
              <div className="section-head">
                <div>
                  <h2>人工修订</h2>
                  <p>{ocrResult?.human_corrected ? "已保存人工修订" : "尚未人工修订"}</p>
                </div>
                <button className="primary-button small" onClick={saveOcr} disabled={!ocrResult || busy}>
                  {busy ? <Loader2 className="spin" size={16} /> : <Save size={16} />}
                  保存并分析
                </button>
              </div>
              <textarea
                className="revision-editor"
                value={correctedText}
                onChange={(event) => setCorrectedText(event.target.value)}
                placeholder="在此录入人工修订文本"
              />
              <QualityBar ocr={ocrResult} />
              <div className="ocr-reference">
                <div className="section-head compact">
                  <div>
                    <h2>OCR 原文对照</h2>
                    <p>{ocrResult ? `${ocrResult.engine ?? "OCR"} · ${ocrResult.status}` : "未加载"}</p>
                  </div>
                </div>
                <pre>{ocrResult?.raw_text || "暂无 OCR 文本"}</pre>
              </div>
            </div>
          </section>
        ) : activeTab === "multi" ? (
          <section className="content-wide">
            <div className="section-head">
              <div>
                <h2>跨文档知识图谱</h2>
                <p>{selectedForMulti.length} 篇文书参与分析</p>
              </div>
              <button className="secondary-button" onClick={runMultiDocumentGraph} disabled={busy}>
                <Network size={17} /> 重新生成
              </button>
            </div>
            {multiGraph ? (
              <GraphView option={multiGraph.content} />
            ) : (
              <EmptyState title="暂无跨文档图谱" text="从左侧选择至少两篇文书后生成。" />
            )}
          </section>
        ) : activeTab === "graph" ? (
          <section className="content-wide">
            <div className="section-head">
              <div>
                <h2>单文书知识图谱</h2>
                  <p>{selectedImage ? displayTitle(selectedImage.title) : "未选择文书"}</p>
              </div>
              <button className="icon-button" onClick={() => graph && setGraph({ ...graph })} title="重置图谱视图">
                <RefreshCw size={17} />
              </button>
            </div>
            {graph ? <GraphView option={graph.content} /> : <EmptyState title="暂无图谱" text="完成结构化分析后将显示关系图。" />}
          </section>
        ) : (
          <section className="document-grid">
            <div className="image-panel">
              <div className="section-head">
                <div>
                  <h2>{selectedImage ? displayTitle(selectedImage.title) : "文书原图"}</h2>
                  <p>{selectedImage?.filename ?? ""}</p>
                </div>
              </div>
              {imageUrl ? (
                  <DocumentViewer
                    imageUrl={imageUrl}
                    imageTitle={selectedImage ? displayTitle(selectedImage.title) : "文书图片"}
                  />
              ) : (
                <EmptyState title="未加载图片" text="从左侧选择一篇文书。" />
              )}
            </div>

            <div className="ocr-panel">
              <div className="section-head">
                <div>
                  <h2>识别结果</h2>
                  <p>{ocrResult ? `${ocrResult.engine ?? "OCR"} · ${ocrResult.status}` : "未加载"}</p>
                </div>
              </div>
              <pre className="ocr-readonly">{ocrResult?.raw_text || "暂无 OCR 文本"}</pre>
              <QualityBar ocr={ocrResult} />
            </div>

            <div className="analysis-panel">
              <div className="section-head">
                <div>
                  <h2>分析结果</h2>
                  <p>{structured ? structured.status : "未加载"}</p>
                </div>
                <Database size={18} />
              </div>
              <StructuredFields content={structuredContent} />
            </div>
          </section>
        )}
      </section>

      {(status || error) && (
        <div className={`toast ${error ? "error" : ""}`}>
          {busy && !error ? <Loader2 className="spin" size={16} /> : null}
          {error || status}
        </div>
      )}
    </main>
  );
}

function StructuredFields({ content }: { content: Record<string, unknown> }) {
  const hasData = Object.keys(content).length > 0;
  if (!hasData) {
    return <EmptyState title="暂无结构化结果" text="保存 OCR 后会自动刷新。" />;
  }
  return (
    <div className="field-list">
      {CORE_FIELDS.map(([key, label]) => (
        <div key={key} className="field-row">
          <span>{label}</span>
          <strong>{displayValue(content[key])}</strong>
        </div>
      ))}
    </div>
  );
}

function QualityBar({ ocr }: { ocr: OcrResult | null }) {
  const confidence = Math.round((ocr?.confidence ?? 0) * 100);
  const coverage = Math.round((ocr?.coverage ?? 0) * 100);
  const corrected = Boolean(ocr?.human_corrected);
  return (
    <div className="quality-grid">
      <div>
        <span>OCR 置信度</span>
        <strong>{confidence}%</strong>
      </div>
      <div>
        <span>OCR 覆盖率</span>
        <strong>{coverage}%</strong>
      </div>
      <div>
        <span>人工修订</span>
        <strong>{corrected ? "是" : "否"}</strong>
      </div>
    </div>
  );
}

function StatisticsView({
  statistics,
  onRefresh,
}: {
  statistics: StatisticsData | null;
  onRefresh: () => Promise<void>;
}) {
  if (!statistics) {
    return <EmptyState title="暂无统计数据" text="完成分析后将显示统计看板。" />;
  }
  const option = {
    tooltip: {},
    grid: { left: 40, right: 20, top: 30, bottom: 40 },
    xAxis: { type: "category", data: statistics.time_distribution.map((item) => item.year) },
    yAxis: { type: "value" },
    series: [
      {
        type: "line",
        smooth: true,
        areaStyle: {},
        data: statistics.time_distribution.map((item) => item.count),
        color: "#239b82",
      },
    ],
  };
  return (
    <>
      <div className="section-head">
        <div>
          <h2>统计看板</h2>
          <p>
            {statistics.total_images} 篇文书 · {statistics.total_analyzed} 篇已分析
          </p>
        </div>
        <button className="icon-button" onClick={onRefresh} title="刷新统计">
          <RefreshCw size={17} />
        </button>
      </div>
      <div className="stats-grid">
        <Metric label="文书总量" value={statistics.total_images} />
        <Metric label="已分析" value={statistics.total_analyzed} />
        <Metric label="年代跨度" value={statistics.time_range.span ?? 0} />
        <Metric label="地点数量" value={statistics.location_distribution.length} />
      </div>
      <div className="stats-layout">
        <div className="chart-panel">
          <GraphView option={option} />
        </div>
        <RankList title="交易地点 Top" items={statistics.location_distribution} />
        <RankList title="高频人物 Top" items={statistics.top_people} />
      </div>
    </>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function RankList({ title, items }: { title: string; items: Array<{ name: string; count: number }> }) {
  return (
    <div className="rank-list">
      <h3>{title}</h3>
      {items.map((item) => (
        <div key={item.name} className="rank-row">
          <span>{item.name}</span>
          <strong>{item.count}</strong>
        </div>
      ))}
    </div>
  );
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <FileText size={28} />
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}
