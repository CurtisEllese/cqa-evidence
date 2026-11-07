import {
  ArrowRight,
  ArrowUpRight,
  CheckCheck,
  ClipboardCheck,
  Headphones,
  Languages,
  MessageSquare,
  Sparkles,
} from "lucide-react";
import { useStore } from "../store";
import { Button, PageHeading, TextLink } from "../components/ui";
import { ConversationTable } from "./Conversations";
import { DatasetSummary } from "./DatasetSummary";

export function Dashboard({
  onOpen,
  onNavigate,
  onImport,
}: {
  onOpen: (id: string) => void;
  onNavigate: (page: string) => void;
  onImport: () => void;
}) {
  const { data, evaluationAvailable } = useStore();
  const evaluated = data.conversations.filter(
    (item) => item.evaluation?.status === "completed",
  );
  const scores = evaluated
    .map((item) => ({
      id: item.id,
      score: item.scores?.final.total ?? null,
    }))
    .filter((item) => item.score !== null);
  const average = scores.length
    ? Math.round(
        (scores.reduce((sum, item) => sum + item.score!, 0) / scores.length) *
          10,
      ) / 10
    : 0;
  const pending = data.appeals.filter((a) => a.status === "pending").length;
  const chartPoints = scores.slice(0, 12).reverse();
  const coordinates = chartPoints.map((item, i) => ({
    x: 40 + i * (650 / Math.max(chartPoints.length - 1, 1)),
    y: 156 - item.score! * 1.28,
    ...item,
  }));
  const line = coordinates
    .map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`)
    .join(" ");
  const languages = ["RU", "KZ", "EN"] as const;
  return (
    <>
      <PageHeading
        eyebrow="КОНТРОЛЬ КАЧЕСТВА"
        title="Обзор качества"
        description="Результаты оценок и апелляции команды."
        actions={
          <Button variant="primary" onClick={onImport}>
            <MessageSquare size={17} />
            Загрузить диалоги
          </Button>
        }
      />
      <div className="dataset-banner">
        <span className="dataset-indicator" aria-hidden="true" />
        <DatasetSummary />
        <button onClick={onImport}>
          Загрузить диалоги <ArrowUpRight size={15} />
        </button>
      </div>
      <div className="stats-grid">
        <Stat
          label="Всего диалогов"
          value={String(data.conversations.length)}
          icon={<MessageSquare size={19} />}
          footer={`${data.conversations.filter((c) => c.channel === "agent chat").length} чатов · ${data.conversations.filter((c) => c.channel === "voice call").length} звонка`}
          accent="violet"
        />
        <Stat
          label="Средняя оценка"
          value={scores.length ? String(average).replace(".", ",") : "—"}
          suffix="/ 100"
          icon={<Sparkles size={19} />}
          footer="По оценённым диалогам"
          accent="green"
          bars={scores.map((x) => x.score!)}
        />
        <Stat
          label="Диалогов оценено"
          value={String(evaluated.length)}
          suffix={`из ${data.conversations.length}`}
          icon={<CheckCheck size={19} />}
          footer={`${data.conversations.filter((c) => !c.evaluation).length} ещё не оценены`}
          accent="blue"
        />
        <Stat
          label="На рассмотрении"
          value={String(pending)}
          icon={<ClipboardCheck size={19} />}
          footer="Апелляции по критериям"
          accent="amber"
          action={() => onNavigate("appeals")}
        />
      </div>
      <div className="overview-middle">
        <section className="panel chart-panel">
          <div className="panel-heading">
            <div>
              <h2>Качество диалогов</h2>
              <p>Итоговые баллы с учётом решений по апелляциям</p>
            </div>
            <span className="chart-legend">
              <i />
              Оценка
            </span>
          </div>
          {scores.length === 0 ? (
            <div className="evaluation-empty">
              <Sparkles size={28} />
              <h3>Оценок пока нет</h3>
              <p>
                {evaluationAvailable
                  ? "Откройте диалог и запустите оценку. Здесь появятся проверенные результаты."
                  : "Диалоги доступны для просмотра. Автоматическая оценка временно недоступна."}
              </p>
              {evaluationAvailable && (
                <Button onClick={() => onNavigate("conversations")}>
                  Перейти к оценке диалога <ArrowRight size={16} />
                </Button>
              )}
            </div>
          ) : (
            <div className="quality-chart">
              <svg
                viewBox="0 0 730 192"
                role="img"
                aria-label={`Оценки ${chartPoints.length} диалогов; средний балл ${average}`}
              >
                <defs>
                  <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#00b323" stopOpacity=".18" />
                    <stop offset="100%" stopColor="#00b323" stopOpacity="0" />
                  </linearGradient>
                </defs>
                {[0, 25, 50, 75, 100].map((n) => (
                  <g key={n}>
                    <line
                      x1="40"
                      x2="700"
                      y1={156 - n * 1.28}
                      y2={156 - n * 1.28}
                      stroke="#edf0ee"
                      strokeDasharray="4 5"
                    />
                    <text x="2" y={160 - n * 1.28} className="chart-label">
                      {n}
                    </text>
                  </g>
                ))}
                {coordinates.length > 0 && (
                  <>
                    <path
                      d={`${line} L${coordinates[coordinates.length - 1].x},156 L40,156 Z`}
                      fill="url(#chart-fill)"
                    />
                    <path
                      d={line}
                      fill="none"
                      stroke="#00aa2b"
                      strokeWidth="2.5"
                      strokeLinejoin="round"
                      strokeLinecap="round"
                    />
                    {coordinates.map((p, i) => (
                      <g key={p.id}>
                        <circle
                          cx={p.x}
                          cy={p.y}
                          r="4"
                          fill="white"
                          stroke="#00aa2b"
                          strokeWidth="2"
                        />
                        <text
                          x={p.x}
                          y="184"
                          textAnchor="middle"
                          className="chart-label"
                        >
                          {String(i + 1).padStart(2, "0")}
                        </text>
                        <title>
                          {p.id}: {p.score} / 100
                        </title>
                      </g>
                    ))}
                  </>
                )}
              </svg>
            </div>
          )}
          <div className="chart-caption">
            <span>Последние {chartPoints.length} оценённых диалогов</span>
            <TextLink onClick={() => onNavigate("conversations")}>
              Все результаты
            </TextLink>
          </div>
        </section>
        <section className="standard-card">
          <div className="standard-orbit orbit-one" />
          <div className="standard-orbit orbit-two" />
          <div className="standard-label">
            <span className="standard-icon">
              <ClipboardCheck size={19} />
            </span>
            <span>ЕДИНЫЙ СТАНДАРТ</span>
          </div>
          <h2>
            Хороший сервис.
            <br />
            <span>В каждой детали.</span>
          </h2>
          <p>
            {data.checklist.criteria.length} критериев, которые помогают
            <br />
            увидеть полную картину диалога.
          </p>
          <div className="standard-footer">
            <span>Чек-лист · версия {data.checklist.version}</span>
            <button
              onClick={() => onNavigate("checklist")}
              aria-label="Открыть чек-лист"
            >
              <ArrowUpRight size={23} />
            </button>
          </div>
        </section>
      </div>
      <div className="overview-bottom">
        <ConversationTable onOpen={onOpen} compact />
        <section className="panel coverage-panel">
          <div className="panel-heading">
            <div>
              <h2>Без языковых границ</h2>
              <p>Диалоги на трёх языках</p>
            </div>
            <Languages size={20} />
          </div>
          <div className="language-breakdown">
            {languages.map((lang, i) => {
              const count = data.conversations.filter(
                (item) => item.language === lang,
              ).length;
              return (
                <div className="language-stat" key={lang}>
                  <div>
                    <span className={`language-marker language-marker-${i}`}>
                      {lang}
                    </span>
                    <span>{["Русский", "Қазақша", "English"][i]}</span>
                    <strong>{count}</strong>
                  </div>
                  <div className="progress-track">
                    <span
                      style={{
                        width: `${data.conversations.length ? (count / data.conversations.length) * 100 : 0}%`,
                      }}
                      className={`language-fill-${i}`}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="coverage-footer">
            <Headphones size={19} />
            <p>
              Текст и звонки —<br />
              <strong>в одном пространстве</strong>
            </p>
          </div>
        </section>
      </div>
      <div className="page-footnote">
        <span>Freedom Quality</span>
        <span>Внимание к диалогу. Уверенность в качестве.</span>
      </div>
    </>
  );
}
function Stat({
  label,
  value,
  suffix,
  icon,
  footer,
  accent,
  bars,
  action,
}: {
  label: string;
  value: string;
  suffix?: string;
  icon: React.ReactNode;
  footer: string;
  accent: string;
  bars?: number[];
  action?: () => void;
}) {
  return (
    <section className="stat-card">
      <div className="stat-top">
        <span>{label}</span>
        <div className={`stat-icon stat-icon-${accent}`}>{icon}</div>
      </div>
      <div className="stat-number">
        {value}
        <span>{suffix}</span>
        {bars && (
          <div className="mini-bars" aria-hidden="true">
            {bars.slice(0, 8).map((v, i) => (
              <i
                key={i}
                style={{
                  height: `${Math.max(5, v * 0.35)}px`,
                  opacity: 0.35 + i * 0.08,
                }}
              />
            ))}
          </div>
        )}
      </div>
      <div className="stat-footer">
        <span>{footer}</span>
        {action && (
          <button aria-label="Открыть апелляции" onClick={action}>
            <ArrowRight size={17} />
          </button>
        )}
      </div>
    </section>
  );
}
