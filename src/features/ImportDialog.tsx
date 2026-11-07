import { useRef, useState } from "react";
import { FileJson, Upload, ArrowRight } from "lucide-react";
import { Button, Modal, Notice } from "../components/ui";
import { useStore } from "../store";

export function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { importConversations } = useStore();
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  async function readFile(file?: File) {
    if (!file || busy) return;
    setError("");
    if (file.size > 5 * 1024 * 1024) {
      setError("Файл должен быть меньше 5 МБ");
      return;
    }
    setBusy(true);
    try {
      setText(await file.text());
      setFilename(file.name);
    } catch {
      setError("Не удалось прочитать файл");
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (busy) return;
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
      setBusy(false);
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
          {busy ? "Загружаем…" : "Загрузить"}
          <ArrowRight size={16} />
        </Button>
      </div>
    </Modal>
  );
}
