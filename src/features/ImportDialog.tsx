import { useRef, useState } from "react";
import { FileJson, Upload, ArrowRight } from "lucide-react";
import { Button, Modal, Notice } from "../components/ui";
import { useStore } from "../store";
import { DatasetSummary } from "./DatasetSummary";

export function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { importConversations, refreshDataset, loading, loadError } =
    useStore();
  const [groupId, setGroupId] = useState("DEV");
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [importBusy, setBusy] = useState(false);
  const [groupAttempted, setGroupAttempted] = useState(false);
  const [groupLoaded, setGroupLoaded] = useState(false);
  const busy = importBusy || loading;
  const operationPending = useRef(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  async function readFile(file?: File) {
    if (!file || busy || operationPending.current) return;
    setError("");
    if (file.size > 5 * 1024 * 1024) {
      setError("Размер файла не должен превышать 5 МБ");
      return;
    }
    operationPending.current = true;
    setBusy(true);
    try {
      setText(await file.text());
      setFilename(file.name);
    } catch {
      setError("Не удалось прочитать файл");
    } finally {
      operationPending.current = false;
      setBusy(false);
    }
  }
  async function submit() {
    if (busy || operationPending.current) return;
    operationPending.current = true;
    setBusy(true);
    setError("");
    try {
      const imported = await importConversations(text);
      if (!imported) {
        setError("Все диалоги уже загружены. Выберите другой набор.");
        return;
      }
      onImported();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Проверьте формат файла");
    } finally {
      operationPending.current = false;
      setBusy(false);
    }
  }
  async function loadGroup() {
    if (busy || operationPending.current || !groupId.trim()) return;
    operationPending.current = true;
    setGroupAttempted(true);
    setGroupLoaded(false);
    try {
      if (await refreshDataset(groupId)) {
        setGroupLoaded(true);
        onImported();
      }
    } finally {
      operationPending.current = false;
    }
  }
  return (
    <Modal
      title="Загрузить диалоги"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="modal-intro">
        Добавьте транскрипты из набора организаторов. Исходные реплики и метки
        времени сохранятся.
      </p>
      <section className="dataset-import" aria-label="Загрузка по группе">
        <DatasetSummary />
        <label className="field-label" htmlFor="dataset-group">
          Группа набора
        </label>
        <form
          className="dataset-import-controls"
          aria-busy={loading}
          onSubmit={(event) => {
            event.preventDefault();
            void loadGroup();
          }}
        >
          <input
            id="dataset-group"
            value={groupId}
            onChange={(e) => {
              setGroupId(e.target.value);
              setGroupAttempted(false);
              setGroupLoaded(false);
            }}
            disabled={busy}
            maxLength={512}
            required
            autoComplete="off"
            spellCheck={false}
            aria-describedby="dataset-group-hint"
          />
          <Button
            type="submit"
            variant="primary"
            disabled={busy || !groupId.trim()}
          >
            <Upload size={16} aria-hidden="true" />
            {loading ? "Загружаем набор…" : "Загрузить набор"}
          </Button>
        </form>
        <p id="dataset-group-hint" className="dataset-import-hint">
          Введите только идентификатор группы, без ссылки организатора.
        </p>
        {groupAttempted && loading && (
          <p role="status">Загружаем группу «{groupId.trim()}»…</p>
        )}
        {groupAttempted && loadError && (
          <p className="form-error" role="alert">
            {loadError} Текущие диалоги и оценки сохранены.
          </p>
        )}
        {groupLoaded && <p role="status">Набор загружен.</p>}
      </section>
      <h3 className="manual-import-title">Ручной импорт JSON</h3>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void readFile(e.dataTransfer.files[0]);
        }}
      >
        <FileJson size={32} />
        <h3>{filename || "Перетащите JSON-файл сюда"}</h3>
        <p>Ответ API организаторов или массив записей · до 5 МБ</p>
        <Button disabled={busy} onClick={() => fileInput.current?.click()}>
          <Upload size={16} />
          Выбрать файл
        </Button>
        <input
          ref={fileInput}
          type="file"
          disabled={busy}
          accept=".json,application/json"
          className="sr-only"
          aria-label="JSON-файл диалогов"
          onChange={(e) => void readFile(e.target.files?.[0])}
        />
      </div>
      <label className="field-label" htmlFor="json-input">
        Или вставьте JSON
      </label>
      <textarea
        id="json-input"
        disabled={busy}
        className="code-input"
        placeholder={'{ "isSuccess": true, "data": { "records": [...] } }'}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setFilename("");
          setError("");
        }}
        rows={5}
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <Notice>
        Сервер проверит файл и сохранит диалоги. Для автоматической оценки
        требуется подключённый сервис ИИ.
      </Notice>
      <div className="modal-actions">
        <Button disabled={busy} onClick={onClose}>
          Отмена
        </Button>
        <Button
          variant="primary"
          disabled={!text.trim() || busy}
          onClick={submit}
        >
          {importBusy ? "Загружаем…" : "Загрузить JSON"}
          <ArrowRight size={16} />
        </Button>
      </div>
    </Modal>
  );
}
